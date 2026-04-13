from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any
from uuid import UUID

from ..graph import build_graph
from ..graph.state import ProjectState

logger = logging.getLogger(__name__)


@lru_cache
def _graph():
    return build_graph(use_postgres=True)


def _config(run_id: UUID) -> dict:
    return {"configurable": {"thread_id": str(run_id)}}


def start_run(run_id: UUID, project_id: UUID, description: str, datasets: list[str]) -> dict:
    """Dispara o grafo até a primeira interrupção (human approval do plano)."""
    graph = _graph()
    initial: ProjectState = {
        "project_id": str(project_id),
        "run_id": str(run_id),
        "description": description,
        "datasets": datasets,
        "status": "active",
        "review_loop_count": 0,
    }
    try:
        state = graph.invoke(initial, config=_config(run_id))
        return _snapshot(state)
    except Exception as e:
        logger.exception("run %s failed", run_id)
        return {"status": "failed", "error": str(e)}


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

    state = graph.invoke(None, config=cfg)
    return _snapshot(state)


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
        "eda_summary": (state.get("eda_results") or {}).get("summary"),
        "model_metrics": (state.get("model_results") or {}).get("metrics"),
        "review": state.get("review_results"),
        "final_report": state.get("final_report"),
        "human_decisions": state.get("human_decisions", []),
    }
