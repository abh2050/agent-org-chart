"""Run telemetry via LangChain callbacks (NFR-03, ADR-06)."""

from __future__ import annotations

import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


class LLMCallCounter(BaseCallbackHandler):
    """Counts chat/LLM model calls across nested graphs and agents (callbacks propagate via config)."""

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def _bump(self) -> None:
        with self._lock:
            self.calls += 1

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        self._bump()

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        self._bump()
