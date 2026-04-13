from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import ProjectState


def build_graph(checkpointer=None):
    """
    Constrói o grafo LangGraph com human-in-the-loop em dois pontos:
    - após 'plan' (aprovação do plano inicial)
    - após 'modeling' (antes da revisão/treino final)

    interrupt_after força o grafo a pausar; o run é retomado via API após a decisão humana.
    """
    g = StateGraph(ProjectState)

    g.add_node("plan", nodes.node_plan)
    g.add_node("eda", nodes.node_eda)
    g.add_node("decide_next", nodes.node_decide_next)
    g.add_node("modeling", nodes.node_modeling)
    g.add_node("review", nodes.node_review)
    g.add_node("report", nodes.node_report)

    g.add_edge(START, "plan")
    g.add_edge("plan", "eda")
    g.add_edge("eda", "decide_next")
    g.add_edge("decide_next", "modeling")
    g.add_edge("modeling", "review")

    g.add_conditional_edges(
        "review",
        nodes.route_after_review,
        {"modeling": "modeling", "report": "report"},
    )
    g.add_edge("report", END)

    return g.compile(
        checkpointer=checkpointer or MemorySaver(),
        interrupt_after=["plan", "modeling"],
    )
