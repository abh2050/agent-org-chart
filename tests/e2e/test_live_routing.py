"""Live routing tests: every decision point, invoked directly with the same schema + prompt the graphs use.

Run: pytest tests/e2e/test_live_routing.py -m live
"""

from __future__ import annotations

import pytest

from hierarchy_company import prompts
from hierarchy_company.config import Settings
from hierarchy_company.factory import Organization
from hierarchy_company.llm import get_model
from hierarchy_company.registry import ORG
from hierarchy_company.routing import FINISH, make_route_schema

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def model(org: Organization):
    return get_model(Settings.from_env())


def ceo_route(model, task: str) -> str:
    schema = make_route_schema("CEODecision", [d.key for d in ORG.divisions])
    return model.with_structured_output(schema).invoke(prompts.ceo_prompt(ORG, task)).choice


def director_route(model, division: str, task: str) -> str:
    div = ORG.division(division)
    schema = make_route_schema(f"{div.key.capitalize()}DirectorDecision", div.team_keys)
    return model.with_structured_output(schema).invoke(prompts.director_prompt(div, task)).choice


def supervisor_route(model, team_key: str, task: str, notes: list[str], turns: int = 0) -> str:
    """Mirrors graphs/team.py: only specialists without a note are offered (FR-15)."""
    team = ORG.team(team_key)
    available = [n for n in team.specialist_names if not any(note.startswith(f"[{n}]") for note in notes)]
    schema = make_route_schema(f"{team.label}SupervisorDecision", [*available, FINISH])
    prompt = prompts.team_supervisor_prompt(team, task, notes, turns, 3, available)
    return model.with_structured_output(schema).invoke(prompt).choice


# ── L1: CEO → division ───────────────────────────────────────────────
CEO_CASES = [
    ("Add a dark-mode toggle to the settings page in our React app.", "engineering"),
    ("Expose a new GraphQL endpoint for order history.", "engineering"),
    ("Migrate the users table from MySQL to Postgres without downtime.", "engineering"),
    ("Our nightly CI build has been failing for three days.", "operations"),
    ("Write integration tests for the Stripe webhook handler.", "operations"),
    ("Rotate all database passwords after an employee left.", "operations"),
    ("Set up autoscaling for our pods during Black Friday traffic.", "operations"),
    ("Refactor the cart component to reduce bundle size.", "engineering"),
]


@pytest.mark.parametrize(("task", "expected"), CEO_CASES)
def test_ceo_routes_to_division(model, task: str, expected: str) -> None:
    assert ceo_route(model, task) == expected


# ── L2: Director → team ──────────────────────────────────────────────
DIRECTOR_CASES = [
    ("engineering", "Keyboard users can't reach the checkout button.", "frontend_team"),
    ("engineering", "Implement OAuth2 login with Google for our API.", "backend_team"),
    ("engineering", "Add a composite index and normalize the addresses table.", "database_team"),
    ("engineering", "Split the monolith's billing module into its own service.", "backend_team"),
    ("operations", "Raise unit-test coverage of the pricing module to 80%.", "qa_team"),
    ("operations", "Write a Helm chart and GitHub Actions workflow for the API.", "devops_team"),
    ("operations", "A critical CVE was published for our JWT library.", "security_team"),
    ("operations", "Find which tests became flaky after the last release.", "qa_team"),
]


@pytest.mark.parametrize(("division", "task", "expected"), DIRECTOR_CASES)
def test_director_routes_to_team(model, division: str, task: str, expected: str) -> None:
    assert director_route(model, division, task) == expected


# ── L3: Supervisor → first specialist ────────────────────────────────
SUPERVISOR_FIRST_PICK = [
    ("frontend_team", "The page fails WCAG color-contrast checks.", {"Accessibility Agent"}),
    ("backend_team", "Define token lifetimes and claims for our JWTs.", {"Auth Agent"}),
    ("database_team", "Explain why this SELECT is doing a sequential scan.", {"SQL Agent", "Query Optimization Agent"}),
    ("qa_team", "Which changed modules need regression coverage this release?", {"Regression Agent"}),
    ("devops_team", "Write a Dockerfile for our Flask app.", {"Docker Agent"}),
    ("security_team", "Check which IAM permissions a leaked key grants.", {"IAM Agent"}),
]


@pytest.mark.parametrize(("team", "task", "acceptable"), SUPERVISOR_FIRST_PICK)
def test_supervisor_first_pick(model, team: str, task: str, acceptable: set[str]) -> None:
    choice = supervisor_route(model, team, task, notes=[])
    assert choice in acceptable
    assert choice != FINISH  # prompt requires at least one specialist first


def test_supervisor_finishes_when_notes_cover_task(model) -> None:
    """Regression for v2.0 over-consultation: FINISH in only 2/15 trials before the coverage rule, 15/15 after."""
    task = "Write a Dockerfile for our Flask app."
    notes = ["[Docker Agent] Multi-stage Dockerfile on python:3.12-slim, non-root user, gunicorn on 8000, "
             "HEALTHCHECK on /health. Scan clean: no root, pinned tags, .dockerignore added."]
    choices = [supervisor_route(model, "devops_team", task, notes, turns=1) for _ in range(5)]
    assert choices.count(FINISH) >= 4, choices


def test_supervisor_moves_to_next_specialist_for_multi_part_task(model) -> None:
    task = "Containerize our Flask app and deploy it to Kubernetes with a CI/CD pipeline."
    notes = ["[Docker Agent] Dockerfile done: multi-stage, non-root, gunicorn, healthcheck."]
    choice = supervisor_route(model, "devops_team", task, notes, turns=1)
    assert choice in {"Kubernetes Agent", "CI/CD Agent"}


def test_supervisor_does_not_repeat_specialist_without_new_info(model) -> None:
    """AT-21 regression check for the v1.0 T5 behaviour (Kubernetes Agent called twice)."""
    task = "Containerize our Flask app and deploy it to Kubernetes with a CI/CD pipeline."
    notes = ["[Docker Agent] Dockerfile done: multi-stage, non-root, gunicorn, healthcheck.",
             "[Kubernetes Agent] Deployment (3 replicas), Service, Ingress with TLS, HPA 2–10 pods; "
             "manifest validated."]
    choice = supervisor_route(model, "devops_team", task, notes, turns=2)
    assert choice in {"CI/CD Agent", FINISH}, f"re-picked {choice} with no new information"
