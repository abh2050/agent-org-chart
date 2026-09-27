"""Prompt templates as pure functions (FR-03, FR-11). No LLM access here, so every prompt is unit-testable."""

from __future__ import annotations

from collections.abc import Sequence

from hierarchy_company.registry import DivisionSpec, Org, TeamSpec
from hierarchy_company.routing import FINISH

PRIMARY_RULE = "Choose exactly one. If the task spans several, pick the one that owns the primary deliverable."
SPECIALIST_WORD_LIMIT = 150
# FR-16 / ADR-09: tools only inspect and propose, so no output may claim an action was performed.
RECOMMENDATION_ONLY = (
    "Important: nothing has been executed. The specialists only inspected systems and proposed changes. "
    "Phrase every action as a recommendation or next step (e.g. 'Revoke the key'), never as already done "
    "(e.g. never 'the key has been revoked')."
)


def format_notes(notes: Sequence[str], empty: str = "(no notes yet)") -> str:
    return "\n\n".join(notes) or empty


# ── L4 ────────────────────────────────────────────────────────────────
def specialist_system_prompt(name: str, role: str) -> str:
    return (
        f"You are the {name}. Your scope: {role}.\n"
        "Stay inside your scope; if part of the task belongs to someone else, say so in one line.\n"
        "Use your tools whenever they are relevant and base your answer on their output.\n"
        "You cannot execute changes: your tools only inspect or propose. Never claim an action was performed.\n"
        "Only pass real data from the task or teammates' notes to your tools; never invent code, configs, keys, "
        "IDs or metrics to feed a tool. If a tool needs an artifact that was not provided, state exactly what "
        "is needed. If a tool returns UNAVAILABLE or INPUT NEEDED, report that plainly; do not guess its result.\n"
        f"Return a focused answer of at most {SPECIALIST_WORD_LIMIT} words."
    )


def specialist_request(task: str, notes: Sequence[str]) -> str:
    return f"Team task:\n{task}\n\nNotes from teammates so far:\n{format_notes(notes, '(none yet)')}"


# ── L3 ────────────────────────────────────────────────────────────────
def team_supervisor_prompt(team: TeamSpec, task: str, notes: Sequence[str], turns: int, max_turns: int,
                           available: Sequence[str] | None = None) -> str:
    """`available` = specialists not yet consulted (FR-15); defaults to the whole team."""
    names = set(team.specialist_names if available is None else available)
    options = "\n".join(f"- {s.name}: {s.role}" for s in team.specialists if s.name in names)
    consulted = [n for n in team.specialist_names if n not in names]
    return f"""You are the {team.supervisor_label}. Pick the next specialist to work on the task.

Options:
{options}
- {FINISH}: the notes already cover the task well enough to write the team report

Rules:
- {PRIMARY_RULE}
- Each specialist is consulted at most once. Already consulted: {", ".join(consulted) or "none"}.
- Call at least one specialist before choosing {FINISH}.
- Coverage test: consult another specialist only if the task explicitly asks for something in their scope
  that the notes do not cover yet. Adjacent or nice-to-have work (for example a CI pipeline when only a
  Dockerfile was requested) is not a reason. If nothing the task asked for is missing, choose {FINISH}.
- Specialist calls used: {turns} of {max_turns}.

Task:
{task}

Notes collected so far:
{format_notes(notes)}"""


def team_writer_prompt(team: TeamSpec, task: str, notes: Sequence[str]) -> str:
    return f"""You are the {team.label} team writer. Merge the specialist notes into a concise team report
(at most 200 words) that answers the task. Keep concrete findings and recommended actions; drop repetition.
{RECOMMENDATION_ONLY}

Task:
{task}

Specialist notes:
{format_notes(notes, '(no notes)')}"""


# ── L2 ────────────────────────────────────────────────────────────────
def director_prompt(division: DivisionSpec, task: str) -> str:
    options = "\n".join(f"- {t.key}: {t.description}" for t in division.teams)
    return f"""You are the {division.director_label}. Assign the task to one of your teams.

Teams:
{options}

{PRIMARY_RULE}

Task:
{task}"""


def director_review_prompt(division: DivisionSpec, task: str, team_key: str, team_output: str) -> str:
    return f"""You are the {division.director_label}. Turn the team report below into a division-level answer.
Keep the team's substance, then add at most 3 bullets of cross-team risks or next steps
(what other teams in the company must do).
{RECOMMENDATION_ONLY}

Task:
{task}

Report from {team_key}:
{team_output}"""


# ── L1 ────────────────────────────────────────────────────────────────
def ceo_prompt(org: Org, task: str) -> str:
    options = "\n".join(f"- {d.key}: {d.description}" for d in org.divisions)
    return f"""You are the CEO. Send the task to one division.

Divisions:
{options}

{PRIMARY_RULE}

Task:
{task}"""


def ceo_final_prompt(task: str, division: str, division_output: str, trace: Sequence[str]) -> str:
    routing = "\n".join(trace)
    return f"""You are the CEO. Write the final executive answer in Markdown with exactly these sections:

## Summary
2–3 sentences answering the task.

## Details
The key findings and actions from the division report, as short bullets.

## Routing Path
Reproduce the routing path below as a numbered list, unchanged.

{RECOMMENDATION_ONLY}

Task:
{task}

Division report ({division}):
{division_output}

Routing path:
{routing}"""
