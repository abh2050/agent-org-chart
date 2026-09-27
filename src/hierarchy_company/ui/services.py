"""Framework-free logic behind the Streamlit UI (FR-20, ADR-12). Nothing here imports streamlit."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from hierarchy_company.config import Settings, load_env
from hierarchy_company.factory import Organization, build_company
from hierarchy_company.llm import get_model
from hierarchy_company.runner import RunEvent, RunResult, append_attachments, stream_company

log = logging.getLogger("hierarchy_company.ui")

MODELS = ("gpt-4.1-mini", "gpt-4.1-nano", "gpt-4.1", "gpt-4o-mini")
DIAGRAMS = Path(__file__).resolve().parents[3] / "docs" / "diagrams"
ARCHITECTURE_URL = "https://abh2050.github.io/agent-org-chart/"

EXAMPLES: dict[str, str] = {
    "Frontend · WCAG contrast and re-renders":
        "Our React checkout page fails WCAG contrast checks (#777777 text on #FFFFFF) and re-renders too often.",
    "Backend · JWT auth for microservices":
        "Design a JWT-based auth flow for our REST API split into microservices.",
    "Database · 9-second Postgres query":
        "This Postgres query on the orders table takes 9 seconds; fix the schema/indexes:\n"
        "SELECT * FROM orders WHERE customer_id = 42 AND status = 'open' ORDER BY created_at DESC OFFSET 5000 LIMIT 50",
    "QA · regression plan for payments":
        "Build a regression test plan for the payment module before release.",
    "DevOps · review a Dockerfile":
        "Our container image is 1.2 GB and rebuilds slowly. Improve this Dockerfile before we deploy:\n"
        "FROM python\nADD . /app\nENV API_KEY=abc123\nRUN pip install -r /app/requirements.txt\n"
        "CMD python /app/app.py",
    "Security · over-privileged IAM policy":
        "Assess the blast radius of this IAM policy attached to our CI user:\n"
        '{"Statement": [{"Effect": "Allow", "Action": ["s3:*", "iam:PassRole", '
        '"secretsmanager:GetSecretValue"], "Resource": "*"}]}',
}


def openai_key_in_env() -> bool:
    load_env()
    return bool(os.environ.get("OPENAI_API_KEY"))


def build_org(model: str, max_turns: int, api_key: str | None = None) -> Organization:
    """Build the organization for the chosen model and turn limit; other settings come from HC_* env."""
    settings = replace(Settings.from_env(), model=model, max_specialist_turns=max_turns)
    return build_company(model=get_model(settings, api_key=api_key or None), settings=settings)


def compose_task(task: str, files: list[tuple[str, bytes]]) -> str:
    return append_attachments(task.strip(), files) if files else task.strip()


def run_stream(task: str, org: Organization) -> Iterator[RunEvent | RunResult]:
    return stream_company(task, org)


@dataclass(frozen=True)
class Integration:
    name: str
    ready: bool
    detail: str


def integration_status() -> list[Integration]:
    """What the tools can reach right now. Never includes secret values."""
    load_env()
    aws = False
    try:
        import boto3

        aws = boto3.Session().get_credentials() is not None
    except Exception:  # boto3 missing or a broken profile: treat as not configured
        aws = False
    return [
        Integration("OSV.dev", True, "public vulnerability data, no key needed"),
        Integration("GitHub Actions", True, "public repos; GITHUB_TOKEN set" if os.environ.get("GITHUB_TOKEN")
                    else "public repos only (set GITHUB_TOKEN for private)"),
        Integration("Postgres", bool(os.environ.get("HC_DATABASE_URL")),
                    "HC_DATABASE_URL set" if os.environ.get("HC_DATABASE_URL") else "set HC_DATABASE_URL to enable"),
        Integration("AWS IAM", aws, "credentials found" if aws else "no AWS credentials found"),
    ]


def describe_error(exc: Exception) -> str:
    """A user-facing explanation that never includes the key or raw provider messages."""
    name = type(exc).__name__
    log.error("run failed: %s", name, exc_info=exc)
    if isinstance(exc, ValueError):
        return str(exc)
    if name == "AuthenticationError":
        return "OpenAI rejected the API key. Check the key in the sidebar or in .env."
    if name == "RateLimitError":
        return "OpenAI rate limit or quota reached. Wait a minute or check your plan's usage."
    if name in ("APIConnectionError", "APITimeoutError"):
        return "Could not reach OpenAI. Check your network connection and try again."
    if name == "NotFoundError":
        return "The selected model isn't available to this API key. Pick another model in the sidebar."
    return f"The run failed ({name}). Details were written to the terminal log."


@dataclass(frozen=True)
class RunRecord:
    result: RunResult
    events: tuple[RunEvent, ...]

    def notes(self) -> list[RunEvent]:
        return [e for e in self.events if e.kind == "note"]

    def tool_calls(self) -> list[tuple[RunEvent, RunEvent | None]]:
        """Pair each tool call with the result that follows it (results arrive in call order per agent)."""
        pairs: list[tuple[RunEvent, RunEvent | None]] = []
        pending: list[int] = []
        for e in self.events:
            if e.kind == "tool_call":
                pairs.append((e, None))
                pending.append(len(pairs) - 1)
            elif e.kind == "tool_result" and pending:
                i = pending.pop(0)
                pairs[i] = (pairs[i][0], e)
        return pairs

    def output(self, kind: str) -> str:
        return next((e.text for e in self.events if e.kind == kind), "")

    def to_json(self) -> str:
        return json.dumps({**self.result.to_dict(),
                           "events": [{"kind": e.kind, "actor": e.actor, "text": e.text} for e in self.events]},
                          indent=2, ensure_ascii=False)


def progress_line(event: RunEvent) -> str | None:
    """One Markdown line for the live progress log, or None for events that only appear in the result."""
    if event.kind == "route":
        return f"**{event.text}**"
    if event.kind == "tool_call":
        return f"&nbsp;&nbsp;`{event.actor}` calls `{event.text}`"
    if event.kind == "tool_result":
        status = event.text.split(" ", 1)[0] if event.text.startswith(("UNAVAILABLE", "INPUT")) else "result"
        return f"&nbsp;&nbsp;&nbsp;&nbsp;↳ {event.actor}: {status}"
    if event.kind == "note":
        return f"&nbsp;&nbsp;{event.actor} reported back"
    if event.kind == "team_output":
        return "Team report written"
    if event.kind == "division_output":
        return "Director review written"
    return None
