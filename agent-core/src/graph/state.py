from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class ProjectState(TypedDict, total=False):
    project_id: str
    run_id: str
    description: str
    plan: dict

    # Workflow configuration
    workflow_type: str  # "data_quality" | "eda_hypothesis" | "full_ml"
    primary_language: str  # "r" | "python"
    client_id: str  # UUID do cliente

    current_phase: str  # "planning" | "eda" | "modeling" | "review" | "reporting" | "done"
    datasets: list[str]

    eda_results: dict
    model_results: dict
    review_results: dict
    final_report: dict

    human_decisions: list[dict]
    messages: Annotated[list[Any], add_messages]
    artifacts: list[str]

    status: str  # "active" | "waiting_human" | "completed" | "failed"
    review_loop_count: int
