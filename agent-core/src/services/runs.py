from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any
from uuid import UUID

import structlog

from ..agents.budget import TokenBudgetExceeded, run_budget
from ..config import get_settings
from ..graph import build_graph
from ..graph.state import ProjectState

logger = logging.getLogger(__name__)


@lru_cache
def _graph():
    return build_graph(use_postgres=True)


def _config(run_id: UUID) -> dict:
    return {"configurable": {"thread_id": str(run_id)}}


def start_run(
    run_id: UUID,
    project_id: UUID,
    description: str,
    datasets: list[str],
    workflow_type: str = "full_ml",
    client_id: str | None = None,
    github_repo: str | None = None,
    issue_number: int | None = None,
    issue_kind: str | None = None,
    issue_title: str | None = None,
) -> dict:
    """Dispara o grafo e roda até terminal (teammate mode, sem breakpoints)."""
    logger.warning("start_run ENTRY run_id=%s project_id=%s workflow=%s", run_id, project_id, workflow_type)
    graph = _graph()
    initial: ProjectState = {
        "project_id": str(project_id),
        "run_id": str(run_id),
        "description": description,
        "datasets": datasets,
        "workflow_type": workflow_type,
        "client_id": client_id or "",
        "github_repo": github_repo or "",
        "status": "active",
        "review_loop_count": 0,
    }
    # Issue-driven run (teammate mode): propaga contexto da issue pro state
    # pra nós futuros (pick_issue / node_report / self_review) poderem ler.
    if issue_number is not None:
        initial["issue_number"] = issue_number
    if issue_kind:
        initial["issue_kind"] = issue_kind
    if issue_title:
        initial["issue_title"] = issue_title
    logger.warning("start_run BEFORE_INVOKE run_id=%s", run_id)
    settings = get_settings()
    structlog.contextvars.bind_contextvars(run_id=str(run_id), project_id=str(project_id))
    try:
        with run_budget(settings.max_run_tokens):
            state = graph.invoke(initial, config=_config(run_id))
        logger.warning("start_run AFTER_INVOKE run_id=%s phase=%s", run_id, state.get("current_phase"))
        return _snapshot(state)
    except TokenBudgetExceeded as e:
        logger.error("run %s abortado: %s", run_id, e)
        _release_claim_on_failure(github_repo, issue_number, run_id)
        return {"status": "aborted", "reason": "token_budget_exceeded", "error": str(e)}
    except Exception as e:
        logger.exception("run %s failed: %s", run_id, e)
        _release_claim_on_failure(github_repo, issue_number, run_id)
        return {"status": "failed", "error": str(e)}
    finally:
        structlog.contextvars.clear_contextvars()


def _release_claim_on_failure(
    github_repo: str | None, issue_number: int | None, run_id: UUID
) -> None:
    """
    Sprint-lamina 4/4: se o run falha antes de node_report, libera o claim
    (`cortex:in-progress`) pra a issue voltar pro backlog. Não fecha a issue,
    só solta o lock — humano ou próximo tick retomam.
    """
    if not github_repo or issue_number is None:
        return
    try:
        from ..storage import github_pm
        github_pm.release_claim(github_repo, int(issue_number))
        logger.warning(
            "release_claim em run falho: run_id=%s issue=#%s",
            run_id, issue_number,
        )
    except Exception as e:
        logger.warning("release_claim após falha também falhou: %s", e)


def resume_run(run_id: UUID, decision: str, comments: str | None) -> dict:
    """
    DEPRECATED (sprint-lamina 3/4, issue #30). Grafo não tem mais breakpoints;
    `start_run` roda até terminal. Review humana acontece no PR do GitHub.

    Função mantida como no-op por 1 sprint pra não quebrar callers legacy;
    será removida no próximo sprint junto do endpoint POST /v1/decisions.
    """
    logger.warning(
        "DEPRECATED resume_run chamado run_id=%s decision=%s — no-op.",
        run_id, decision,
    )
    return {"status": "deprecated", "reason": "graph runs to terminal, no resume needed"}


def get_run_state(run_id: UUID) -> dict | None:
    graph = _graph()
    snapshot = graph.get_state(_config(run_id))
    if snapshot is None:
        return None
    return _snapshot(snapshot.values)


def _snapshot(state: dict[str, Any]) -> dict:
    return {
        "current_phase": state.get("current_phase"),
        "status": state.get("status"),
        "plan": state.get("plan"),
        "quality_summary": (state.get("quality_results") or {}).get("summary"),
        "hypothesis_summary": (state.get("hypothesis_results") or {}).get("summary"),
        "model_metrics": (state.get("model_results") or {}).get("metrics"),
        "review": state.get("review_results"),
        "final_report": state.get("final_report"),
        "human_decisions": state.get("human_decisions", []),
    }
