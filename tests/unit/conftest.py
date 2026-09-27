from __future__ import annotations

from typing import Any

import pytest

from hierarchy_company.config import Settings
from hierarchy_company.factory import Organization, build_company
from tests.fakes import FakeChatModel, fake_agent_factory

SECURITY_SCRIPT = {
    "CEODecision": ["operations"],
    "OperationsDirectorDecision": ["security_team"],
    "SecuritySupervisorDecision": ["Secrets Agent", "IAM Agent", "FINISH"],
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_DISABLE_DOTENV", "1")
    for var in ("HC_MODEL", "HC_TEMPERATURE", "HC_MAX_SPECIALIST_TURNS",
                "HC_SPECIALIST_RECURSION_LIMIT", "HC_GRAPH_RECURSION_LIMIT", "HC_REQUEST_TIMEOUT", "HC_MAX_RETRIES"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def agent_log() -> list[tuple[str, Any]]:
    return []


def make_org(script: dict[str, list[str]], log: list[tuple[str, Any]],
             settings: Settings | None = None) -> tuple[Organization, FakeChatModel]:
    model = FakeChatModel(script)
    org = build_company(model=model, settings=settings or Settings(),  # type: ignore[arg-type]
                        agent_factory=fake_agent_factory(log))
    return org, model
