"""
Testes do POST /v1/decisions após sprint-lamina 3/4 (#30).

O endpoint foi mantido por compatibilidade por 1 sprint, mas virou no-op:
- Ainda grava a decisão em DB (audit), pra não perder registros legacy.
- NÃO dispara mais `runs_service.resume_run` — o grafo não pausa mais.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient


RUN_ID = UUID("000000aa-0000-0000-0000-000000000000")
DECISION_ID = UUID("000000bb-0000-0000-0000-000000000000")


@pytest.fixture
def app_client() -> TestClient:
    from src.main import app

    return TestClient(app)


@pytest.fixture
def mock_decision_deps(monkeypatch):
    from src.api import routes

    calls = {"insert_human_decision": 0, "resume_run_thread_spawned": False}

    class FakeThread:
        def __init__(self, *, target=None, kwargs=None, daemon=None, **_):
            # Se o endpoint ainda spawnasse thread de resume_run, detectaríamos aqui.
            if target is routes.runs_service.resume_run:
                calls["resume_run_thread_spawned"] = True

        def start(self) -> None:
            pass

    monkeypatch.setattr(routes.threading, "Thread", FakeThread)

    async def fake_insert_human_decision(run_id, task_id, decision, comments):
        calls["insert_human_decision"] += 1
        return {
            "id": DECISION_ID,
            "run_id": run_id,
            "task_id": task_id,
            "decision": decision,
            "comments": comments,
            "decided_at": datetime(2026, 4, 21, 12, 0),
        }

    monkeypatch.setattr(routes.db, "insert_human_decision", fake_insert_human_decision)
    return calls


class TestDecisionsEndpointDeprecated:
    def test_still_returns_201_for_audit(self, app_client, mock_decision_deps):
        r = app_client.post(
            "/v1/decisions",
            json={"run_id": str(RUN_ID), "decision": "approved", "comments": "ok"},
        )
        assert r.status_code == 201
        assert r.json()["decision"] == "approved"

    def test_does_not_spawn_resume_run(self, app_client, mock_decision_deps):
        app_client.post(
            "/v1/decisions",
            json={"run_id": str(RUN_ID), "decision": "approved", "comments": "ok"},
        )
        # Grafo roda até terminal em start_run; resume_run é dead code aqui.
        assert mock_decision_deps["resume_run_thread_spawned"] is False

    def test_still_persists_for_audit(self, app_client, mock_decision_deps):
        app_client.post(
            "/v1/decisions",
            json={"run_id": str(RUN_ID), "decision": "rejected", "comments": "skip"},
        )
        assert mock_decision_deps["insert_human_decision"] == 1

    def test_rejects_invalid_decision(self, app_client, mock_decision_deps):
        r = app_client.post(
            "/v1/decisions",
            json={"run_id": str(RUN_ID), "decision": "maybe", "comments": None},
        )
        assert r.status_code == 400
