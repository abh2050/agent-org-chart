# Changelog

This project follows [Semantic Versioning](https://semver.org). Each release must pass SDLC gates G0–G5 (`python -m gates`).

## [2.1.0] — 2026-09-27

### Added
- **Streamlit web UI** (`hierarchy-company-ui`, install with the `ui` extra), FR-20:
  - Pick an example or type a task, and attach files (only the first 50 KB of each file is sent).
  - Choose the model and the specialist limit.
  - Watch routing decisions, tool calls and specialist reports stream in live.
  - Review the answer, route, specialist notes, tool calls and JSON download, plus a session history and the architecture diagrams.
  - A key pasted into the UI stays in the session and is never displayed.
- **`stream_company()`**, a typed event stream built on LangGraph's nested `subgraphs=True` stream (FR-19). `run_company()` is now built on top of it.
- **`append_attachments()`** helper, shared by the CLI and the UI.
- **Gate G4 now names every failing live test** in its evidence, using a JUnit report.

### Fixed
- In a real browser, choosing an example did not fill the task box. The headless `AppTest` suite did not show this bug; a browser run caught it. The task box now uses a versioned widget key.

## [2.0.0] — 2026-09-27

### Breaking
- **All 36 mock tools are replaced with real tools.** Several tools were renamed or re-scoped (for example `lookup_cve` became `lookup_vulnerability`, and `rotate_secret` became `get_rotation_runbook`), so the tool signatures have changed.

### Added
- **Local analyzers over the artifacts you supply:**
  - JSX lint and render-trigger analysis, a CSS layout audit, WCAG contrast math, and an HTML accessibility audit.
  - OpenAPI validation, JWT decoding and audit, and compose dependency and cycle mapping.
  - SQL anti-pattern analysis (sqlglot), index derivation, and EXPLAIN-plan parsing.
  - pytest skeletons generated from the Python AST, reading Cobertura coverage reports, git change risk, and flaky tests from JUnit XML.
  - Dockerfile and Kubernetes linting, and IAM policy analysis.
  - A secret scanner with provider patterns, entropy scoring and redaction.
- **Live public data:** OSV.dev dependency and vulnerability lookups, and GitHub Actions run status.
- **Credentialed integrations:** read-only Postgres (`HC_DATABASE_URL`, SELECT-only EXPLAIN, statement timeout) and AWS IAM access-key usage (boto3).
- **Generators:** Dockerfile, Kubernetes manifests, a GitHub Actions workflow, a JWT config (RFC 8725), REST endpoints, HTTP test fixtures and rotation runbooks. Generated artifacts pass the project's own linters.
- **Honest degradation:** tools return `UNAVAILABLE (...)` or `INPUT NEEDED (...)` instead of guessing (FR-17), and specialists never invent inputs (FR-18).
- **CLI `--attach FILE`** to supply real artifacts.
- **Live honesty tests** (`tests/e2e/test_live_tools.py`).

### Removed
- `section9_hierarchical_company.py`, the notebook prototype that used mock tools.

## [1.1.0] — 2026-09-27

### Fixed
- **A supervisor could re-pick a specialist it had already consulted** (FR-15). In v1.0 this happened in T5, where Kubernetes Agent was called twice. The routing enum now offers only unconsulted specialists, and once every specialist has been consulted the supervisor finishes without an LLM call. `TeamState` gains a `consulted` field.
- **Final answers claimed actions had been executed** (FR-16), for example "the key has been revoked". Every summarizing prompt now enforces a recommendation-only contract, and G4 checks live answers for completion claims.

### Added
- OpenAI request timeout (`HC_REQUEST_TIMEOUT`, 60s) and bounded retries (`HC_MAX_RETRIES`, 2) (NFR-08).
- Live OpenAI integration tests and per-level routing tests (`tests/e2e/test_live_openai.py`, `tests/e2e/test_live_routing.py`).
- G4 check: no repeated specialists and no fabricated claims.

## [1.0.0] — 2026-09-27

### Added
- A 4-level hierarchy: 1 CEO, 2 directors, 6 team supervisors and 18 specialists, each specialist with 2 deterministic mock tools.
- The org chart is defined as data (`registry.py`), and all 9 graphs are generated from it by the `build_company()` composition root.
- Structured routing through `Literal` enum schemas at every decision point.
- A code-enforced cap on specialist turns (`HC_MAX_SPECIALIST_TURNS`) and a soft-fail recursion limit on specialists.
- A trace contract that runs across all levels.
- `run_company()` returns a `RunResult` that includes the LLM call count and latency.
- The `hierarchy-company` CLI, with `--json` and `--graph` options.
- The SDLC gate engine (`gates/`), requirements and design specs, an offline unit suite and a live E2E suite.

### Changed
- Replaced the notebook-cell prototype (`section9_hierarchical_company.py`) with the modular package. The prompt was revised into `PROMPT.md` v2 (SDLC-gated).
