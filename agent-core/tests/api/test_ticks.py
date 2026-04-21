"""
Testes do endpoint POST /v1/ticks.

Estratégia: FastAPI TestClient + monkeypatch em `routes.db.*` (async) e
`routes.github_pm.*` (sync) — não sobe Postgres nem chama GitHub. Também
substitui `threading.Thread` por um fake que só grava os kwargs recebidos,
pra verificar que o run foi disparado com o contexto correto da issue sem
efetivamente executar o grafo.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient


PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")
CLIENT_ID = UUID("00000000-0000-0000-0000-000000000002")
RUN_ID = UUID("000000aa-0000-0000-0000-000000000000")
REPO = "https://github.com/wrprates/demo-repo"


def _project(**over) -> dict:
    base = {
        "id": PROJECT_ID,
        "client_id": CLIENT_ID,
        "name": "demo",
        "description": "desc",
        "workflow_type": "full_ml",
    }
    base.update(over)
    return base


def _client_row(**over) -> dict:
    base = {"id": CLIENT_ID, "name": "acme", "github_repo": REPO}
    base.update(over)
    return base


def _run_row(**over) -> dict:
    base = {
        "id": RUN_ID,
        "project_id": PROJECT_ID,
        "status": "active",
        "started_at": datetime(2026, 4, 18, 12, 0),
        "finished_at": None,
        "issue_number": None,
        "issue_kind": None,
        "issue_title": None,
    }
    base.update(over)
    return base


@pytest.fixture
def app_client() -> TestClient:
    from src.main import app

    return TestClient(app)


@pytest.fixture
def mock_tick_deps(monkeypatch):
    """
    Patcha db.*, github_pm.* e threading.Thread dentro do módulo de rotas.

    Retorna um dict com dois compartimentos:
    - `state`: valores que cada mock retorna (ajustáveis por teste).
    - `spawned`: lista de kwargs que seriam passados pro thread de run.
    """
    from src.api import routes

    spawned: list[dict] = []

    class FakeThread:
        def __init__(self, *, target=None, kwargs=None, daemon=None, **_):
            spawned.append({"target": target, "kwargs": kwargs or {}})

        def start(self) -> None:
            pass

    monkeypatch.setattr(routes.threading, "Thread", FakeThread)

    state: dict = {
        "project": None,
        "client": None,
        "issues": [],
        "claim_ok": True,
    }

    async def fake_fetch_project(_pid):
        return state["project"]

    async def fake_fetch_client(_cid):
        return state["client"]

    async def fake_insert_run(pid, *, issue_number=None, issue_kind=None, issue_title=None):
        return _run_row(
            project_id=pid,
            issue_number=issue_number,
            issue_kind=issue_kind,
            issue_title=issue_title,
        )

    monkeypatch.setattr(routes.db, "fetch_project", fake_fetch_project)
    monkeypatch.setattr(routes.db, "fetch_client", fake_fetch_client)
    monkeypatch.setattr(routes.db, "insert_run", fake_insert_run)

    def fake_list(_repo):
        return state["issues"]

    _KINDS = {"cortex:quality", "cortex:eda", "cortex:modeling", "cortex:review"}

    def fake_kind(issue):
        for lb in sorted(
            (lb.get("name", "") for lb in issue.get("labels", [])),
        ):
            if lb in _KINDS:
                return lb.split(":", 1)[1]
        return None

    def fake_claim(_repo, _num):
        return state["claim_ok"]

    monkeypatch.setattr(routes.github_pm, "list_claimable_issues", fake_list)
    monkeypatch.setattr(routes.github_pm, "get_issue_kind", fake_kind)
    monkeypatch.setattr(routes.github_pm, "claim_issue", fake_claim)

    return {"state": state, "spawned": spawned}


class TestTickStarted:
    def test_started_dispatches_run_with_issue_context(self, app_client, mock_tick_deps):
        s = mock_tick_deps["state"]
        s["project"] = _project()
        s["client"] = _client_row()
        s["issues"] = [
            {
                "number": 42,
                "title": "investigar nulls em churn",
                "labels": [{"name": "cortex:quality"}],
                "html_url": "https://github.com/wrprates/demo-repo/issues/42",
                "updated_at": "2026-04-18T10:00:00Z",
            }
        ]
        s["claim_ok"] = True

        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "started"
        assert body["issue_number"] == 42
        assert body["issue_kind"] == "quality"
        assert body["issue_title"] == "investigar nulls em churn"
        assert body["run_id"] is not None

        # Thread de run recebeu o contexto da issue — sem isso, node_report
        # não consegue fechar o ciclo da issue no GitHub.
        assert len(mock_tick_deps["spawned"]) == 1
        kwargs = mock_tick_deps["spawned"][0]["kwargs"]
        assert kwargs["issue_number"] == 42
        assert kwargs["issue_kind"] == "quality"
        assert kwargs["issue_title"] == "investigar nulls em churn"
        assert kwargs["github_repo"] == REPO
        assert kwargs["client_id"] == str(CLIENT_ID)

    def test_datasets_propagated_to_run_thread(self, app_client, mock_tick_deps):
        s = mock_tick_deps["state"]
        s["project"] = _project()
        s["client"] = _client_row()
        s["issues"] = [
            {
                "number": 1,
                "title": "t",
                "labels": [{"name": "cortex:eda"}],
                "html_url": "u",
                "updated_at": "2026-04-18T10:00:00Z",
            }
        ]

        r = app_client.post(
            "/v1/ticks",
            json={"project_id": str(PROJECT_ID), "datasets": ["s3://bucket/churn.csv"]},
        )

        assert r.status_code == 200
        assert r.json()["status"] == "started"
        assert mock_tick_deps["spawned"][0]["kwargs"]["datasets"] == [
            "s3://bucket/churn.csv"
        ]


class TestTickIdle:
    def test_idle_when_backlog_empty(self, app_client, mock_tick_deps):
        s = mock_tick_deps["state"]
        s["project"] = _project()
        s["client"] = _client_row()
        s["issues"] = []

        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "idle"
        assert body["reason"] == "no claimable issues"
        assert body["run_id"] is None
        assert mock_tick_deps["spawned"] == []

    def test_idle_when_issue_has_no_kind_label(self, app_client, mock_tick_deps):
        """
        Guarda defensiva: `list_claimable_issues` já filtra por kind, mas se
        evoluir e retornar algo sem kind, o endpoint precisa devolver idle
        ao invés de crashar ou claimar errado.
        """
        s = mock_tick_deps["state"]
        s["project"] = _project()
        s["client"] = _client_row()
        s["issues"] = [
            {"number": 1, "title": "t", "labels": [{"name": "bug"}]},
        ]

        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})

        assert r.status_code == 200
        assert r.json()["status"] == "idle"
        assert mock_tick_deps["spawned"] == []


class TestTickSkipped:
    def test_skipped_when_claim_race_lost(self, app_client, mock_tick_deps):
        s = mock_tick_deps["state"]
        s["project"] = _project()
        s["client"] = _client_row()
        s["issues"] = [
            {
                "number": 7,
                "title": "EDA churn",
                "labels": [{"name": "cortex:eda"}],
                "html_url": "u",
                "updated_at": "2026-04-18T10:00:00Z",
            }
        ]
        s["claim_ok"] = False  # outro agente pegou antes

        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "skipped"
        assert body["issue_number"] == 7
        assert body["issue_kind"] == "eda"
        assert body["issue_title"] == "EDA churn"
        assert "#7" in body["reason"]
        # Não pode criar run se perdeu race — senão fica órfão no banco.
        assert mock_tick_deps["spawned"] == []


class TestTickErrors:
    def test_404_when_project_missing(self, app_client, mock_tick_deps):
        mock_tick_deps["state"]["project"] = None

        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})

        assert r.status_code == 404
        assert "project" in r.json()["detail"].lower()

    def test_409_when_project_has_no_client(self, app_client, mock_tick_deps):
        mock_tick_deps["state"]["project"] = _project(client_id=None)

        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})

        assert r.status_code == 409
        assert "client" in r.json()["detail"].lower()
        assert mock_tick_deps["spawned"] == []

    def test_409_when_client_has_no_github_repo(self, app_client, mock_tick_deps):
        s = mock_tick_deps["state"]
        s["project"] = _project()
        s["client"] = _client_row(github_repo=None)

        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})

        assert r.status_code == 409
        assert "github_repo" in r.json()["detail"].lower()
        assert mock_tick_deps["spawned"] == []

    def test_422_when_project_id_is_not_uuid(self, app_client, mock_tick_deps):
        r = app_client.post("/v1/ticks", json={"project_id": "not-a-uuid"})
        assert r.status_code == 422


class TestKindDrivesWorkflow:
    """
    Sprint Lâmina 2/4 (#29): issue_kind vira ditador do workflow_type.
    O valor salvo em `project.workflow_type` é ignorado quando a issue tem
    uma label kind reconhecida — só serve como fallback defensivo.
    """

    def _base_state(self, deps, kind: str, project_workflow: str = "full_ml"):
        s = deps["state"]
        s["project"] = _project(workflow_type=project_workflow)
        s["client"] = _client_row()
        s["issues"] = [
            {
                "number": 99,
                "title": f"issue {kind}",
                "labels": [{"name": f"cortex:{kind}"}],
                "html_url": "u",
                "updated_at": "2026-04-21T10:00:00Z",
            }
        ]
        s["claim_ok"] = True

    def test_quality_kind_maps_to_data_quality(self, app_client, mock_tick_deps):
        self._base_state(mock_tick_deps, "quality", project_workflow="full_ml")
        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})
        assert r.status_code == 200
        kwargs = mock_tick_deps["spawned"][0]["kwargs"]
        assert kwargs["workflow_type"] == "data_quality"

    def test_eda_kind_maps_to_eda_hypothesis(self, app_client, mock_tick_deps):
        self._base_state(mock_tick_deps, "eda", project_workflow="data_quality")
        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})
        assert r.status_code == 200
        kwargs = mock_tick_deps["spawned"][0]["kwargs"]
        assert kwargs["workflow_type"] == "eda_hypothesis"

    def test_modeling_kind_maps_to_full_ml(self, app_client, mock_tick_deps):
        self._base_state(mock_tick_deps, "modeling", project_workflow="data_quality")
        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})
        assert r.status_code == 200
        kwargs = mock_tick_deps["spawned"][0]["kwargs"]
        assert kwargs["workflow_type"] == "full_ml"

    def test_review_kind_maps_to_full_ml_for_now(self, app_client, mock_tick_deps):
        # TODO: sprint futuro introduzir workflow dedicado review_only.
        self._base_state(mock_tick_deps, "review", project_workflow="data_quality")
        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})
        assert r.status_code == 200
        kwargs = mock_tick_deps["spawned"][0]["kwargs"]
        assert kwargs["workflow_type"] == "full_ml"

    def test_kind_overrides_project_workflow(self, app_client, mock_tick_deps):
        """
        project.workflow_type='full_ml' mas issue é quality → roda só data_quality.
        É a garantia central do sprint: kind dita, projeto é fallback.
        """
        self._base_state(mock_tick_deps, "quality", project_workflow="full_ml")
        r = app_client.post("/v1/ticks", json={"project_id": str(PROJECT_ID)})
        assert r.status_code == 200
        kwargs = mock_tick_deps["spawned"][0]["kwargs"]
        assert kwargs["workflow_type"] == "data_quality"
