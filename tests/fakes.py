"""Offline test doubles: a scripted chat model and fake specialist agents (NFR-01, ADR-02)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from hierarchy_company.registry import SpecialistSpec
from hierarchy_company.routing import options_of


class FakeRouter:
    def __init__(self, parent: FakeChatModel, schema: type[BaseModel]) -> None:
        self.parent, self.schema = parent, schema

    def invoke(self, prompt: Any, config: Any = None, **kwargs: Any) -> BaseModel:
        name = self.schema.__name__
        self.parent.prompts.append((name, str(prompt)))
        self.parent.offered.append(options_of(self.schema))
        queue = self.parent.script.get(name)
        choice = queue.pop(0) if queue else options_of(self.schema)[0]  # default: first option, forever
        return self.schema(choice=choice, reason=f"scripted choice {choice}")


class FakeChatModel:
    """Structured decisions come from `script[schema_name]` (FIFO). Text calls return a numbered reply."""

    def __init__(self, script: dict[str, list[str]] | None = None, reply: str = "fake reply") -> None:
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.reply = reply
        self.prompts: list[tuple[str, str]] = []
        self.offered: list[tuple[str, ...]] = []  # options each structured decision was allowed to pick

    def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> FakeRouter:
        return FakeRouter(self, schema)

    def invoke(self, prompt: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        self.prompts.append(("text", str(prompt)))
        return AIMessage(content=f"{self.reply} #{len(self.prompts)}")

    def prompts_for(self, kind: str) -> list[str]:
        return [p for k, p in self.prompts if k == kind]


class FakeAgent:
    def __init__(self, name: str, log: list[tuple[str, Any]]) -> None:
        self.name, self.log = name, log

    def invoke(self, input: dict[str, Any], config: Any = None, **kwargs: Any) -> dict[str, Any]:
        self.log.append((self.name, config))
        return {"messages": [AIMessage(content=f"{self.name} answer")]}


class LoopingAgent(FakeAgent):
    """Simulates a specialist whose tool loop never converges."""

    def invoke(self, input: dict[str, Any], config: Any = None, **kwargs: Any) -> dict[str, Any]:
        self.log.append((self.name, config))
        raise GraphRecursionError("Recursion limit reached")


def fake_agent_factory(log: list[tuple[str, Any]], cls: type[FakeAgent] = FakeAgent
                       ) -> Callable[[SpecialistSpec, Any], FakeAgent]:
    return lambda spec, model: cls(spec.name, log)
