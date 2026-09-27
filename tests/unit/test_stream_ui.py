"""AT-27 event stream · AT-28 Streamlit UI (rendered headlessly with streamlit.testing.AppTest)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from hierarchy_company import runner
from hierarchy_company.runner import RunEvent, RunResult, run_company, stream_company
from hierarchy_company.ui import services
from tests.unit.conftest import SECURITY_SCRIPT, make_org

APP = Path(__file__).resolve().parents[2] / "src" / "hierarchy_company" / "ui" / "app.py"
SECRET = "sk-test-" + "never-render-this-value"


# ── AT-27: stream_company ────────────────────────────────────────────
def test_at27_stream_is_ordered_and_ends_with_result(agent_log: list) -> None:
    org, _ = make_org(SECURITY_SCRIPT, agent_log)
    items = list(stream_company("An AWS key leaked", org))
    events, result = items[:-1], items[-1]
    assert isinstance(result, RunResult) and all(isinstance(e, RunEvent) for e in events)

    routes = [e.text for e in events if e.kind == "route"]
    assert tuple(routes) == result.trace  # no duplicates from wrapper nodes, same order
    kinds = [e.kind for e in events]
    assert kinds.index("note") > kinds.index("route")
    assert kinds[-3:] == ["team_output", "division_output", "final"]
    assert [e.actor for e in events if e.kind == "note"] == ["Secrets Agent", "IAM Agent"]
    assert next(e.text for e in events if e.kind == "final") == result.final_answer


def test_at27_run_company_matches_stream(agent_log: list) -> None:
    org, _ = make_org(SECURITY_SCRIPT, agent_log)
    streamed = next(i for i in stream_company("An AWS key leaked", org) if isinstance(i, RunResult))
    org2, _ = make_org(SECURITY_SCRIPT, [])
    direct = run_company("An AWS key leaked", org2)
    assert (streamed.division, streamed.team, streamed.trace) == (direct.division, direct.team, direct.trace)


def test_at27_tool_events_from_specialist_agent_messages(agent_log: list) -> None:
    org, _ = make_org(SECURITY_SCRIPT, agent_log)
    ns = ("call_operations:1", "call_security_team:2", "secrets_agent:3")
    call = AIMessage(content="", tool_calls=[{"name": "scan_for_secrets", "args": {"text": "x" * 100}, "id": "c1"}])
    events = list(runner._events("model", {"messages": [call]}, ns, org))
    assert events[0].kind == "tool_call" and events[0].actor == "Secrets Agent"
    assert events[0].text.startswith("scan_for_secrets(text=") and len(events[0].text) < 90  # args truncated
    result = ToolMessage(content="Secret scan: no issues found.", name="scan_for_secrets", tool_call_id="c1")
    assert next(runner._events("tools", {"messages": [result]}, ns, org)).kind == "tool_result"


def test_at27_stream_rejects_empty_task(agent_log: list) -> None:
    org, model = make_org(SECURITY_SCRIPT, agent_log)
    with pytest.raises(ValueError):
        next(stream_company("  ", org))
    assert model.prompts == []


def test_append_attachments_truncates() -> None:
    out = runner.append_attachments("Review", [("big.txt", b"a" * 60_000), ("Dockerfile", b"FROM x")])
    assert "--- Attached file: big.txt (truncated to 50 KB) ---" in out and "FROM x" in out
    assert out.count("a") < 50_100


# ── services (framework-free UI logic) ───────────────────────────────
def test_at28_record_pairs_tool_calls_with_results() -> None:
    events = (RunEvent("tool_call", "IAM Agent", "analyze_iam_policy(policy_json='{}')"),
              RunEvent("tool_call", "IAM Agent", "get_access_key_last_used(access_key_id='A')"),
              RunEvent("tool_result", "analyze_iam_policy", "IAM policy analysis: no issues found."),
              RunEvent("tool_result", "get_access_key_last_used", "UNAVAILABLE (x): no creds"),
              RunEvent("note", "IAM Agent", "done"), RunEvent("team_output", "team writer", "report"))
    record = services.RunRecord(RunResult("t", "operations", "security_team", "r", ("CEO → operations",), "a", 3, 1.0),
                                events)
    pairs = record.tool_calls()
    assert [p[1].actor for p in pairs if p[1]] == ["analyze_iam_policy", "get_access_key_last_used"]
    assert record.output("team_output") == "report" and len(record.notes()) == 1
    assert '"kind": "tool_call"' in record.to_json()


def test_at28_progress_lines() -> None:
    assert services.progress_line(RunEvent("route", "CEO", "CEO → operations")) == "**CEO → operations**"
    assert "UNAVAILABLE" in services.progress_line(RunEvent("tool_result", "t", "UNAVAILABLE (t): x"))
    assert services.progress_line(RunEvent("final", "CEO", "answer")) is None


class AuthenticationError(Exception):
    pass


@pytest.mark.parametrize(("exc", "expected"), [
    (AuthenticationError("bad key " + SECRET), "rejected the API key"),
    (ValueError("task must be a non-empty string"), "task must be a non-empty string"),
    (RuntimeError("boom " + SECRET), "The run failed (RuntimeError)"),
])
def test_at28_describe_error_never_leaks(exc: Exception, expected: str) -> None:
    message = services.describe_error(exc)
    assert expected in message and SECRET not in message


def test_at28_integration_status(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3

    monkeypatch.setenv("HC_DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(boto3, "Session", lambda: type("S", (), {"get_credentials": lambda self: None})())
    status = {i.name: i for i in services.integration_status()}
    assert status["Postgres"].ready and "postgresql://" not in status["Postgres"].detail
    assert not status["AWS IAM"].ready and status["OSV.dev"].ready


def test_at28_build_org_uses_session_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    org = services.build_org("gpt-4.1-nano", 2, api_key="sk-session-key-for-test")
    assert org.settings.model == "gpt-4.1-nano" and org.settings.max_specialist_turns == 2
    assert services.compose_task("  t  ", []) == "t"


# ── AT-28: the Streamlit page ────────────────────────────────────────
@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, agent_log: list):
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    st.cache_resource.clear()
    calls: dict[str, Any] = {}

    def fake_build_org(model: str, turns: int, api_key: str | None = None):
        calls.update(model=model, turns=turns, api_key=api_key)
        return make_org(SECURITY_SCRIPT, agent_log)[0]

    monkeypatch.setattr(services, "build_org", fake_build_org)
    monkeypatch.setattr(services, "openai_key_in_env", lambda: True)
    monkeypatch.setattr(services, "integration_status", lambda: [services.Integration("OSV.dev", True, "public")])
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.calls = calls  # type: ignore[attr-defined]
    return at


def _all_text(at: Any) -> str:
    parts = []
    for kind in ("markdown", "caption", "code", "success", "info", "warning", "error", "title", "header",
                 "subheader", "metric"):
        for el in getattr(at, kind):
            parts.append(str(getattr(el, "value", "")) + str(getattr(el, "label", "")))
    return "\n".join(parts)


def test_at28_page_renders_with_examples(app) -> None:
    app.run()
    assert not app.exception
    assert app.title[0].value == "hierarchy-company"
    app.selectbox(key="example").select("Security · over-privileged IAM policy").run()
    assert "iam:PassRole" in app.text_area[0].value
    assert not app.button(key="run").disabled


def test_at28_run_shows_route_answer_and_history(app) -> None:
    app.run()
    app.text_area[0].input("An AWS key was committed to GitHub.").run()
    app.button(key="run").click().run()
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Division"] == "operations" and metrics["Team"] == "security_team"
    text = _all_text(app)
    assert "CEO → operations" in text and "Security Supervisor → FINISH" in text
    assert len(app.session_state["history"]) == 1
    assert app.calls["model"] == "gpt-4.1-mini" and app.calls["turns"] == 3


def test_at28_empty_task_warns(app) -> None:
    app.run()
    app.button(key="run").click().run()
    assert app.warning and "Enter a task first" in app.warning[0].value


def test_at28_errors_are_explained(app, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_stream(task: str, org: Any):
        raise AuthenticationError("bad key " + SECRET)
        yield  # pragma: no cover

    monkeypatch.setattr(services, "run_stream", broken_stream)
    app.run()
    app.text_area[0].input("anything").run()
    app.button(key="run").click().run()
    assert "rejected the API key" in app.error[0].value
    assert SECRET not in _all_text(app)


def test_at28_session_key_is_used_but_never_rendered(app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "openai_key_in_env", lambda: False)
    app.run()
    assert app.button(key="run").disabled
    app.text_input(key="api_key").input(SECRET).run()
    app.text_area[0].input("An AWS key was committed to GitHub.").run()
    app.button(key="run").click().run()
    assert app.calls["api_key"] == SECRET
    assert SECRET not in _all_text(app)


def test_at28_launcher_runs_streamlit_with_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from streamlit.web import cli as stcli

    from hierarchy_company.ui import launcher

    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(stcli, "main", lambda: seen.setdefault("argv", list(sys.argv)) and 0)
    monkeypatch.setattr(sys, "argv", ["hierarchy-company-ui", "--server.port=8600"])
    with pytest.raises(SystemExit):
        launcher.main()
    argv = seen["argv"]
    assert argv[:3] == ["streamlit", "run", str(launcher.APP)]
    assert "--theme.primaryColor=#0B7C6E" in argv and argv[-1] == "--server.port=8600"
