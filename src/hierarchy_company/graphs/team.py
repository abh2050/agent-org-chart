"""Level 3: team supervisor subgraph (FR-04, FR-05, FR-06).

START → team_supervisor → (specialist → team_supervisor)* → team_writer → END
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from hierarchy_company.agents import Agent, AgentFactory, make_specialist
from hierarchy_company.config import Settings
from hierarchy_company.llm import message_text
from hierarchy_company.prompts import specialist_request, team_supervisor_prompt, team_writer_prompt
from hierarchy_company.registry import TeamSpec
from hierarchy_company.routing import FINISH, make_route_schema, node_id
from hierarchy_company.state import TeamState

WRITER = "team_writer"
SUPERVISOR = "team_supervisor"


def _specialist_node(name: str, agent: Agent, recursion_limit: int):
    def run_specialist(state: TeamState) -> dict[str, Any]:
        request = specialist_request(state["task"], state["specialist_notes"])
        try:
            result = agent.invoke({"messages": [{"role": "user", "content": request}]},
                                  config={"recursion_limit": recursion_limit})
            answer = message_text(result["messages"][-1])
        except GraphRecursionError:  # soft failure: a runaway tool loop must not kill the run
            answer = "Stopped: too many tool-call rounds; no final answer."
        return {"specialist_notes": [f"[{name}] {answer}"], "turns": state["turns"] + 1, "consulted": [name]}

    return run_specialist


def build_team_graph(team: TeamSpec, model: BaseChatModel, settings: Settings,
                     agent_factory: AgentFactory = make_specialist):
    names = team.specialist_names
    max_turns = settings.max_specialist_turns
    routers: dict[tuple[str, ...], Any] = {}

    def router_for(available: tuple[str, ...]) -> Any:
        # ADR-08: the enum only offers unconsulted specialists, so a repeat is impossible (cached per subset)
        if available not in routers:
            schema = make_route_schema(f"{team.label}SupervisorDecision", [*available, FINISH])
            routers[available] = model.with_structured_output(schema)
        return routers[available]

    def forced_finish(why: str) -> dict[str, Any]:
        return {"next_specialist": FINISH, "trace": [f"{team.supervisor_label} → {FINISH} ({why})"]}

    def team_supervisor(state: TeamState) -> dict[str, Any]:
        if state["turns"] >= max_turns:  # ADR-04: cap reached → forced FINISH, no LLM call
            return forced_finish("turn limit")
        available = tuple(n for n in names if n not in state["consulted"])
        if not available:  # FR-15: everyone consulted → forced FINISH, no LLM call
            return forced_finish("all specialists consulted")
        prompt = team_supervisor_prompt(team, state["task"], state["specialist_notes"], state["turns"],
                                        max_turns, available)
        decision: Any = router_for(available).invoke(prompt)
        return {"next_specialist": decision.choice, "trace": [f"{team.supervisor_label} → {decision.choice}"]}

    def route(state: TeamState) -> str:
        # Second guard (ADR-04, FR-15): enforced in code whatever the LLM chose.
        choice = state["next_specialist"]
        if state["turns"] >= max_turns or choice == FINISH or choice in state["consulted"]:
            return WRITER
        return choice

    def team_writer(state: TeamState) -> dict[str, Any]:
        reply = model.invoke(team_writer_prompt(team, state["task"], state["specialist_notes"]))
        return {"team_output": message_text(reply)}

    graph = StateGraph(TeamState)
    graph.add_node(SUPERVISOR, team_supervisor)
    graph.add_node(WRITER, team_writer)
    for spec in team.specialists:
        agent = agent_factory(spec, model)
        graph.add_node(node_id(spec.name), _specialist_node(spec.name, agent, settings.specialist_recursion_limit))
        graph.add_edge(node_id(spec.name), SUPERVISOR)

    graph.add_edge(START, SUPERVISOR)
    graph.add_conditional_edges(SUPERVISOR, route, {**{n: node_id(n) for n in names}, WRITER: WRITER})
    graph.add_edge(WRITER, END)
    return graph.compile(name=team.key)
