from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import ProjectState


def build_graph(checkpointer=None, *, use_postgres: bool = True):
    """
    Constrói o grafo LangGraph sem breakpoints — teammate mode (sprint-lamina 3/4).

    Workflows:
    - data_quality:   probe → planning → quality → report
    - eda_hypothesis: probe → planning → quality → hypothesis → report
    - full_ml:        probe → planning → quality → hypothesis → decide_next
                                                              → modeling → review → report

    Review humana acontece no PR do GitHub aberto pelo node_report, não mais
    num endpoint POST /v1/decisions. Run flui do claim até terminal.
    """
    g = StateGraph(ProjectState)

    g.add_node("probe", nodes.node_probe)
    g.add_node("planning", nodes.node_plan)
    g.add_node("quality", nodes.node_quality)
    g.add_node("hypothesis", nodes.node_hypothesis)
    g.add_node("decide_next", nodes.node_decide_next)
    g.add_node("modeling", nodes.node_modeling)
    g.add_node("review", nodes.node_review)
    g.add_node("report", nodes.node_report)

    g.add_edge(START, "probe")
    g.add_edge("probe", "planning")
    g.add_edge("planning", "quality")

    g.add_conditional_edges(
        "quality",
        nodes.route_after_quality,
        {"hypothesis": "hypothesis", "report": "report"},
    )
    g.add_conditional_edges(
        "hypothesis",
        nodes.route_after_hypothesis,
        {"decide_next": "decide_next", "report": "report"},
    )

    g.add_edge("decide_next", "modeling")
    g.add_edge("modeling", "review")

    g.add_conditional_edges(
        "review",
        nodes.route_after_review,
        {"modeling": "modeling", "report": "report"},
    )
    g.add_edge("report", END)

    if checkpointer is None:
        if use_postgres:
            from .checkpointer import get_checkpointer
            checkpointer = get_checkpointer()
        else:
            checkpointer = MemorySaver()

    return g.compile(checkpointer=checkpointer)
