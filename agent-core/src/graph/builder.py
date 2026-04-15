from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import ProjectState


def build_graph(checkpointer=None, *, use_postgres: bool = True):
    """
    Constrói o grafo LangGraph com human-in-the-loop e suporte a workflows parciais.

    Workflows:
    - data_quality: plan → eda → report (sem modelagem)
    - eda_hypothesis: plan → eda → report (com testes de hipóteses, sem modelagem)
    - full_ml: plan → eda → decide_next → modeling → review → report

    interrupt_after força o grafo a pausar; o run é retomado via API após a decisão humana.
    """
    g = StateGraph(ProjectState)

    g.add_node("probe", nodes.node_probe)
    g.add_node("planning", nodes.node_plan)
    g.add_node("eda", nodes.node_eda)
    g.add_node("decide_next", nodes.node_decide_next)
    g.add_node("modeling", nodes.node_modeling)
    g.add_node("review", nodes.node_review)
    g.add_node("report", nodes.node_report)

    g.add_edge(START, "probe")
    g.add_edge("probe", "planning")
    g.add_edge("planning", "eda")

    # Roteamento condicional após EDA baseado em workflow_type
    g.add_conditional_edges(
        "eda",
        nodes.route_after_eda,
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

    return g.compile(
        checkpointer=checkpointer,
        interrupt_after=["planning", "modeling"],
    )
