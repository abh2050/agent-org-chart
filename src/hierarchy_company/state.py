"""State contracts for each level (FR-10). `trace` uses the same reducer at every level."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class TeamState(TypedDict):
    task: str
    specialist_notes: Annotated[list[str], operator.add]
    next_specialist: str
    turns: int
    team_output: str
    consulted: Annotated[list[str], operator.add]  # FR-15: specialists already called this run
    trace: Annotated[list[str], operator.add]


class DirectorState(TypedDict):
    task: str
    selected_team: str
    reason: str
    team_output: str
    division_output: str
    trace: Annotated[list[str], operator.add]


class CompanyState(TypedDict):
    task: str
    selected_division: str
    ceo_reason: str
    selected_team: str
    division_output: str
    trace: Annotated[list[str], operator.add]
    final_answer: str


# Children always start with an empty trace, so the parent appends only their new entries.
def initial_team_state(task: str) -> TeamState:
    return TeamState(task=task, specialist_notes=[], next_specialist="", turns=0, team_output="",
                     consulted=[], trace=[])


def initial_director_state(task: str) -> DirectorState:
    return DirectorState(task=task, selected_team="", reason="", team_output="", division_output="", trace=[])


def initial_company_state(task: str) -> CompanyState:
    return CompanyState(task=task, selected_division="", ceo_reason="", selected_team="",
                        division_output="", trace=[], final_answer="")
