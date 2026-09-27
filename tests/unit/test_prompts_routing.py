"""AT-03 specialist prompt, AT-04 route schemas, AT-11 routing prompt contents."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hierarchy_company import prompts
from hierarchy_company.registry import ORG
from hierarchy_company.routing import FINISH, make_route_schema, node_id, options_of

SECURITY = ORG.team("security_team")
OPERATIONS = ORG.division("operations")
TASK = "An AWS key was committed to GitHub."


def test_at03_specialist_prompt_role_scope_tools_and_limit() -> None:
    p = prompts.specialist_system_prompt("IAM Agent", "cloud IAM permissions")
    assert "IAM Agent" in p and "cloud IAM permissions" in p
    assert "Use your tools" in p
    assert "at most 150 words" in p


def test_at04_team_schema_accepts_only_its_specialists_and_finish() -> None:
    schema = make_route_schema("SecuritySupervisorDecision", [*SECURITY.specialist_names, FINISH])
    assert set(options_of(schema)) == {"Vulnerability Agent", "IAM Agent", "Secrets Agent", FINISH}
    assert schema(choice="IAM Agent", reason="r").choice == "IAM Agent"
    with pytest.raises(ValidationError):
        schema(choice="React Agent", reason="another team's specialist")
    enum = schema.model_json_schema()["properties"]["choice"]["enum"]
    assert FINISH in enum


@pytest.mark.parametrize("options", [[], ["a", "a"]])
def test_at04_schema_rejects_bad_options(options: list[str]) -> None:
    with pytest.raises(ValueError):
        make_route_schema("Bad", options)


def test_at04_schema_rejects_bad_name() -> None:
    with pytest.raises(ValueError):
        make_route_schema("bad name!", ["a"])


def test_node_id_is_graph_safe() -> None:
    assert node_id("CI/CD Agent") == "ci_cd_agent"
    assert node_id("Query Optimization Agent") == "query_optimization_agent"


def test_at11_team_supervisor_prompt_has_options_rule_task_and_notes() -> None:
    notes = ["[Secrets Agent] key found in settings.py"]
    p = prompts.team_supervisor_prompt(SECURITY, TASK, notes, turns=1, max_turns=3)
    for s in SECURITY.specialists:
        assert f"- {s.name}: {s.role}" in p
    assert f"- {FINISH}:" in p
    assert prompts.PRIMARY_RULE in p
    assert TASK in p and notes[0] in p
    assert "1 of 3" in p
    assert "Each specialist is consulted at most once. Already consulted: none." in p
    assert "Coverage test" in p and "Adjacent or nice-to-have work" in p


def test_route_schema_puts_reason_before_choice() -> None:
    schema = make_route_schema("OrderDecision", ["a", "b"])
    assert list(schema.model_json_schema()["properties"]) == ["reason", "choice"]


def test_at21_supervisor_prompt_lists_only_available_specialists() -> None:
    p = prompts.team_supervisor_prompt(SECURITY, TASK, [], turns=1, max_turns=3,
                                       available=["Vulnerability Agent", "IAM Agent"])
    assert "- IAM Agent:" in p and "- Vulnerability Agent:" in p
    assert "- Secrets Agent:" not in p
    assert "Already consulted: Secrets Agent." in p


def test_at22_prompts_forbid_claims_of_completed_actions() -> None:
    assert "Never claim an action was performed" in prompts.specialist_system_prompt("IAM Agent", "IAM")
    for p in (prompts.team_writer_prompt(SECURITY, TASK, ["[IAM Agent] n"]),
              prompts.director_review_prompt(OPERATIONS, TASK, "security_team", "report"),
              prompts.ceo_final_prompt(TASK, "operations", "report", ["CEO → operations"])):
        assert prompts.RECOMMENDATION_ONLY in p


def test_at11_director_and_ceo_prompts_have_descriptions_rule_task() -> None:
    d = prompts.director_prompt(OPERATIONS, TASK)
    assert "- security_team: vulnerabilities, IAM/permissions, secrets and credential leaks" in d
    assert prompts.PRIMARY_RULE in d and TASK in d

    c = prompts.ceo_prompt(ORG, TASK)
    assert "- engineering:" in c and "- operations:" in c
    assert prompts.PRIMARY_RULE in c and TASK in c


def test_at09_final_prompt_requires_three_sections() -> None:
    p = prompts.ceo_final_prompt(TASK, "operations", "report", ["CEO → operations"])
    for section in ("## Summary", "## Details", "## Routing Path"):
        assert section in p
    assert "CEO → operations" in p


def test_writer_and_review_prompts_include_inputs() -> None:
    assert "(no notes)" in prompts.team_writer_prompt(SECURITY, TASK, [])
    r = prompts.director_review_prompt(OPERATIONS, TASK, "security_team", "TEAM REPORT")
    assert "TEAM REPORT" in r and "at most 3 bullets" in r


def test_at03_make_specialist_builds_real_create_agent_offline() -> None:
    from langchain_openai import ChatOpenAI

    from hierarchy_company.agents import make_specialist

    spec = ORG.team("security_team").specialists[1]
    agent = make_specialist(spec, ChatOpenAI(model="gpt-4.1-mini", api_key="sk-offline"))  # type: ignore[arg-type]
    nodes = set(agent.get_graph().nodes)  # type: ignore[attr-defined]
    assert {"model", "tools"} <= nodes


def test_at25_specialist_prompt_forbids_invented_inputs_and_guessing() -> None:
    p = prompts.specialist_system_prompt("SQL Agent", "SQL")
    assert "never invent code, configs, keys, IDs or metrics" in p
    assert "UNAVAILABLE or INPUT NEEDED" in p and "do not guess" in p
