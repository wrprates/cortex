"""
Testes da Opção A (PR parcial tolerado) — #36.

Contratos:
- `open_partial_pr` abre DRAFT PR com título `[INCOMPLETO: ...]` somente
  quando `github_repo` + `stages_committed` não vazios estão no state.
- `start_run` em falha (exception ou TokenBudgetExceeded) verifica o
  checkpointer: se alguma stage comitou, abre partial PR antes de
  release_claim.
- Sem stages comitadas → nenhum PR, só release_claim (comportamento pré-36).
"""
from __future__ import annotations

from uuid import UUID

import pytest


REPO = "https://github.com/wrprates/demo-repo"
RUN_ID = UUID("abc12345-0000-0000-0000-000000000000")
PROJECT_ID = UUID("000000bb-0000-0000-0000-000000000000")


@pytest.fixture
def pr_mocks(monkeypatch):
    """Mocks de github_pm (create_pr + release_claim)."""
    calls = {"create_pr": [], "release_claim": []}
    pr_result: dict = {"number": 123, "url": "u", "html_url": "h"}

    def fake_create_pr(repo, *, head, base, title, body, draft):
        calls["create_pr"].append(
            {"head": head, "title": title, "body": body, "draft": draft}
        )
        return pr_result if pr_result else None

    def fake_release(repo, num):
        calls["release_claim"].append((repo, num))
        return True

    from src.storage import github_pm
    monkeypatch.setattr(github_pm, "create_pr", fake_create_pr)
    monkeypatch.setattr(github_pm, "release_claim", fake_release)
    return {"calls": calls, "pr_result": pr_result}


class TestOpenPartialPR:
    def _state_with_stages(self, stages: list[str]) -> dict:
        return {
            "github_repo": REPO,
            "run_id": str(RUN_ID),
            "workflow_type": "full_ml",
            "current_phase": "modeling",
            "issue_number": 42,
            "stages_committed": stages,
            "quality_results": {"summary": {}},
            "hypothesis_results": {"summary": {}},
            "model_results": {},
            "plan": {},
        }

    def test_no_stages_returns_none_no_pr(self, pr_mocks):
        from src.graph import nodes
        out = nodes.open_partial_pr(self._state_with_stages([]), reason="x")
        assert out is None
        assert pr_mocks["calls"]["create_pr"] == []

    def test_no_github_repo_returns_none(self, pr_mocks):
        from src.graph import nodes
        state = self._state_with_stages(["quality"])
        state["github_repo"] = ""
        out = nodes.open_partial_pr(state, reason="x")
        assert out is None
        assert pr_mocks["calls"]["create_pr"] == []

    def test_with_stages_opens_draft_pr(self, pr_mocks):
        from src.graph import nodes
        out = nodes.open_partial_pr(
            self._state_with_stages(["quality", "hypothesis"]),
            reason="token budget exceeded: over by 14k",
        )
        assert out is not None
        assert out["number"] == 123
        assert len(pr_mocks["calls"]["create_pr"]) == 1
        call = pr_mocks["calls"]["create_pr"][0]
        assert call["draft"] is True
        assert call["head"] == "run/abc12345"
        assert "[INCOMPLETO]" in call["title"]
        assert "aborted at modeling" in call["title"]
        assert "token budget exceeded" in call["body"]
        assert "quality, hypothesis" in call["body"]

    def test_create_pr_exception_returns_none(self, pr_mocks, monkeypatch):
        from src.graph import nodes
        from src.storage import github_pm

        def boom(*_a, **_kw):
            raise RuntimeError("api down")

        monkeypatch.setattr(github_pm, "create_pr", boom)
        out = nodes.open_partial_pr(
            self._state_with_stages(["quality"]), reason="x"
        )
        assert out is None


def _fake_graph(raise_exc: Exception, snapshot_values: dict | None):
    """Cria um fake-graph que lança em invoke e entrega snapshot em get_state."""
    class FakeSnapshot:
        def __init__(self, v):
            self.values = v

    class FakeGraph:
        def invoke(self, *_a, **_kw):
            raise raise_exc

        def get_state(self, _cfg):
            if snapshot_values is None:
                return None
            return FakeSnapshot(snapshot_values)

    return FakeGraph()


class TestStartRunPartialPROnFailure:
    def test_exception_with_stages_opens_partial_pr(self, pr_mocks, monkeypatch):
        from src.services import runs as runs_service

        snapshot = {
            "github_repo": REPO,
            "run_id": str(RUN_ID),
            "workflow_type": "full_ml",
            "current_phase": "modeling",
            "issue_number": 7,
            "stages_committed": ["quality", "hypothesis"],
            "plan": {},
        }
        graph = _fake_graph(RuntimeError("modeling crashed"), snapshot)
        monkeypatch.setattr(runs_service, "_graph", lambda: graph)

        out = runs_service.start_run(
            run_id=RUN_ID, project_id=PROJECT_ID,
            description="x", datasets=[],
            workflow_type="full_ml",
            github_repo=REPO,
            issue_number=7, issue_kind="modeling", issue_title="t",
        )

        assert out["status"] == "failed"
        assert "partial_pr" in out and out["partial_pr"]["number"] == 123
        # PR aberto como draft
        assert len(pr_mocks["calls"]["create_pr"]) == 1
        assert pr_mocks["calls"]["create_pr"][0]["draft"] is True
        assert "RuntimeError" in pr_mocks["calls"]["create_pr"][0]["body"]
        # Claim liberado sempre
        assert (REPO, 7) in pr_mocks["calls"]["release_claim"]

    def test_exception_without_stages_no_pr_only_release(self, pr_mocks, monkeypatch):
        from src.services import runs as runs_service

        snapshot = {
            "github_repo": REPO,
            "run_id": str(RUN_ID),
            "stages_committed": [],  # nada comitado ainda
        }
        graph = _fake_graph(RuntimeError("early crash"), snapshot)
        monkeypatch.setattr(runs_service, "_graph", lambda: graph)

        out = runs_service.start_run(
            run_id=RUN_ID, project_id=PROJECT_ID,
            description="x", datasets=[],
            workflow_type="data_quality",
            github_repo=REPO,
            issue_number=9, issue_kind="quality", issue_title="t",
        )

        assert out["status"] == "failed"
        assert "partial_pr" not in out
        assert pr_mocks["calls"]["create_pr"] == []
        assert (REPO, 9) in pr_mocks["calls"]["release_claim"]

    def test_token_budget_exceeded_with_stages_opens_partial_pr(
        self, pr_mocks, monkeypatch
    ):
        from src.services import runs as runs_service
        from src.agents.budget import TokenBudgetExceeded

        snapshot = {
            "github_repo": REPO,
            "run_id": str(RUN_ID),
            "workflow_type": "full_ml",
            "current_phase": "modeling",
            "issue_number": 3,
            "stages_committed": ["quality", "hypothesis"],
        }
        graph = _fake_graph(
            TokenBudgetExceeded("264212 > 250000"), snapshot
        )
        monkeypatch.setattr(runs_service, "_graph", lambda: graph)

        out = runs_service.start_run(
            run_id=RUN_ID, project_id=PROJECT_ID,
            description="x", datasets=[],
            workflow_type="full_ml",
            github_repo=REPO,
            issue_number=3, issue_kind="modeling", issue_title="t",
        )

        assert out["status"] == "aborted"
        assert out["reason"] == "token_budget_exceeded"
        assert "partial_pr" in out and out["partial_pr"]["number"] == 123
        assert len(pr_mocks["calls"]["create_pr"]) == 1
        body = pr_mocks["calls"]["create_pr"][0]["body"]
        assert "token budget exceeded" in body
        assert (REPO, 3) in pr_mocks["calls"]["release_claim"]

    def test_checkpoint_unavailable_no_pr_still_release(
        self, pr_mocks, monkeypatch
    ):
        """Se get_state explodir, partial-PR helper é best-effort (só loga)."""
        from src.services import runs as runs_service

        class BrokenGraph:
            def invoke(self, *_a, **_kw):
                raise RuntimeError("boom")

            def get_state(self, _cfg):
                raise RuntimeError("checkpointer down")

        monkeypatch.setattr(runs_service, "_graph", lambda: BrokenGraph())

        out = runs_service.start_run(
            run_id=RUN_ID, project_id=PROJECT_ID,
            description="x", datasets=[],
            workflow_type="data_quality",
            github_repo=REPO,
            issue_number=1, issue_kind="quality", issue_title="t",
        )

        assert out["status"] == "failed"
        assert "partial_pr" not in out
        assert pr_mocks["calls"]["create_pr"] == []
        assert (REPO, 1) in pr_mocks["calls"]["release_claim"]
