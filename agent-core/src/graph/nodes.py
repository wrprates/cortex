from __future__ import annotations

import logging

from ..agents import (
    run_analyst,
    run_analyst_r,
    run_modeler,
    run_modeler_r,
    run_orchestrator,
    run_reviewer,
)
from .state import ProjectState

logger = logging.getLogger(__name__)

MAX_REVIEW_LOOPS = 2


def _loop_run(coro):
    """Roda uma coroutine num loop novo, isolado — safe em thread."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def node_plan(state: ProjectState) -> dict:
    workflow_type = state.get("workflow_type", "full_ml")
    plan = run_orchestrator(
        "plan",
        context={
            "description": state.get("description"),
            "datasets": state.get("datasets", []),
            "workflow_type": workflow_type,
        },
    )
    return {"plan": plan, "current_phase": "planning", "status": "waiting_human"}


def node_eda(state: ProjectState) -> dict:
    from ..storage import minio_client
    import os

    language = state.get("primary_language", "r")
    workflow_type = state.get("workflow_type", "full_ml")
    datasets = state.get("datasets", [])

    # Baixa datasets s3:// do MinIO pra passar como inputs ao sandbox
    inputs: dict[str, bytes] = {}
    for uri in datasets:
        if uri.startswith("s3://"):
            # s3://bucket/key...  → pega só o key (remove prefixo bucket)
            path = uri[5:]
            parts = path.split("/", 1)
            if len(parts) == 2:
                key = parts[1]
                try:
                    data = minio_client.get_bytes(key)
                    inputs[os.path.basename(key)] = data
                    logger.info("Dataset downloaded: %s (%d bytes)", key, len(data))
                except Exception as e:
                    logger.error("Failed to download %s: %s", uri, e)

    context = {
        "plan": state.get("plan"),
        "datasets": datasets,
        "available_inputs": list(inputs.keys()),
    }

    if language == "r":
        result = run_analyst_r(
            task="Execute EDA conforme o plano aprovado.",
            context=context,
            inputs=inputs,
            workflow_type=workflow_type,
        )
    else:
        result = run_analyst(
            task="Execute EDA conforme o plano aprovado.",
            context=context,
            inputs=inputs,
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
    language = state.get("primary_language", "r")

    context = {
        "plan": state.get("plan"),
        "eda_summary": (state.get("eda_results") or {}).get("summary"),
    }

    if language == "r":
        result = run_modeler_r(
            task="Treine e compare modelos baseline conforme o plano.",
            context=context,
            final_training=False,
        )
    else:
        result = run_modeler(
            task="Treine e compare modelos baseline conforme o plano.",
            context=context,
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
    import asyncio
    import json as _json
    from ..storage import github_manager, postgres as _db

    report = run_orchestrator(
        "compile_report",
        context={
            "plan": state.get("plan"),
            "eda_results": state.get("eda_results"),
            "model_results": state.get("model_results"),
            "review_results": state.get("review_results"),
        },
    )

    # Push pro GitHub se o cliente tem repo (já resolvido no start, via state)
    push_status = "skipped"
    repo_url = state.get("github_repo")
    if repo_url:
        try:
            eda = state.get("eda_results") or {}
            model = state.get("model_results") or {}
            files = {
                "final_report.json": _json.dumps(report, ensure_ascii=False, indent=2).encode(),
                "plan.json": _json.dumps(state.get("plan") or {}, ensure_ascii=False, indent=2).encode(),
                "eda_summary.json": _json.dumps(eda.get("summary") or {}, ensure_ascii=False, indent=2).encode(),
                "analyst_code.R": (eda.get("code") or "").encode(),
            }
            if model.get("code"):
                files["modeler_code.R"] = model["code"].encode()
            project_name = f"run-{state.get('run_id','unknown')[:8]}"
            ok = _loop_run(github_manager.push_analysis(repo_url, project_name, files))
            push_status = "pushed" if ok else "failed"
        except Exception as e:
            logger.exception("github push error: %s", e)
            push_status = f"error:{type(e).__name__}"

    report["_github_push"] = push_status
    return {
        "final_report": report,
        "current_phase": "done",
        "status": "completed",
    }


def route_after_eda(state: ProjectState) -> str:
    """
    Roteia após EDA baseado no tipo de workflow.

    - data_quality: vai direto para report
    - eda_hypothesis: vai direto para report
    - full_ml: continua para decide_next → modeling
    """
    workflow_type = state.get("workflow_type", "full_ml")
    if workflow_type in ("data_quality", "eda_hypothesis"):
        logger.info("Workflow %s: skipping modeling, going to report.", workflow_type)
        return "report"
    return "decide_next"


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
