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
    primary_language: str = "r",
    client_id: str | None = None,
    github_repo: str | None = None,
) -> dict:
    """Dispara o grafo até a primeira interrupção (human approval do plano)."""
    logger.warning("start_run ENTRY run_id=%s project_id=%s workflow=%s", run_id, project_id, workflow_type)
    graph = _graph()
    initial: ProjectState = {
        "project_id": str(project_id),
        "run_id": str(run_id),
        "description": description,
        "datasets": datasets,
        "workflow_type": workflow_type,
        "primary_language": primary_language,
        "client_id": client_id or "",
        "github_repo": github_repo or "",
        "status": "active",
        "review_loop_count": 0,
    }
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
        return {"status": "aborted", "reason": "token_budget_exceeded", "error": str(e)}
    except Exception as e:
        logger.exception("run %s failed: %s", run_id, e)
        return {"status": "failed", "error": str(e)}
    finally:
        structlog.contextvars.clear_contextvars()


def resume_run(run_id: UUID, decision: str, comments: str | None) -> dict:
    """Retoma o grafo após decisão humana (approved|rejected)."""
    graph = _graph()
    cfg = _config(run_id)

    snapshot = graph.get_state(cfg)
    if snapshot is None:
        return {"status": "not_found"}

    human_log = list(snapshot.values.get("human_decisions", []))
    human_log.append({"decision": decision, "comments": comments, "phase": snapshot.values.get("current_phase")})
    graph.update_state(cfg, {"human_decisions": human_log})

    if decision == "rejected":
        # Marca como abortado; não segue.
        graph.update_state(cfg, {"status": "failed"})
        return {"status": "failed", "reason": "rejected_by_human"}

    settings = get_settings()
    try:
        with run_budget(settings.max_run_tokens):
            state = graph.invoke(None, config=cfg)
        return _snapshot(state)
    except TokenBudgetExceeded as e:
        logger.error("resume_run %s abortado: %s", run_id, e)
        return {"status": "aborted", "reason": "token_budget_exceeded", "error": str(e)}


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
