"""AT-01 org shape · AT-02 tools documented · AT-26 no network on invalid input, never raises."""

from __future__ import annotations

import socket

import pytest

from hierarchy_company.registry import ORG

ALL_SPECIALISTS = [s for t in ORG.teams for s in t.specialists]
ALL_TOOLS = [tool for s in ALL_SPECIALISTS for tool in s.tools]

EXPECTED = {
    "engineering": {
        "frontend_team": ["React Agent", "UI Agent", "Accessibility Agent"],
        "backend_team": ["API Agent", "Auth Agent", "Microservice Agent"],
        "database_team": ["SQL Agent", "Schema Agent", "Query Optimization Agent"],
    },
    "operations": {
        "qa_team": ["Unit Test Agent", "Integration Test Agent", "Regression Agent"],
        "devops_team": ["Docker Agent", "Kubernetes Agent", "CI/CD Agent"],
        "security_team": ["Vulnerability Agent", "IAM Agent", "Secrets Agent"],
    },
}


def test_at01_org_shape_matches_spec() -> None:
    actual = {d.key: {t.key: t.specialist_names for t in d.teams} for d in ORG.divisions}
    assert actual == EXPECTED
    assert (len(ORG.divisions), len(ORG.teams), len(ALL_SPECIALISTS)) == (2, 6, 18)


def test_at01_lookups() -> None:
    assert ORG.division_of("security_team") == "operations"
    assert ORG.team("qa_team").supervisor_label == "QA Supervisor"
    assert ORG.division("engineering").director_label == "Engineering Director"


def test_at02_each_specialist_has_two_documented_tools() -> None:
    for spec in ALL_SPECIALISTS:
        assert len(spec.tools) == 2, spec.name
        for t in spec.tools:
            assert len(t.description.strip()) > 40, t.name


def test_at02_tool_names_unique() -> None:
    names = [t.name for t in ALL_TOOLS]
    assert len(names) == len(set(names)) == 36


def test_at02_no_mock_modules_or_canned_markers() -> None:
    import inspect
    import re

    from hierarchy_company import tools

    for module in (tools.frontend, tools.backend, tools.database, tools.qa, tools.devops, tools.security):
        src = inspect.getsource(module).lower()
        assert not re.search(r"\b(mock|canned|fake|dummy)\b", src), module.__name__


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_at26_junk_input_never_raises_or_touches_network(tool, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args, **kwargs):
        raise AssertionError("tool attempted a network connection on invalid input")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.delenv("HC_DATABASE_URL", raising=False)
    schema = tool.args_schema.model_json_schema()
    args = {k: "sample" for k in schema.get("required", [])}
    out = tool.invoke(args)
    assert isinstance(out, str) and out.strip()
