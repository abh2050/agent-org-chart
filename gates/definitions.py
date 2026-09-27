"""Gate definitions: the exit checks for each SDLC phase (G0 Requirements → G5 Release)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from gates.engine import ROOT, Check, CheckResult, Gate

REQUIREMENTS = ROOT / "docs" / "01_requirements.md"
DESIGN = ROOT / "docs" / "02_design.md"
E2E_RESULTS = ROOT / "reports" / "e2e_results.json"
MAX_LLM_CALLS_PER_RUN = 20
DESIGN_HEADINGS = ("Module map", "State contracts", "Sequence", "Architecture decisions", "Failure modes")
SCAN_EXCLUDE = {".venv", ".git", "reports", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _sh(*cmd: str, env: dict[str, str] | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **(env or {})})


def _tail(text: str, n: int = 3) -> str:
    return " | ".join(line.strip() for line in text.strip().splitlines()[-n:])


def _exists(path: Path) -> Check:
    return Check(f"{path.relative_to(ROOT)} exists",
                 lambda: CheckResult(path.exists(), "found" if path.exists() else "missing"))


def _at_ids(text: str) -> set[str]:
    return set(re.findall(r"\bAT-\d{2}\b", text))


# ─────────────────────────── G0 · Requirements ───────────────────────────
def check_requirements_traceable() -> CheckResult:
    rows = [r for r in REQUIREMENTS.read_text().splitlines() if re.match(r"\|\s*N?FR-\d{2}", r)]
    unmapped = [r.split("|")[1].strip() for r in rows if not re.search(r"AT-\d{2}", r)]
    if not rows:
        return CheckResult(False, "no FR-/NFR- rows found")
    return CheckResult(not unmapped, f"{len(rows)} requirements, unmapped: {unmapped or 'none'}")


# ─────────────────────────── G1 · Design ───────────────────────────
def check_design_sections() -> CheckResult:
    text = DESIGN.read_text()
    missing = [h for h in DESIGN_HEADINGS if not re.search(rf"^#+ .*{h}", text, re.M | re.I)]
    return CheckResult(not missing, f"missing sections: {missing or 'none'}")


def _designed_modules() -> list[str]:
    return sorted(set(re.findall(r"`(src/hierarchy_company/[\w/]+\.py)`", DESIGN.read_text())))


def check_design_names_modules() -> CheckResult:
    modules = _designed_modules()
    return CheckResult(len(modules) >= 10, f"{len(modules)} modules named in the module map")


# ─────────────────────────── G2 · Implementation ───────────────────────────
def check_design_conformance() -> CheckResult:
    missing = [m for m in _designed_modules() if not (ROOT / m).exists()]
    return CheckResult(not missing, f"designed modules missing from src: {missing or 'none'}")


def check_ruff() -> CheckResult:
    p = _sh(sys.executable, "-m", "ruff", "check", "src", "tests", "gates")
    return CheckResult(p.returncode == 0, _tail(p.stdout or p.stderr))


def check_mypy() -> CheckResult:
    p = _sh(sys.executable, "-m", "mypy", "src")
    return CheckResult(p.returncode == 0, _tail(p.stdout or p.stderr, 1))


def check_import_without_key() -> CheckResult:
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    p = subprocess.run([sys.executable, "-c", "import hierarchy_company, hierarchy_company.factory; print('ok')"],
                       cwd=ROOT, capture_output=True, text=True, env={**env, "HC_DISABLE_DOTENV": "1"})
    return CheckResult(p.stdout.strip() == "ok", _tail(p.stdout + p.stderr, 1))


_COMPILE_SNIPPET = """
from langchain_openai import ChatOpenAI
from hierarchy_company.factory import build_company
org = build_company(model=ChatOpenAI(model="gpt-4.1-mini", api_key="sk-offline-compile-check"))
print(len(org.team_graphs), len(org.director_graphs), 1)
"""


def check_graphs_compile() -> CheckResult:
    p = _sh(sys.executable, "-c", _COMPILE_SNIPPET, env={"HC_DISABLE_DOTENV": "1"})
    counts = p.stdout.split()
    ok = p.returncode == 0 and counts == ["6", "2", "1"]
    return CheckResult(ok, f"teams/directors/company = {'/'.join(counts)}" if ok else _tail(p.stderr))


def check_registry_integrity() -> CheckResult:
    from hierarchy_company.registry import ORG

    teams = [t for d in ORG.divisions for t in d.teams]
    specialists = [s for t in teams for s in t.specialists]
    bad_tools = [s.name for s in specialists
                 if len(s.tools) != 2 or any(not (t.description or "").strip() for t in s.tools)]
    shape = f"1 CEO / {len(ORG.divisions)} directors / {len(teams)} teams / {len(specialists)} specialists"
    ok = (len(ORG.divisions), len(teams), len(specialists)) == (2, 6, 18) and not bad_tools
    return CheckResult(ok, f"{shape}; specialists without 2 documented tools: {bad_tools or 'none'}")


# ─────────────────────────── G3 · Verification ───────────────────────────
def check_unit_tests() -> CheckResult:
    p = _sh(sys.executable, "-m", "pytest", "tests/unit", "-q", "-p", "no:cacheprovider",
            "--cov=hierarchy_company", "--cov-report=term", "--cov-fail-under=85",
            env={"HC_DISABLE_DOTENV": "1", "OPENAI_API_KEY": ""})
    total = re.search(r"^TOTAL.*?(\d+%)", p.stdout, re.M)
    return CheckResult(p.returncode == 0,
                       f"{_tail(p.stdout, 1)}; coverage {total.group(1) if total else '?'} (min 85%)")


def check_traceability() -> CheckResult:
    required = _at_ids(REQUIREMENTS.read_text())
    covered = set().union(*(_at_ids(f.read_text()) for f in (ROOT / "tests").rglob("*.py")))
    missing = sorted(required - covered)
    return CheckResult(not missing, f"{len(required - set(missing))}/{len(required)} AT ids referenced by tests; "
                                    f"missing: {missing or 'none'}")


# ─────────────────────────── G4 · Validation ───────────────────────────
def check_api_key() -> CheckResult:
    from dotenv import dotenv_values

    present = bool(os.environ.get("OPENAI_API_KEY") or dotenv_values(ROOT / ".env").get("OPENAI_API_KEY"))
    return CheckResult(present, "OPENAI_API_KEY available" if present else "OPENAI_API_KEY not set")


E2E_JUNIT = ROOT / "reports" / "e2e_junit.xml"


def check_live_e2e() -> CheckResult:
    E2E_RESULTS.unlink(missing_ok=True)
    p = _sh(sys.executable, "-m", "pytest", "tests/e2e", "-m", "live", "-q", "-p", "no:cacheprovider",
            f"--junitxml={E2E_JUNIT}", timeout=1800)
    failed = re.findall(r"^FAILED (\S+)", p.stdout, re.M)
    if not failed and E2E_JUNIT.exists():  # evidence: name every failing test, not just the count
        import xml.etree.ElementTree as ET

        failed = [f"{c.get('classname')}::{c.get('name')}" for c in ET.parse(E2E_JUNIT).getroot().iter("testcase")
                  if c.find("failure") is not None or c.find("error") is not None]
    evidence = _tail(p.stdout, 1) + (f"; failing: {failed}" if failed else "")
    return CheckResult(p.returncode == 0, evidence)


def _e2e() -> list[dict]:
    return json.loads(E2E_RESULTS.read_text())["runs"] if E2E_RESULTS.exists() else []


def check_division_accuracy() -> CheckResult:
    runs = _e2e()
    hits = sum(r["division_ok"] for r in runs)
    return CheckResult(len(runs) == 6 and hits == 6, f"division routing {hits}/{len(runs)} (required 6/6)")


def check_bounded_and_complete() -> CheckResult:
    runs = _e2e()
    over = [r["task_id"] for r in runs if r["llm_calls"] > MAX_LLM_CALLS_PER_RUN]
    incomplete = [r["task_id"] for r in runs if not r["sections_ok"]]
    calls = [r["llm_calls"] for r in runs]
    ok = bool(runs) and not over and not incomplete
    return CheckResult(ok, f"LLM calls/run min={min(calls, default=0)} max={max(calls, default=0)} "
                           f"(ceiling {MAX_LLM_CALLS_PER_RUN}); over budget: {over or 'none'}; "
                           f"missing sections: {incomplete or 'none'}")


def check_no_repeats_no_fabrication() -> CheckResult:
    runs = _e2e()
    repeats = [f"{r['task_id']}:{r['specialists']}" for r in runs if not r.get("no_repeat_ok", False)]
    claims = [f"{r['task_id']}:{r['fabricated_claims']}" for r in runs if not r.get("claims_ok", False)]
    return CheckResult(bool(runs) and not repeats and not claims,
                       f"repeated specialists: {repeats or 'none'}; fabricated completion claims: {claims or 'none'}")


def check_team_accuracy() -> CheckResult:
    runs = _e2e()
    hits = sum(r["team_ok"] for r in runs)
    misses = [f"{r['task_id']}→{r['team']}" for r in runs if not r["team_ok"]]
    return CheckResult(hits >= 5, f"team routing {hits}/{len(runs)} (target ≥5/6); misroutes: {misses or 'none'}")


# ─────────────────────────── G5 · Release ───────────────────────────
def check_version_consistent() -> CheckResult:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    init = (ROOT / "src/hierarchy_company/__init__.py").read_text()
    in_init = f'__version__ = "{version}"' in init
    in_changelog = f"[{version}]" in (ROOT / "CHANGELOG.md").read_text()
    return CheckResult(in_init and in_changelog,
                       f"pyproject={version}, __init__ match={in_init}, CHANGELOG entry={in_changelog}")


def check_env_ignored() -> CheckResult:
    gi = ROOT / ".gitignore"
    ok = gi.exists() and ".env" in gi.read_text().split()
    return CheckResult(ok, ".env listed in .gitignore" if ok else ".env not git-ignored")


_SECRET_PATTERNS = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{30,}|AKIA[0-9A-Z]{16}")


def check_no_secrets() -> CheckResult:
    hits = []
    for f in ROOT.rglob("*"):
        if f.is_dir() or f.name == ".env" or SCAN_EXCLUDE & set(f.relative_to(ROOT).parts):
            continue
        try:
            if _SECRET_PATTERNS.search(f.read_text(errors="ignore")):
                hits.append(str(f.relative_to(ROOT)))
        except OSError:
            continue
    return CheckResult(not hits, f"files with secret-like strings: {hits or 'none'}")


GATES: list[Gate] = [
    Gate("G0", "Requirements", ("docs/01_requirements.md",), (
        _exists(REQUIREMENTS),
        Check("every FR/NFR maps to an acceptance test", check_requirements_traceable),
    )),
    Gate("G1", "Design", ("docs/02_design.md",), (
        _exists(DESIGN),
        Check("design has required sections", check_design_sections),
        Check("module map names the modules", check_design_names_modules),
    )),
    Gate("G2", "Implementation", ("src/hierarchy_company/**",), (
        Check("implementation conforms to design module map", check_design_conformance),
        Check("ruff lint clean", check_ruff),
        Check("package imports without an API key", check_import_without_key),
        Check("all 9 graphs compile", check_graphs_compile),
        Check("org registry is 1/2/6/18 with 2 documented tools each", check_registry_integrity),
        Check("mypy type check clean", check_mypy, blocking=False),
    )),
    Gate("G3", "Verification", ("tests/unit/**",), (
        Check("offline unit tests pass with ≥85% coverage", check_unit_tests),
        Check("every acceptance test id is covered by a test", check_traceability),
    )),
    Gate("G4", "Validation", ("tests/e2e/**", "reports/e2e_results.json"), (
        Check("OpenAI API key available", check_api_key),
        Check("live end-to-end suite passes", check_live_e2e),
        Check("division routing 6/6", check_division_accuracy),
        Check("runs bounded by budget and answers complete", check_bounded_and_complete),
        Check("no repeated specialists, no fabricated claims (FR-15/16)", check_no_repeats_no_fabrication),
        Check("team routing ≥5/6", check_team_accuracy, blocking=False),
    )),
    Gate("G5", "Release", ("README.md", "CHANGELOG.md"), (
        _exists(ROOT / "README.md"),
        _exists(ROOT / "CHANGELOG.md"),
        Check("version consistent across pyproject/__init__/CHANGELOG", check_version_consistent),
        Check(".env is git-ignored", check_env_ignored),
        Check("no secrets in source tree", check_no_secrets),
    )),
]
