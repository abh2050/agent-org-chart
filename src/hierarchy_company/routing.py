"""Structured routing decisions (FR-04, FR-07, FR-09, ADR-05)."""

from __future__ import annotations

import re
from typing import Literal, get_args

from pydantic import BaseModel, Field, create_model

FINISH = "FINISH"


def make_route_schema(schema_name: str, options: list[str]) -> type[BaseModel]:
    """Create a decision model `{choice: Literal[*options], reason: str}`.

    Out-of-set choices fail Pydantic validation, and OpenAI structured output receives an enum.
    """
    if not options or len(set(options)) != len(options):
        raise ValueError(f"{schema_name}: options must be non-empty and unique, got {options}")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", schema_name):
        raise ValueError(f"{schema_name!r} is not a valid schema name")
    # `reason` comes first so the model states its reasoning before committing to a choice.
    return create_model(
        schema_name,
        reason=(str, Field(description="One line: what the task still needs, then why this option")),
        choice=(Literal[tuple(options)], Field(description="The selected option")),
    )


def options_of(schema: type[BaseModel]) -> tuple[str, ...]:
    """Return the allowed choices of a schema built by `make_route_schema`."""
    return get_args(schema.model_fields["choice"].annotation)


def node_id(name: str) -> str:
    """'CI/CD Agent' -> 'ci_cd_agent' (safe graph node name)."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
