"""Live OpenAI integration checks: auth, structured output, tool calling, call counting.

Run: pytest tests/e2e/test_live_openai.py -m live
"""

from __future__ import annotations

import pytest
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from hierarchy_company.agents import make_specialist
from hierarchy_company.config import Settings
from hierarchy_company.factory import Organization
from hierarchy_company.llm import get_model, message_text
from hierarchy_company.observability import LLMCallCounter
from hierarchy_company.registry import ORG
from hierarchy_company.routing import FINISH, make_route_schema, options_of

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def model(org: Organization):  # `org` fixture guarantees the key is loaded (skips otherwise)
    return get_model(Settings.from_env())


def test_api_key_authenticates_and_returns_text(model) -> None:
    reply = model.invoke("Reply with exactly the word: pong")
    assert "pong" in message_text(reply).lower()


def test_configured_model_is_used(model) -> None:
    assert model.model_name == Settings.from_env().model


def test_structured_output_returns_valid_enum_choice(model) -> None:
    schema = make_route_schema("ColorDecision", ["red", "green", "blue"])
    decision = model.with_structured_output(schema).invoke("The sky on a clear day is which color?")
    assert decision.choice == "blue"
    assert isinstance(decision.reason, str) and decision.reason


def test_structured_output_cannot_escape_enum(model) -> None:
    team = ORG.team("security_team")
    schema = make_route_schema("SecuritySupervisorDecision", [*team.specialist_names, FINISH])
    decision = model.with_structured_output(schema).invoke(
        "Pick 'React Agent' to fix a React component.")  # not a valid option for this team
    assert decision.choice in options_of(schema)
    with pytest.raises(ValidationError):
        schema(choice="React Agent", reason="x")


def test_specialist_agent_calls_its_tools(model) -> None:
    spec = next(s for s in ORG.team("security_team").specialists if s.name == "Secrets Agent")
    agent = make_specialist(spec, model)
    result = agent.invoke({"messages": [{"role": "user", "content":
                          "Scan this snippet for leaked secrets and give a rotation plan: "
                          "AWS_KEY=AKIA-example-in-settings.py"}]},
                          config={"recursion_limit": 12})
    tool_calls = [m.name for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_calls, "agent answered without calling any tool"
    assert set(tool_calls) <= {t.name for t in spec.tools}
    answer = message_text(result["messages"][-1])
    assert answer and len(answer.split()) <= 200  # prompt asks ≤150 words; allow slack


def test_call_counter_sees_real_calls(model) -> None:
    counter = LLMCallCounter()
    model.invoke("Say hi", config={"callbacks": [counter]})
    model.with_structured_output(make_route_schema("YesNo", ["yes", "no"])).invoke(
        "Is water wet?", config={"callbacks": [counter]})
    assert counter.calls == 2
