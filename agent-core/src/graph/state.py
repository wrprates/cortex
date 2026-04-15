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
    client_id: str  # UUID do cliente
    github_repo: str  # URL do repo do cliente (se houver)

    current_phase: str  # "probing" | "planning" | "quality" | "hypothesis" | "modeling" | "review" | "reporting" | "done"
    datasets: list[str]

    dataset_profile: dict  # output de node_probe: shape, tipos, NA%, cardinalidade
    plan_issues: dict  # {stage_name: issue_number} — issues GitHub criadas em node_plan
    milestone_number: int  # milestone do projeto no repo (idempotente por projeto)
    quality_results: dict
    hypothesis_results: dict
    model_results: dict
    review_results: dict
    final_report: dict

    human_decisions: list[dict]
    messages: Annotated[list[Any], add_messages]
    artifacts: list[str]

    status: str  # "active" | "waiting_human" | "completed" | "failed"
    review_loop_count: int
