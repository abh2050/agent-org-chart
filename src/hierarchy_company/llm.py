"""The single place where a chat model is created (NFR-01: nothing builds an LLM at import time)."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from hierarchy_company.config import Settings, load_env


def get_model(settings: Settings | None = None, api_key: str | None = None) -> BaseChatModel:
    """`api_key` overrides OPENAI_API_KEY (used by the UI for a key pasted into one browser session)."""
    from langchain_openai import ChatOpenAI

    load_env()
    s = settings or Settings.from_env()
    extra: dict[str, Any] = {"api_key": api_key} if api_key else {}
    return ChatOpenAI(model=s.model, temperature=s.temperature, timeout=s.request_timeout, max_retries=s.max_retries,
                      **extra)


def message_text(message: Any) -> str:
    """Return the text of an AIMessage (content may be a str or a list of content blocks)."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
