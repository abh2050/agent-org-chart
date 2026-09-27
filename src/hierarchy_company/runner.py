"""Public entry points: stream a task through the company as events (FR-19), or run it to a RunResult (FR-12)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, ToolMessage

from hierarchy_company.factory import Organization, build_company
from hierarchy_company.llm import message_text
from hierarchy_company.observability import LLMCallCounter
from hierarchy_company.state import initial_company_state

EventKind = Literal["route", "tool_call", "tool_result", "note", "team_output", "division_output", "final"]


@dataclass(frozen=True)
class RunEvent:
    kind: EventKind
    actor: str   # who produced it: "CEO", "Security Supervisor", "IAM Agent", "security_team", ...
    text: str


@dataclass(frozen=True)
class RunResult:
    task: str
    division: str
    team: str
    ceo_reason: str
    trace: tuple[str, ...]
    final_answer: str
    llm_calls: int
    latency_s: float

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "trace": list(self.trace)}


MAX_ATTACHMENT_BYTES = 50_000


def append_attachments(task: str, files: list[tuple[str, bytes]]) -> str:
    """Append file contents to a task so specialists analyze real artifacts instead of inventing them (FR-18)."""
    parts = [task]
    for name, data in files:
        text = data[:MAX_ATTACHMENT_BYTES].decode("utf-8", errors="replace")
        note = " (truncated to 50 KB)" if len(data) > MAX_ATTACHMENT_BYTES else ""
        parts.append(f"\n--- Attached file: {name}{note} ---\n{text}\n--- end of {name} ---")
    return "\n".join(parts)


def _validate(task: Any) -> str:
    if not isinstance(task, str) or not task.strip():  # NFR-06: reject before any LLM call
        raise ValueError("task must be a non-empty string")
    return task.strip()


def _specialist_name(namespace: tuple[str, ...], org: Organization) -> str:
    """Map a stream namespace like (..., 'iam_agent:<id>') back to 'IAM Agent'."""
    from hierarchy_company.routing import node_id

    last = namespace[-1].split(":")[0] if namespace else ""
    for team in org.org.teams:
        for spec in team.specialists:
            if node_id(spec.name) == last:
                return spec.name
    return last


def _events(node: str, update: dict[str, Any], namespace: tuple[str, ...], org: Organization) -> Iterator[RunEvent]:
    # Wrapper nodes (call_*) re-send their child's trace; the child already emitted it live.
    if not node.startswith("call_"):
        for entry in update.get("trace") or []:
            yield RunEvent("route", entry.split(" → ")[0], entry)
    for note in update.get("specialist_notes") or []:
        name, _, body = note.partition("] ")
        yield RunEvent("note", name.lstrip("["), body)
    if update.get("team_output") and node == "team_writer":
        yield RunEvent("team_output", "team writer", update["team_output"])
    if update.get("division_output") and node == "director_review":
        yield RunEvent("division_output", "director review", update["division_output"])
    if update.get("final_answer"):
        yield RunEvent("final", "CEO", update["final_answer"])
    for msg in update.get("messages") or []:  # inside a specialist agent (create_agent subgraph)
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                args = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in (call.get("args") or {}).items())
                yield RunEvent("tool_call", _specialist_name(namespace, org), f"{call['name']}({args})")
        elif isinstance(msg, ToolMessage):
            yield RunEvent("tool_result", msg.name or "tool", message_text(msg))


def stream_company(task: str, org: Organization | None = None) -> Iterator[RunEvent | RunResult]:
    """Yield RunEvents as the run progresses, then the final RunResult as the last item."""
    task = _validate(task)
    org = org or build_company()
    counter = LLMCallCounter()
    state: dict[str, Any] = initial_company_state(task)  # type: ignore[assignment]
    trace: list[str] = []

    start = time.perf_counter()
    stream = org.company_graph.stream(
        initial_company_state(task), stream_mode="updates", subgraphs=True,
        config={"recursion_limit": org.settings.graph_recursion_limit, "callbacks": [counter]},
    )
    for item in stream:
        namespace, chunk = cast(tuple[tuple[str, ...], dict[str, Any]], item)
        for node, update in (chunk or {}).items():
            if not isinstance(update, dict):
                continue
            if not namespace:  # top-level company state
                trace += update.get("trace") or []
                state.update({k: v for k, v in update.items() if k != "trace"})
            yield from _events(node, update, namespace, org)

    yield RunResult(
        task=task,
        division=state["selected_division"],
        team=state["selected_team"],
        ceo_reason=state["ceo_reason"],
        trace=tuple(trace),
        final_answer=state["final_answer"],
        llm_calls=counter.calls,
        latency_s=round(time.perf_counter() - start, 2),
    )


def run_company(task: str, org: Organization | None = None) -> RunResult:
    result: RunResult | None = None
    for item in stream_company(task, org):
        if isinstance(item, RunResult):
            result = item
    assert result is not None  # stream_company always ends with a RunResult
    return result
