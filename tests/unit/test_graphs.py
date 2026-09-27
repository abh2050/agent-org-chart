"""AT-03, AT-05, AT-06 (team) · AT-07, AT-08 (director) · AT-09, AT-10, AT-15 (company)."""

from __future__ import annotations

from typing import Any

import pytest

from hierarchy_company.config import Settings
from hierarchy_company.graphs import build_company_graph, build_director_graph, build_team_graph
from hierarchy_company.registry import ORG
from hierarchy_company.state import initial_company_state, initial_director_state, initial_team_state
from tests.fakes import FakeChatModel, LoopingAgent, fake_agent_factory
from tests.unit.conftest import SECURITY_SCRIPT, make_org

TASK = "An AWS key was committed to GitHub; assess the IAM blast radius and remediate."


def team_graph(script: dict[str, list[str]], log: list, settings: Settings, cls=None):
    model = FakeChatModel(script)
    factory = fake_agent_factory(log, cls) if cls else fake_agent_factory(log)
    return build_team_graph(ORG.team("security_team"), model, settings, factory), model  # type: ignore[arg-type]


# ── Level 3 ─────────────────────────────────────────────────────────
def test_at05_turn_cap_enforced_when_llm_never_finishes(agent_log: list, settings: Settings) -> None:
    graph, model = team_graph({}, agent_log, settings)  # fake always picks the first specialist
    out = graph.invoke(initial_team_state(TASK))
    assert out["turns"] == settings.max_specialist_turns == 3
    assert len(agent_log) == 3
    assert out["trace"][-1] == "Security Supervisor → FINISH (turn limit)"
    assert len(model.prompts_for("SecuritySupervisorDecision")) == 3  # 4th decision forced without an LLM call


def test_at05_turn_cap_is_configurable(agent_log: list) -> None:
    graph, _ = team_graph({}, agent_log, Settings(max_specialist_turns=1))
    out = graph.invoke(initial_team_state(TASK))
    assert out["turns"] == 1 and len(agent_log) == 1


def test_at21_no_specialist_consulted_twice(agent_log: list) -> None:
    graph, model = team_graph({}, agent_log, Settings(max_specialist_turns=5))  # fake picks first offered
    out = graph.invoke(initial_team_state(TASK))

    names = [name for name, _ in agent_log]
    assert names == ["Vulnerability Agent", "IAM Agent", "Secrets Agent"]  # each exactly once
    assert out["consulted"] == names
    assert [len(o) for o in model.offered] == [4, 3, 2]  # enum shrinks as specialists are consulted
    assert all(set(o).isdisjoint(names[:i]) for i, o in enumerate(model.offered))
    assert out["trace"][-1] == "Security Supervisor → FINISH (all specialists consulted)"
    assert len(model.prompts_for("SecuritySupervisorDecision")) == 3  # forced FINISH made no LLM call


def test_at06_supervisor_loop_and_writer(agent_log: list, settings: Settings) -> None:
    script = {"SecuritySupervisorDecision": ["Secrets Agent", "IAM Agent", "FINISH"]}
    graph, model = team_graph(script, agent_log, settings)
    out = graph.invoke(initial_team_state(TASK))

    assert [name for name, _ in agent_log] == ["Secrets Agent", "IAM Agent"]
    assert out["specialist_notes"] == ["[Secrets Agent] Secrets Agent answer", "[IAM Agent] IAM Agent answer"]
    assert out["trace"] == ["Security Supervisor → Secrets Agent", "Security Supervisor → IAM Agent",
                            "Security Supervisor → FINISH"]
    assert out["team_output"].startswith("fake reply")
    # AT-11: second decision saw the first specialist's note; writer saw all notes
    assert "[Secrets Agent] Secrets Agent answer" in model.prompts_for("SecuritySupervisorDecision")[1]
    assert "[IAM Agent] IAM Agent answer" in model.prompts_for("text")[0]


def test_at03_specialist_recursion_limit_passed_and_soft_fails(agent_log: list, settings: Settings) -> None:
    script = {"SecuritySupervisorDecision": ["IAM Agent", "FINISH"]}
    graph, _ = team_graph(script, agent_log, settings, cls=LoopingAgent)
    out = graph.invoke(initial_team_state(TASK))
    assert agent_log[0][1] == {"recursion_limit": settings.specialist_recursion_limit}
    assert out["specialist_notes"] == ["[IAM Agent] Stopped: too many tool-call rounds; no final answer."]
    assert out["team_output"]  # run still completes


# ── Level 2 ─────────────────────────────────────────────────────────
def test_at07_at08_director_routes_to_exactly_one_team(agent_log: list, settings: Settings) -> None:
    org, _ = make_org(SECURITY_SCRIPT, agent_log, settings)
    out = org.director_graphs["operations"].invoke(initial_director_state(TASK))

    security = set(ORG.team("security_team").specialist_names)
    assert {name for name, _ in agent_log} <= security  # no other team ran
    assert out["selected_team"] == "security_team"
    assert out["reason"] == "scripted choice security_team"
    assert out["trace"][0] == "Operations Director → security_team"
    assert out["division_output"].startswith("fake reply")


def test_at07_director_requires_all_team_graphs(settings: Settings) -> None:
    with pytest.raises(ValueError, match="missing team graphs"):
        build_director_graph(ORG.division("operations"), {}, FakeChatModel())  # type: ignore[arg-type]


# ── Level 1 ─────────────────────────────────────────────────────────
def test_at10_full_trace_across_four_levels(agent_log: list, settings: Settings) -> None:
    org, _ = make_org(SECURITY_SCRIPT, agent_log, settings)
    out = org.company_graph.invoke(initial_company_state(TASK), config={"recursion_limit": 50})
    assert out["trace"] == [
        "CEO → operations",
        "Operations Director → security_team",
        "Security Supervisor → Secrets Agent",
        "Security Supervisor → IAM Agent",
        "Security Supervisor → FINISH",
    ]


def test_at09_ceo_routes_and_final_answer_uses_trace(agent_log: list, settings: Settings) -> None:
    script = {"CEODecision": ["engineering"], "EngineeringDirectorDecision": ["database_team"],
              "DatabaseSupervisorDecision": ["Schema Agent", "FINISH"]}
    org, model = make_org(script, agent_log, settings)
    out = org.company_graph.invoke(initial_company_state("Slow Postgres query"))

    assert out["selected_division"] == "engineering" and out["selected_team"] == "database_team"
    assert [name for name, _ in agent_log] == ["Schema Agent"]
    final_prompt = model.prompts_for("text")[-1]
    assert "## Routing Path" in final_prompt and "Database Supervisor → Schema Agent" in final_prompt
    assert out["final_answer"].startswith("fake reply")


def test_at09_company_requires_all_director_graphs() -> None:
    with pytest.raises(ValueError, match="missing director graphs"):
        build_company_graph(ORG, {}, FakeChatModel())  # type: ignore[arg-type]


def test_at15_build_offline_with_injected_model(agent_log: list[tuple[str, Any]]) -> None:
    org, _ = make_org({}, agent_log)
    assert set(org.team_graphs) == {t.key for t in ORG.teams}
    assert set(org.director_graphs) == {"engineering", "operations"}
    nodes = set(org.team_graphs["devops_team"].get_graph().nodes)
    assert {"team_supervisor", "team_writer", "docker_agent", "kubernetes_agent", "ci_cd_agent"} <= nodes
    assert "call_security_team" in org.director_graphs["operations"].get_graph().nodes
    assert agent_log == []  # building never calls an agent
