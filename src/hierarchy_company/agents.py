"""Level-4 specialist agent factory (FR-03)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from hierarchy_company.prompts import specialist_system_prompt
from hierarchy_company.registry import SpecialistSpec


class Agent(Protocol):
    def invoke(self, input: dict[str, Any], config: Any = None, **kwargs: Any) -> dict[str, Any]: ...


AgentFactory = Callable[[SpecialistSpec, BaseChatModel], Agent]


def make_specialist(spec: SpecialistSpec, model: BaseChatModel) -> Agent:
    agent = create_agent(model, tools=list(spec.tools), system_prompt=specialist_system_prompt(spec.name, spec.role))
    return cast(Agent, agent)
