from __future__ import annotations

import logging

from ..agents import run_analyst, run_modeler, run_orchestrator, run_reviewer
from .state import ProjectState

logger = logging.getLogger(__name__)

MAX_REVIEW_LOOPS = 2


def node_plan(state: ProjectState) -> dict:
    plan = run_orchestrator(
        "plan",
        context={
            "description": state.get("description"),
            "datasets": state.get("datasets", []),
        },
    )
    return {"plan": plan, "current_phase": "planning", "status": "waiting_human"}


def node_eda(state: ProjectState) -> dict:
    result = run_analyst(
        task="Execute EDA conforme o plano aprovado.",
        context={
            "plan": state.get("plan"),
            "datasets": state.get("datasets", []),
        },
    )
    return {"eda_results": result, "current_phase": "eda"}


def node_decide_next(state: ProjectState) -> dict:
    decision = run_orchestrator(
        "decide_next",
        context={
            "plan": state.get("plan"),
            "eda_results": state.get("eda_results"),
            "model_results": state.get("model_results"),
            "review_results": state.get("review_results"),
        },
    )
    return {"plan": {**state.get("plan", {}), "_next_decision": decision}}


def node_modeling(state: ProjectState) -> dict:
    result = run_modeler(
        task="Treine e compare modelos baseline conforme o plano.",
        context={
            "plan": state.get("plan"),
            "eda_summary": (state.get("eda_results") or {}).get("summary"),
        },
        final_training=False,
    )
    return {
        "model_results": result,
        "current_phase": "modeling",
        "status": "waiting_human",
    }


def node_review(state: ProjectState) -> dict:
    result = run_reviewer(
        artifacts_context={
            "eda_results": state.get("eda_results"),
            "model_results": state.get("model_results"),
        }
    )
    loops = state.get("review_loop_count", 0) + 1
    return {
        "review_results": result,
        "current_phase": "review",
        "review_loop_count": loops,
    }


def node_report(state: ProjectState) -> dict:
    report = run_orchestrator(
        "compile_report",
        context={
            "plan": state.get("plan"),
            "eda_results": state.get("eda_results"),
            "model_results": state.get("model_results"),
            "review_results": state.get("review_results"),
        },
    )
    return {
        "final_report": report,
        "current_phase": "done",
        "status": "completed",
    }


def route_after_review(state: ProjectState) -> str:
    review = state.get("review_results") or {}
    decision = review.get("decision")
    loops = state.get("review_loop_count", 0)

    if decision == "approved":
        return "report"
    if loops >= MAX_REVIEW_LOOPS:
        logger.warning("Review rejected %d times, forcing report.", loops)
        return "report"
    # Reprovado e ainda dentro do limite → volta para modelagem
    return "modeling"
