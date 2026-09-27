"""Console entry point: `hierarchy-company "<task>"` (FR-13)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from hierarchy_company.factory import Organization, build_company
from hierarchy_company.runner import RunResult, append_attachments, run_company


def render(result: RunResult) -> str:
    lines = [f"Task: {result.task}", f"Division: {result.division}   Team: {result.team}", "", "Trace:"]
    lines += [f"  {i}. {step}" for i, step in enumerate(result.trace, 1)]
    lines += ["", f"LLM calls: {result.llm_calls}   Latency: {result.latency_s}s", "", result.final_answer]
    return "\n".join(lines)


def with_attachments(task: str, paths: Sequence[str]) -> str:
    """Read files from disk and append them to the task (FR-18)."""
    files = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            raise ValueError(f"attachment not found: {p}")
        files.append((path.name, path.read_bytes()))
    return append_attachments(task, files)


def render_graphs(org: Organization) -> str:
    parts = [("Company (L1)", org.company_graph)]
    parts += [(f"Director: {k} (L2)", g) for k, g in org.director_graphs.items()]
    parts += [(f"Team: {k} (L3)", g) for k, g in org.team_graphs.items()]
    return "\n\n".join(f"%% {title}\n{g.get_graph().draw_mermaid()}" for title, g in parts)


def main(argv: Sequence[str] | None = None, org: Organization | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hierarchy-company", description=__doc__)
    parser.add_argument("task", nargs="?", help="plain-English task for the company")
    parser.add_argument("--graph", action="store_true", help="print Mermaid diagrams for all graphs and exit")
    parser.add_argument("--json", action="store_true", help="print the RunResult as JSON")
    parser.add_argument("--attach", action="append", default=[], metavar="FILE",
                        help="attach a file (Dockerfile, SQL, policy JSON, ...) to the task; repeatable")
    args = parser.parse_args(argv)

    if not args.graph and not args.task:
        parser.error("a task is required unless --graph is given")
    try:
        org = org or build_company()
        if args.graph:
            print(render_graphs(org))
            return 0
        result = run_company(with_attachments(args.task, args.attach), org)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # e.g. openai.AuthenticationError: never echo the key, only the error type
        print(f"error: run failed ({type(exc).__name__}). Check OPENAI_API_KEY and network.", file=sys.stderr)
        return 1
    print(json.dumps(result.to_dict(), indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
