"""QA team tools: Python AST test generation, coverage/JUnit report parsing, git change analysis (local)."""

from __future__ import annotations

import ast
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from langchain_core.tools import tool

from hierarchy_company.tools._common import needs_input, report, unavailable

_REF = re.compile(r"^[\w./~^@{}-]{1,100}$")


# ── Unit Test Agent ──────────────────────────────────────────────────
@tool
def generate_unit_test_skeleton(source: str) -> str:
    """Parse Python source with the ast module and generate a pytest skeleton: one happy-path test per public
    function/method, plus a pytest.raises test for every exception type the function raises explicitly."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return needs_input("generate_unit_test_skeleton", f"valid Python source (syntax error line {exc.lineno})")
    targets: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_"):
            targets.append((node.name, node))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            targets += [(f"{node.name}.{m.name}", m) for m in node.body
                        if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef) and not m.name.startswith("_")]
    if not targets:
        return "No public functions or methods found to test."
    out = ["import pytest", ""]
    for qual, fn in targets:
        slug = qual.replace(".", "_").lower()
        params = [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]
        prefix = "async " if isinstance(fn, ast.AsyncFunctionDef) else ""
        mark = "@pytest.mark.asyncio\n" if prefix else ""
        out += [f"{mark}{prefix}def test_{slug}_happy_path():",
                f"    # arrange: {', '.join(params) or 'no arguments'}",
                f"    # act: call {qual}(...)", "    # assert: expected result", "    ...", ""]
        raised = sorted({(r.exc.func.id if isinstance(r.exc, ast.Call) and isinstance(r.exc.func, ast.Name)
                          else r.exc.id if isinstance(r.exc, ast.Name) else "Exception")
                         for r in ast.walk(fn) if isinstance(r, ast.Raise) and r.exc is not None})
        for exc_name in raised:
            out += [f"{mark}{prefix}def test_{slug}_raises_{exc_name.lower()}():",
                    f"    with pytest.raises({exc_name}):", f"        ...  # call {qual} with input that triggers it", ""]
    return f"pytest skeleton for {len(targets)} target(s):\n" + "\n".join(out)


@tool
def measure_code_coverage(coverage_xml_path: str) -> str:
    """Read a Cobertura coverage report (e.g. produced by `coverage xml`) from a file path and summarize
    line/branch coverage overall and for the least-covered files."""
    path = Path(coverage_xml_path).expanduser()
    if not path.is_file():
        return unavailable("measure_code_coverage", f"no coverage report at '{coverage_xml_path}' "
                           "(generate one with `pytest --cov --cov-report=xml`)")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return needs_input("measure_code_coverage", f"a valid Cobertura XML file ({exc})")
    line_rate = float(root.get("line-rate", 0)) * 100
    branch_rate = float(root.get("branch-rate", 0)) * 100
    files = sorted((float(c.get("line-rate", 0)) * 100, c.get("filename", "?")) for c in root.iter("class"))
    worst = "\n".join(f"- {name}: {rate:.0f}%" for rate, name in files[:5])
    return f"Coverage: lines {line_rate:.1f}%, branches {branch_rate:.1f}%, {len(files)} file(s).\nLeast covered:\n{worst}"


# ── Integration Test Agent ───────────────────────────────────────────
_INTEGRATION_PATTERNS = {
    "HTTP call": r"\b(requests|httpx|aiohttp|urllib\.request)\.\w*(get|post|put|patch|delete|request|urlopen)\w*\(",
    "AWS SDK": r"boto3\.(client|resource)\(\s*['\"](\w+)",
    "SQL database": r"\b(psycopg2?|sqlite3|pymysql|asyncpg)\.connect\(|create_engine\(",
    "Redis": r"\bredis\.(Redis|StrictRedis|from_url)\(",
    "Message queue": r"\b(KafkaProducer|KafkaConsumer|pika\.BlockingConnection|Celery)\(",
    "Subprocess": r"\bsubprocess\.(run|Popen|call|check_output)\(",
    "Environment config": r"os\.(environ|getenv)\b[^\n]*(URL|HOST|DSN|ENDPOINT)",
}


@tool
def list_integration_points(source: str) -> str:
    """Scan source code for integration points that need integration tests or stubs: outbound HTTP calls,
    AWS SDK clients, database connections, Redis, message queues, subprocesses and URL/host configuration."""
    if len(source.strip()) < 10:
        return needs_input("list_integration_points", "source code to scan")
    findings: list[str] = []
    for kind, pattern in _INTEGRATION_PATTERNS.items():
        for m in re.finditer(pattern, source):
            line = source.count("\n", 0, m.start()) + 1
            snippet = source.splitlines()[line - 1].strip()[:100]
            findings.append(f"{kind} (line {line}): {snippet}")
    return report("Integration points", findings) if findings else "Integration points: none detected."


@tool
def create_test_fixture(service: str, endpoints: str) -> str:
    """Generate a pytest fixture that stubs an HTTP dependency with httpx.MockTransport. `endpoints` is a
    comma-separated list like 'GET /charges, POST /refunds'; each gets a success and an error scenario."""
    routes = [(m.upper(), p) for m, p in re.findall(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[\w/{}.-]*)", endpoints, re.I)]
    if not service.strip() or not routes:
        return needs_input("create_test_fixture", "a service name and endpoints like 'GET /charges, POST /refunds'")
    name = re.sub(r"\W+", "_", service.strip().lower())
    cases = "\n".join(f'        ("{m}", "{p}"): httpx.Response(200 if scenario == "ok" else 503, json={{}}),'
                      for m, p in routes)
    return f'''import httpx
import pytest


@pytest.fixture(params=["ok", "unavailable"])
def {name}_client(request):
    """Stubbed {service} client; parametrized over success and 503 scenarios."""
    scenario = request.param
    routes = {{
{cases}
    }}

    def handler(req: httpx.Request) -> httpx.Response:
        return routes.get((req.method, req.url.path), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://{name}.test")'''


# ── Regression Agent ─────────────────────────────────────────────────
@tool
def find_changed_modules(repo_path: str, since_ref: str = "HEAD~10") -> str:
    """List files changed in a local git repository between `since_ref` and HEAD with lines added/removed,
    rated HIGH (>200 lines churn), MEDIUM (>50) or LOW, to prioritize regression testing."""
    if not _REF.match(since_ref) or since_ref.startswith("-"):
        return needs_input("find_changed_modules", "a valid git ref such as 'v1.4.0' or 'HEAD~10'")
    repo = Path(repo_path).expanduser()
    if not (repo / ".git").exists():
        return unavailable("find_changed_modules", f"'{repo_path}' is not a git repository")
    try:
        proc = subprocess.run(["git", "-C", str(repo), "diff", "--numstat", f"{since_ref}..HEAD"],
                              capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return unavailable("find_changed_modules", f"git failed ({type(exc).__name__})")
    if proc.returncode != 0:
        return unavailable("find_changed_modules", proc.stderr.strip().splitlines()[0] if proc.stderr else "git error")
    rows = []
    for line in proc.stdout.splitlines():
        added, removed, path = line.split("\t", 2)
        churn = (int(added) if added.isdigit() else 0) + (int(removed) if removed.isdigit() else 0)
        risk = "HIGH" if churn > 200 else "MEDIUM" if churn > 50 else "LOW"
        rows.append((churn, f"{risk:<6} {path} (+{added}/-{removed})"))
    if not rows:
        return f"No changes between {since_ref} and HEAD."
    rows.sort(reverse=True)
    return f"{len(rows)} file(s) changed since {since_ref}:\n" + "\n".join(r for _, r in rows[:25])


@tool
def get_flaky_test_history(junit_dir: str) -> str:
    """Parse every JUnit XML report in a directory (one file per CI run) and identify flaky tests, i.e. tests
    that both passed and failed across runs, with their failure rate."""
    folder = Path(junit_dir).expanduser()
    files = sorted(folder.glob("**/*.xml")) if folder.is_dir() else []
    if not files:
        return unavailable("get_flaky_test_history", f"no JUnit XML reports found in '{junit_dir}'")
    outcomes: dict[str, list[bool]] = defaultdict(list)
    for f in files:
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            if case.find("skipped") is not None:
                continue
            failed = case.find("failure") is not None or case.find("error") is not None
            outcomes[f"{case.get('classname', '')}::{case.get('name', '')}"].append(not failed)
    flaky = sorted(((results.count(False) / len(results), name, results) for name, results in outcomes.items()
                    if True in results and False in results), reverse=True)
    lines = [f"{name}: failed {r.count(False)}/{len(r)} runs ({rate:.0%})" for rate, name, r in flaky]
    return report("Flaky tests", lines, f"{len(files)} report(s), {len(outcomes)} test(s).")
