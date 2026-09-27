"""Level 2: director subgraph (FR-07, FR-08).

START → director → call_<team> → director_review → END
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from hierarchy_company.llm import message_text
from hierarchy_company.prompts import director_prompt, director_review_prompt
from hierarchy_company.registry import DivisionSpec
from hierarchy_company.routing import make_route_schema
from hierarchy_company.state import DirectorState, initial_team_state

REVIEW = "director_review"


def _team_caller(team_graph: CompiledStateGraph):
    """Wrapper node (ADR-01): director state → team state → back."""
    def call_team(state: DirectorState) -> dict[str, Any]:
        result = team_graph.invoke(initial_team_state(state["task"]))
        return {"team_output": result["team_output"], "trace": result["trace"]}

    return call_team


def build_director_graph(division: DivisionSpec, team_graphs: Mapping[str, CompiledStateGraph],
                         model: BaseChatModel):
    missing = set(division.team_keys) - set(team_graphs)
    if missing:
        raise ValueError(f"{division.key}: missing team graphs {sorted(missing)}")
    schema = make_route_schema(f"{division.key.capitalize()}DirectorDecision", division.team_keys)
    router = model.with_structured_output(schema)

    def director(state: DirectorState) -> dict[str, Any]:
        decision: Any = router.invoke(director_prompt(division, state["task"]))
        return {"selected_team": decision.choice, "reason": decision.reason,
                "trace": [f"{division.director_label} → {decision.choice}"]}

    def route(state: DirectorState) -> str:
        return state["selected_team"]

    def director_review(state: DirectorState) -> dict[str, Any]:
        reply = model.invoke(director_review_prompt(division, state["task"], state["selected_team"],
                                                    state["team_output"]))
        return {"division_output": message_text(reply)}

    graph = StateGraph(DirectorState)
    graph.add_node("director", director)
    graph.add_node(REVIEW, director_review)
    for key in division.team_keys:
        graph.add_node(f"call_{key}", _team_caller(team_graphs[key]))
        graph.add_edge(f"call_{key}", REVIEW)

    graph.add_edge(START, "director")
    graph.add_conditional_edges("director", route, {k: f"call_{k}" for k in division.team_keys})
    graph.add_edge(REVIEW, END)
    return graph.compile(name=f"{division.key}_director")
