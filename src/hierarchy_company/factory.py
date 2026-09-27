"""Composition root: builds all 9 graphs from the registry (NFR-01, ADR-02, ADR-03)."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from hierarchy_company.agents import AgentFactory, make_specialist
from hierarchy_company.config import Settings
from hierarchy_company.graphs import build_company_graph, build_director_graph, build_team_graph
from hierarchy_company.llm import get_model
from hierarchy_company.registry import ORG, Org


@dataclass(frozen=True)
class Organization:
    org: Org
    settings: Settings
    company_graph: CompiledStateGraph
    director_graphs: dict[str, CompiledStateGraph]
    team_graphs: dict[str, CompiledStateGraph]


def build_company(model: BaseChatModel | None = None, settings: Settings | None = None,
                  agent_factory: AgentFactory = make_specialist, org: Org = ORG) -> Organization:
    """Build L3 teams → L2 directors → L1 company. Pass a fake `model`/`agent_factory` to test offline."""
    settings = settings or Settings.from_env()
    model = model or get_model(settings)

    team_graphs = {t.key: build_team_graph(t, model, settings, agent_factory) for t in org.teams}
    director_graphs = {
        d.key: build_director_graph(d, {k: team_graphs[k] for k in d.team_keys}, model) for d in org.divisions
    }
    company_graph = build_company_graph(org, director_graphs, model)
    return Organization(org, settings, company_graph, director_graphs, team_graphs)
