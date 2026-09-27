"""Typed runtime settings, read from HC_* environment variables (NFR-04)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields

from dotenv import load_dotenv


def load_env() -> None:
    """Load `.env` once, unless disabled (tests and offline gates set HC_DISABLE_DOTENV=1)."""
    if not os.environ.get("HC_DISABLE_DOTENV"):
        load_dotenv()


@dataclass(frozen=True)
class Settings:
    model: str = "gpt-4.1-mini"
    temperature: float = 0.0
    max_specialist_turns: int = 3        # FR-05: hard cap on specialist calls per team
    specialist_recursion_limit: int = 12  # FR-03: caps a specialist's internal tool loop
    graph_recursion_limit: int = 50       # NFR-02: parent graph invocation limit
    request_timeout: float = 60.0         # NFR-08: seconds per OpenAI request
    max_retries: int = 2                  # NFR-08: client retries (with backoff) on 429/5xx/timeouts

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must be non-empty")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be in [0, 2]")
        if not 1 <= self.max_specialist_turns <= 10:
            raise ValueError("max_specialist_turns must be in [1, 10]")
        if self.specialist_recursion_limit < 3:
            raise ValueError("specialist_recursion_limit must be >= 3")
        if self.graph_recursion_limit < 10:
            raise ValueError("graph_recursion_limit must be >= 10")
        if not 1.0 <= self.request_timeout <= 600.0:
            raise ValueError("request_timeout must be in [1, 600] seconds")
        if not 0 <= self.max_retries <= 10:
            raise ValueError("max_retries must be in [0, 10]")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from HC_<FIELD> variables, e.g. HC_MODEL, HC_MAX_SPECIALIST_TURNS."""
        if env is None:
            load_env()
            env = os.environ
        overrides: dict[str, object] = {}
        for f in fields(cls):
            raw = env.get(f"HC_{f.name.upper()}")
            if raw is None or raw == "":
                continue
            caster = {"int": int, "float": float}.get(str(f.type), str)
            try:
                overrides[f.name] = caster(raw)
            except ValueError as exc:
                raise ValueError(f"HC_{f.name.upper()}={raw!r} is not a valid {f.type}") from exc
        return cls(**overrides)  # type: ignore[arg-type]
