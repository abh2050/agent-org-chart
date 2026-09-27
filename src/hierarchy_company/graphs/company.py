"""Level 1: CEO / top supervisor graph (FR-09).

START → ceo → call_<division> → ceo_final → END
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from hierarchy_company.llm import message_text
from hierarchy_company.prompts import ceo_final_prompt, ceo_prompt
from hierarchy_company.registry import Org
from hierarchy_company.routing import make_route_schema
from hierarchy_company.state import CompanyState, initial_director_state

FINAL = "ceo_final"


def _division_caller(director_graph: CompiledStateGraph):
    """Wrapper node (ADR-01): company state → director state → back."""
    def call_division(state: CompanyState) -> dict[str, Any]:
        result = director_graph.invoke(initial_director_state(state["task"]))
        return {"division_output": result["division_output"], "selected_team": result["selected_team"],
                "trace": result["trace"]}

    return call_division


def build_company_graph(org: Org, director_graphs: Mapping[str, CompiledStateGraph], model: BaseChatModel):
    keys = [d.key for d in org.divisions]
    missing = set(keys) - set(director_graphs)
    if missing:
        raise ValueError(f"missing director graphs {sorted(missing)}")
    router = model.with_structured_output(make_route_schema("CEODecision", keys))

    def ceo(state: CompanyState) -> dict[str, Any]:
        decision: Any = router.invoke(ceo_prompt(org, state["task"]))
        return {"selected_division": decision.choice, "ceo_reason": decision.reason,
                "trace": [f"CEO → {decision.choice}"]}

    def route(state: CompanyState) -> str:
        return state["selected_division"]

    def ceo_final(state: CompanyState) -> dict[str, Any]:
        reply = model.invoke(ceo_final_prompt(state["task"], state["selected_division"],
                                              state["division_output"], state["trace"]))
        return {"final_answer": message_text(reply)}

    graph = StateGraph(CompanyState)
    graph.add_node("ceo", ceo)
    graph.add_node(FINAL, ceo_final)
    for key in keys:
        graph.add_node(f"call_{key}", _division_caller(director_graphs[key]))
        graph.add_edge(f"call_{key}", FINAL)

    graph.add_edge(START, "ceo")
    graph.add_conditional_edges("ceo", route, {k: f"call_{k}" for k in keys})
    graph.add_edge(FINAL, END)
    return graph.compile(name="company")
