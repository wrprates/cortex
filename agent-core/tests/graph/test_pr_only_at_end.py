"""
Testes do sprint-lamina 4/4 (#31): PR só abre no fim do run.

Contratos defendidos:
- `_commit_stage_to_repo` empurra código pro branch mas NÃO abre nem atualiza PR.
- `node_report` abre o PR (ready-for-review, não draft) só se push deu certo.
- Se push ou create_pr falha, `_close_issue_driven_cycle` é chamado com
  push_ok=False (libera claim, deixa issue aberta pra re-tick).
- `start_run` com exceção no meio libera claim da issue-driver.
"""
from __future__ import annotations

from uuid import UUID

import pytest


REPO = "https://github.com/wrprates/demo-repo"


@pytest.fixture
def gh_mocks(monkeypatch):
    """Captura chamadas a github_manager + github_pm sem tocar rede."""
    calls = {
        "push_to_branch": [],
        "create_pr": [],
        "update_pr_body": [],
        "comment_issue": [],
        "release_claim": [],
        "close_issue": [],
    }

    push_result = {"ok": True}

    def fake_push(repo, branch, files, commit_message):
        calls["push_to_branch"].append({"branch": branch, "files": list(files.keys())})
        return push_result

    def fake_create_pr(repo, *, head, base, title, body, draft):
        calls["create_pr"].append({"head": head, "draft": draft, "title": title})
        return {"number": 99, "url": "u", "html_url": "h"}

    def fake_update_body(repo, number, body):
        calls["update_pr_body"].append({"number": number})
        return True

    def fake_comment_issue(repo, num, body):
        calls["comment_issue"].append({"num": num})
        return True

    def fake_release(repo, num):
        calls["release_claim"].append(num)
        return True

    def fake_close(repo, num, reason="completed"):
        calls["close_issue"].append((num, reason))
        return True

    from src.storage import github_manager, github_pm
    monkeypatch.setattr(github_manager, "push_to_branch", fake_push)
    monkeypatch.setattr(github_pm, "create_pr", fake_create_pr)
    monkeypatch.setattr(github_pm, "update_pr_body", fake_update_body)
    monkeypatch.setattr(github_pm, "comment_issue", fake_comment_issue)
    monkeypatch.setattr(github_pm, "release_claim", fake_release)
    monkeypatch.setattr(github_pm, "close_issue", fake_close)
    return {"calls": calls, "push_result": push_result}


class TestCommitStageDoesNotOpenPR:
    def test_stage_commit_pushes_but_does_not_create_pr(self, gh_mocks):
        from src.graph import nodes

        state = {
            "github_repo": REPO,
            "run_id": "abc12345-0000-0000-0000-000000000000",
            "workflow_type": "full_ml",
            "stages_committed": [],
        }
        result = {
            "success": True,
            "code": "print('hi')",
            "summary": {"dataset": {"rows_original": 10, "rows_final": 10, "cols_dropped": 0}},
        }

        updates = nodes._commit_stage_to_repo(state, "quality", result)

        assert updates is not None
        assert "quality" in updates.get("stages_committed", [])
        # O ponto: nenhum PR é aberto/atualizado no commit de stage.
        assert gh_mocks["calls"]["create_pr"] == []
        assert gh_mocks["calls"]["update_pr_body"] == []
        # Mas o push aconteceu.
        assert len(gh_mocks["calls"]["push_to_branch"]) == 1
        assert gh_mocks["calls"]["push_to_branch"][0]["branch"] == "run/abc12345"


class TestNodeReportOpensPRAtEnd:
    def _base_state(self) -> dict:
        return {
            "github_repo": REPO,
            "run_id": "abc12345-0000-0000-0000-000000000000",
            "workflow_type": "full_ml",
            "issue_number": 42,
            "stages_committed": ["quality", "hypothesis", "ml"],
            "quality_results": {"summary": {}},
            "hypothesis_results": {},
            "model_results": {"metrics": {}},
            "plan": {"phases": []},
        }

    def _patch_orchestrator(self, monkeypatch, report):
        from src.graph import nodes

        monkeypatch.setattr(
            nodes, "run_orchestrator", lambda *_a, **_kw: report,
        )

    def test_push_ok_then_create_pr_then_close_issue(self, gh_mocks, monkeypatch):
        from src.graph import nodes

        self._patch_orchestrator(monkeypatch, {
            "executive_summary": "ok",
            "key_findings": [],
            "recommendations": [],
        })
        out = nodes.node_report(self._base_state())

        # PR criado uma vez, ready-for-review (draft=False).
        assert len(gh_mocks["calls"]["create_pr"]) == 1
        assert gh_mocks["calls"]["create_pr"][0]["draft"] is False
        # Issue fechada + claim liberado (happy path).
        assert 42 in gh_mocks["calls"]["release_claim"]
        assert (42, "completed") in gh_mocks["calls"]["close_issue"]
        assert out["status"] == "completed"
        assert out["run_pr"]["number"] == 99

    def test_push_fails_no_pr_no_close(self, gh_mocks, monkeypatch):
        from src.graph import nodes

        gh_mocks["push_result"]["ok"] = False
        self._patch_orchestrator(monkeypatch, {
            "executive_summary": "",
            "key_findings": [],
            "recommendations": [],
        })
        nodes.node_report(self._base_state())

        # Sem push ok, nenhum PR deve abrir.
        assert gh_mocks["calls"]["create_pr"] == []
        # Claim liberado, issue permanece aberta (sem close).
        assert 42 in gh_mocks["calls"]["release_claim"]
        assert gh_mocks["calls"]["close_issue"] == []

    def test_create_pr_fails_release_but_no_close(self, gh_mocks, monkeypatch):
        from src.graph import nodes
        from src.storage import github_pm

        self._patch_orchestrator(monkeypatch, {
            "executive_summary": "",
            "key_findings": [],
            "recommendations": [],
        })
        monkeypatch.setattr(github_pm, "create_pr", lambda *_a, **_kw: None)

        nodes.node_report(self._base_state())

        # Push ok, mas create_pr retornou None: issue NÃO fecha; claim solta.
        assert 42 in gh_mocks["calls"]["release_claim"]
        assert gh_mocks["calls"]["close_issue"] == []


class TestReleaseClaimOnRunFailure:
    def test_release_claim_called_when_start_run_raises(self, monkeypatch):
        from src.services import runs as runs_service

        released = []

        def fake_release(repo, num):
            released.append((repo, num))
            return True

        from src.storage import github_pm
        monkeypatch.setattr(github_pm, "release_claim", fake_release)

        def boom(*_a, **_kw):
            raise RuntimeError("node crashed mid-run")

        class FakeGraph:
            def invoke(self, *_a, **_kw):
                boom()

        monkeypatch.setattr(runs_service, "_graph", lambda: FakeGraph())

        out = runs_service.start_run(
            run_id=UUID("000000aa-0000-0000-0000-000000000000"),
            project_id=UUID("000000bb-0000-0000-0000-000000000000"),
            description="x",
            datasets=[],
            workflow_type="data_quality",
            client_id="cid",
            github_repo=REPO,
            issue_number=7,
            issue_kind="quality",
            issue_title="t",
        )

        assert out["status"] == "failed"
        assert released == [(REPO, 7)]

    def test_no_release_when_run_has_no_issue_context(self, monkeypatch):
        """Runs legacy (POST /v1/runs) não têm issue_number — nada pra liberar."""
        from src.services import runs as runs_service

        released = []

        def fake_release(repo, num):
            released.append((repo, num))

        from src.storage import github_pm
        monkeypatch.setattr(github_pm, "release_claim", fake_release)

        class FakeGraph:
            def invoke(self, *_a, **_kw):
                raise RuntimeError("boom")

        monkeypatch.setattr(runs_service, "_graph", lambda: FakeGraph())

        runs_service.start_run(
            run_id=UUID("000000aa-0000-0000-0000-000000000000"),
            project_id=UUID("000000bb-0000-0000-0000-000000000000"),
            description="x",
            datasets=[],
            workflow_type="data_quality",
        )

        assert released == []
