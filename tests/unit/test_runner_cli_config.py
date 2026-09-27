"""AT-12 RunResult, AT-13 CLI, AT-17 config, AT-19 input validation, AT-23 timeouts/retries; call counting."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage

from hierarchy_company import cli
from hierarchy_company.config import Settings
from hierarchy_company.llm import get_model, message_text
from hierarchy_company.observability import LLMCallCounter
from hierarchy_company.runner import RunResult, run_company
from tests.unit.conftest import SECURITY_SCRIPT, make_org

TASK = "An AWS key was committed to GitHub; assess the IAM blast radius and remediate."


# ── AT-12 / AT-19 runner ────────────────────────────────────────────
def test_at12_run_result_fields(agent_log: list) -> None:
    org, _ = make_org(SECURITY_SCRIPT, agent_log)
    result = run_company(f"  {TASK}  ", org)
    assert isinstance(result, RunResult)
    assert result.task == TASK
    assert (result.division, result.team) == ("operations", "security_team")
    assert result.ceo_reason == "scripted choice operations"
    assert result.trace[0] == "CEO → operations" and len(result.trace) == 5
    assert result.final_answer and result.latency_s >= 0
    assert result.llm_calls == 0  # fakes bypass callbacks; counting is covered below and in G4
    assert result.to_dict()["trace"] == list(result.trace)


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_at19_empty_task_rejected_before_any_llm_call(bad: Any, agent_log: list) -> None:
    org, model = make_org(SECURITY_SCRIPT, agent_log)
    with pytest.raises(ValueError):
        run_company(bad, org)
    assert model.prompts == []


def test_llm_call_counter_counts_model_calls() -> None:
    counter = LLMCallCounter()
    model = FakeListChatModel(responses=["a", "b"])
    model.invoke("hi", config={"callbacks": [counter]})
    model.invoke("hi again", config={"callbacks": [counter]})
    assert counter.calls == 2
    counter.on_llm_start({}, ["x"])
    assert counter.calls == 3


def test_message_text_handles_str_and_blocks() -> None:
    assert message_text(AIMessage(content="plain")) == "plain"
    assert message_text(AIMessage(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])) == "ab"


# ── AT-13 CLI ───────────────────────────────────────────────────────
def test_at13_cli_prints_trace_and_answer(agent_log: list, capsys: pytest.CaptureFixture[str]) -> None:
    org, _ = make_org(SECURITY_SCRIPT, agent_log)
    assert cli.main([TASK], org=org) == 0
    out = capsys.readouterr().out
    assert "1. CEO → operations" in out and "Security Supervisor → FINISH" in out and "fake reply" in out


def test_at13_cli_json_and_graph(agent_log: list, capsys: pytest.CaptureFixture[str]) -> None:
    org, _ = make_org(SECURITY_SCRIPT, agent_log)
    assert cli.main([TASK, "--json"], org=org) == 0
    assert json.loads(capsys.readouterr().out)["division"] == "operations"

    assert cli.main(["--graph"], org=org) == 0
    out = capsys.readouterr().out
    assert "%% Company (L1)" in out and "%% Team: security_team (L3)" in out and "graph TD" in out


def test_at13_cli_errors(agent_log: list, capsys: pytest.CaptureFixture[str]) -> None:
    org, _ = make_org(SECURITY_SCRIPT, agent_log)
    with pytest.raises(SystemExit):
        cli.main([], org=org)
    assert cli.main(["   "], org=org) == 2

    class Boom:
        def stream(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("secret-bearing message")

    broken = type(org)(org.org, org.settings, Boom(), org.director_graphs, org.team_graphs)  # type: ignore[arg-type]
    assert cli.main([TASK], org=broken) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "secret-bearing" not in err


# ── AT-17 config ────────────────────────────────────────────────────
def test_at17_defaults() -> None:
    s = Settings.from_env({})
    assert (s.model, s.temperature, s.max_specialist_turns, s.graph_recursion_limit) == ("gpt-4.1-mini", 0.0, 3, 50)


def test_at17_env_overrides() -> None:
    s = Settings.from_env({"HC_MODEL": "gpt-4.1-nano", "HC_TEMPERATURE": "0.2", "HC_MAX_SPECIALIST_TURNS": "2",
                           "HC_SPECIALIST_RECURSION_LIMIT": "8", "HC_GRAPH_RECURSION_LIMIT": "40",
                           "HC_REQUEST_TIMEOUT": "30", "HC_MAX_RETRIES": "4"})
    assert s == Settings("gpt-4.1-nano", 0.2, 2, 8, 40, 30.0, 4)


def test_at17_reads_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_MAX_SPECIALIST_TURNS", "2")
    assert Settings.from_env().max_specialist_turns == 2


@pytest.mark.parametrize("env", [
    {"HC_REQUEST_TIMEOUT": "0"},
    {"HC_MAX_RETRIES": "-1"},
    {"HC_MAX_SPECIALIST_TURNS": "zero"},
    {"HC_MAX_SPECIALIST_TURNS": "0"},
    {"HC_TEMPERATURE": "5"},
    {"HC_MODEL": " "},
    {"HC_SPECIALIST_RECURSION_LIMIT": "1"},
    {"HC_GRAPH_RECURSION_LIMIT": "5"},
])
def test_at17_invalid_values_rejected(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Settings.from_env(env)


def test_at17_get_model_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    model = get_model(Settings(model="gpt-4.1-nano", temperature=0.1))
    assert model.model_name == "gpt-4.1-nano"  # type: ignore[attr-defined]
    assert model.temperature == 0.1  # type: ignore[attr-defined]


def test_at23_get_model_sets_timeout_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    model = get_model(Settings(request_timeout=15.0, max_retries=5))
    assert model.request_timeout == 15.0  # type: ignore[attr-defined]
    assert model.max_retries == 5  # type: ignore[attr-defined]


def test_at25_cli_attach_includes_file_contents(agent_log: list, tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\nUSER app\n")
    attached = cli.with_attachments("Review", [str(dockerfile)])
    assert "--- Attached file: Dockerfile ---\nFROM python:3.12-slim" in attached

    org, model = make_org(SECURITY_SCRIPT, agent_log)
    assert cli.main([TASK, "--attach", str(dockerfile)], org=org) == 0
    assert "FROM python:3.12-slim" in model.prompts_for("CEODecision")[0]
    assert cli.main([TASK, "--attach", str(tmp_path / "missing")], org=org) == 2
    assert "attachment not found" in capsys.readouterr().err
