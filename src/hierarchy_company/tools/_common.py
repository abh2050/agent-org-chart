"""Shared helpers for real tools (FR-17, NFR-09): honest unavailability, bounded HTTP, parsing, formatting."""

from __future__ import annotations

from typing import Any

import httpx
import yaml

HTTP_TIMEOUT_S = 10.0
USER_AGENT = "hierarchy-company/2.0"


class ToolUnavailable(Exception):
    """Raised inside a tool when its data source cannot be reached or is not configured."""


def unavailable(tool: str, reason: str) -> str:
    return f"UNAVAILABLE ({tool}): {reason}. No result was produced; do not guess one."


def http_get_json(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    try:
        r = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT, **(headers or {})},
                      timeout=HTTP_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise ToolUnavailable(f"request to {url} failed ({type(exc).__name__})") from exc
    if r.status_code == 404:
        raise ToolUnavailable(f"not found at {url}")
    if r.status_code >= 400:
        raise ToolUnavailable(f"{url} returned HTTP {r.status_code}")
    return r.json()


def http_post_json(url: str, payload: dict[str, Any]) -> Any:
    try:
        r = httpx.post(url, json=payload, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise ToolUnavailable(f"request to {url} failed ({type(exc).__name__})") from exc
    if r.status_code >= 400:
        raise ToolUnavailable(f"{url} returned HTTP {r.status_code}")
    return r.json()


def load_yaml_docs(text: str) -> list[Any]:
    """Parse one or more YAML (or JSON) documents; raises ValueError with a readable message."""
    try:
        return [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as exc:
        raise ValueError(f"could not parse YAML/JSON: {str(exc).splitlines()[0]}") from exc


def report(title: str, findings: list[str], summary: str = "") -> str:
    head = f"{title}: " + (summary + " " if summary else "")
    if not findings:
        return head + "no issues found."
    return head + f"{len(findings)} finding(s):\n" + "\n".join(f"{i}. {f}" for i, f in enumerate(findings, 1))


def needs_input(tool: str, what: str) -> str:
    return f"INPUT NEEDED ({tool}): {what}. Ask for the artifact instead of inventing one."
