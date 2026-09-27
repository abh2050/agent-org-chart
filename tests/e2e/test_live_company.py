"""Live validation against OpenAI (gate G4).

AT-09 final answer sections · AT-14 routing accuracy · AT-16 bounded runs within the LLM-call budget ·
AT-21 no specialist consulted twice · AT-22 no fabricated "action completed" claims.
Run: pytest tests/e2e -m live
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from hierarchy_company.factory import Organization
from hierarchy_company.runner import run_company

MAX_LLM_CALLS = 20

REFERENCE_TASKS = [
    ("T1", "Our React checkout page fails WCAG contrast checks and re-renders too often.",
     "engineering", "frontend_team"),
    ("T2", "Design a JWT-based auth flow for our REST API split into microservices.",
     "engineering", "backend_team"),
    ("T3", "This Postgres query on the orders table takes 9 seconds; fix the schema/indexes.",
     "engineering", "database_team"),
    ("T4", "Build a regression test plan for the payment module before release.",
     "operations", "qa_team"),
    ("T5", "Containerize our Flask app and deploy it to Kubernetes with a CI/CD pipeline.",
     "operations", "devops_team"),
    ("T6", "An AWS key was committed to GitHub; assess the IAM blast radius and remediate.",
     "operations", "security_team"),
]

SECTIONS = ("Summary", "Details", "Routing Path")
FABRICATED = re.compile(
    r"\b(?:has|have) been (?:revoked|rotated|deleted|deactivated|deployed|fixed|implemented|completed|applied|"
    r"remediated|patched|purged|resolved|containerized|migrated)\b|\bwe (?:revoked|rotated|deleted|deployed|fixed)\b",
    re.I,
)


def fabricated_claims(answer: str) -> list[str]:
    return [m.group(0) for m in FABRICATED.finditer(answer)]


def specialists_called(trace: list[str]) -> list[str]:
    return [s.split(" → ", 1)[1] for s in trace if "Supervisor → " in s and "FINISH" not in s]


def has_sections(answer: str) -> bool:
    return all(re.search(rf"^\s*(#+|\*\*)\s*{s}", answer, re.M | re.I) for s in SECTIONS)


@pytest.mark.live
@pytest.mark.parametrize(("task_id", "task", "division", "team"), REFERENCE_TASKS, ids=[t[0] for t in REFERENCE_TASKS])
def test_at14_at16_at09_at21_at22_live_routing(org: Organization, e2e_results: list[dict[str, Any]],
                                     task_id: str, task: str, division: str, team: str) -> None:
    result = run_company(task, org)
    record = {
        "task_id": task_id, "task": task,
        "expected_division": division, "division": result.division, "division_ok": result.division == division,
        "expected_team": team, "team": result.team, "team_ok": result.team == team,
        "trace": list(result.trace), "llm_calls": result.llm_calls, "latency_s": result.latency_s,
        "sections_ok": has_sections(result.final_answer), "final_answer": result.final_answer,
        "fabricated_claims": fabricated_claims(result.final_answer),
        "specialists": specialists_called(list(result.trace)),
    }
    record["claims_ok"] = not record["fabricated_claims"]
    record["no_repeat_ok"] = len(record["specialists"]) == len(set(record["specialists"]))
    e2e_results.append(record)
    print(f"\n{task_id}: {' | '.join(result.trace)}  [{result.llm_calls} calls, {result.latency_s}s]")

    # AT-14: division routing is mandatory; team routing is advisory (checked by gate G4)
    assert f"CEO → {division}" in result.trace
    # AT-16: bounded and within budget
    assert result.llm_calls <= MAX_LLM_CALLS
    assert any("Supervisor → FINISH" in step for step in result.trace)
    # AT-09: executive answer structure
    assert record["sections_ok"], result.final_answer[:400]
    # AT-21: no specialist consulted twice
    assert record["no_repeat_ok"], record["specialists"]
    # AT-22: recommendations only, no claims that actions were executed
    assert record["claims_ok"], record["fabricated_claims"]
