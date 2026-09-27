# 01 · Requirements Specification (Gate G0)

**Product:** `hierarchy_company`, a 4-level hierarchical multi-agent system (CEO → Director → Team Supervisor → Specialist).
**Source:** `PROMPT.md` v2. **v2.0 change:** all mock tools are replaced with real implementations (FR-02, FR-17, FR-18, NFR-09).
**Status:** baselined at G0. Later changes go through `CHANGELOG.md`.

## 1. Scope

**In scope:**
- A LangGraph orchestration of 1 CEO, 2 directors, 6 team supervisors and 18 tool-using specialists.
- Real tools: local analyzers, public no-key APIs (OSV.dev, GitHub) and credentialed integrations (Postgres, AWS IAM).
- A CLI and a Python API.
- Offline unit tests.
- A live end-to-end validation suite.

**Out of scope:**
- Write or mutating actions on external systems. Tools are read-only or generate artifacts.
- Persistence and checkpointing.
- Async or parallel fan-out.

## 2. Stakeholders

| Role | Interest |
|---|---|
| Course learners | A readable reference for hierarchical supervision |
| Engineers | A modular, testable code base they can extend with real tools |
| Operators | Bounded cost and guaranteed termination |

## 3. Functional requirements

| ID | Requirement | Priority | Acceptance test |
|---|---|---|---|
| FR-01 | The organization has exactly 1 CEO, 2 divisions (engineering, operations), 6 teams and 18 specialists, as in the PROMPT table. | Must | AT-01 |
| FR-02 | Every specialist has exactly 2 **real** `@tool`s with non-empty docstrings. **No canned or mock data:** every output is computed from the tool's input, from a live source, or from a documented generator (templates or runbooks). | Must | AT-02 |
| FR-03 | Specialists are built with `create_agent`. The system prompt states the role and scope, says to use the tools, and caps the answer at 150 words. The internal tool loop is bounded by a recursion limit and fails soft. | Must | AT-03 |
| FR-04 | A team supervisor chooses only from its own 3 specialists or `FINISH`. The structured-output schema rejects any other value. | Must | AT-04 |
| FR-05 | A team makes at most `MAX_SPECIALIST_TURNS` (default 3) specialist calls, **enforced in code** whatever the LLM outputs. | Must | AT-05 |
| FR-06 | A team writer merges the specialist notes into `team_output`. | Must | AT-06 |
| FR-07 | A director chooses exactly one of its 3 teams and records a reason. Only the chosen team runs. | Must | AT-07 |
| FR-08 | The director review turns the team output into `division_output`, with at most 3 cross-team bullets. | Must | AT-08 |
| FR-09 | The CEO chooses exactly one division. The final answer contains the sections **Summary**, **Details** and **Routing Path**. | Must | AT-09 |
| FR-10 | Trace contract: each decision appends `"<Actor> → <choice>"`. Child graphs contribute only their new entries, in order: CEO, then Director, then Supervisor decisions. | Must | AT-10 |
| FR-11 | Routing prompts include a one-line description of every option, the text "Choose exactly one. If the task spans several, pick the one that owns the primary deliverable.", the task, and (for supervisors) the notes collected so far. | Must | AT-11 |
| FR-12 | `run_company(task)` returns a `RunResult` with division, team, trace, final answer, LLM call count and latency. | Must | AT-12 |
| FR-13 | A CLI (`hierarchy-company "<task>"`) prints the routing trace and the final answer. `--graph` prints the Mermaid diagrams. | Should | AT-13 |
| FR-14 | On the 6 reference tasks, division routing is 6/6 correct and team routing is at least 5/6 correct. | Must | AT-14 |
| FR-15 | A specialist is consulted at most once per team run, **enforced in code**: the supervisor's schema offers only the specialists not yet consulted, plus `FINISH`. When every specialist has been consulted, `FINISH` is forced without an LLM call. *(v1.1, fixes the repeated Kubernetes Agent seen in T5)* | Must | AT-21 |
| FR-17 | Tools degrade honestly. When an integration is not configured or a call fails, the tool returns `UNAVAILABLE (<tool>): <reason>` and never a guessed result. Specialists report that state instead of inventing data. | Must | AT-24 |
| FR-18 | Specialists never invent inputs (code, configs, keys, metrics) to feed a tool. If an artifact is missing, they say exactly what is needed. The CLI can attach files to a task (`--attach`). | Must | AT-25 |
| FR-19 | `stream_company(task)` yields typed events as the run progresses: routing decisions, tool calls and results, specialist notes, and team, division and final outputs. It ends with the same `RunResult` as `run_company`, which is built on top of it. | Must | AT-27 |
| FR-20 | A Streamlit web UI (`hierarchy-company-ui`) lets a user enter or pick a task, attach files, choose the model and turn limit, and watch the route live. It then shows the answer, trace, specialist notes, tool calls, metrics and a JSON download, plus a session history and the architecture diagrams. The API key is never displayed; a key pasted in the UI lives only in the session. | Should | AT-28 |
| FR-16 | Outputs never claim that an action was executed. Specialists, writers, reviews and the CEO phrase every action as a recommendation, because the tools only inspect and propose. *(v1.1, fixes fabricated claims such as "key has been revoked")* | Must | AT-22 |

## 4. Non-functional requirements

| ID | Requirement | Measure | Acceptance test |
|---|---|---|---|
| NFR-01 | Offline testability | The package imports and all graphs build without an API key. Builders accept an injected model. | AT-15 |
| NFR-02 | Termination | Every run finishes within `recursion_limit=50`. There are no unbounded loops. | AT-05, AT-16 |
| NFR-03 | Cost | At most 20 LLM calls per run, measured through a callback. | AT-16 |
| NFR-04 | Configurability | Model, temperature, turn limits and recursion limits come from `HC_*` environment variables with typed defaults and validation. | AT-17 |
| NFR-05 | Security | No secrets in the source tree. `.env` is git-ignored. The key is never printed. | AT-18 |
| NFR-06 | Robustness | Empty or whitespace-only tasks are rejected with `ValueError` before any LLM call. | AT-19 |
| NFR-07 | Maintainability | `ruff` is clean, the code is typed (`mypy` advisory), and unit-test coverage is at least 85%. | AT-20 (+ gates G2, G3) |
| NFR-09 | Safety of external access | Network calls use timeouts (≤10s). Tool inputs are validated before any call (repo slug, key id, vulnerability id, git ref). The database is queried read-only: only `SELECT` is explained, never `EXPLAIN ANALYZE`. Unit tests make no network calls. | AT-26 |
| NFR-08 | Resilience | Every OpenAI call has a request timeout (`HC_REQUEST_TIMEOUT`, default 60s) and bounded retries (`HC_MAX_RETRIES`, default 2). | AT-23 |

## 5. Acceptance-test catalogue

| AT | Level | Verifies |
|---|---|---|
| AT-01 | unit | Org registry shape is 1/2/6/18 |
| AT-02 | unit | Each specialist has 2 documented tools, and each tool's behaviour is tested against real inputs |
| AT-03 | unit | Specialist prompt content; soft failure on the recursion limit |
| AT-04 | unit | Team schema accepts only its specialists and FINISH |
| AT-05 | unit | The turn cap holds even when the LLM never chooses FINISH |
| AT-06 | unit | The team writer output lands in `team_output` |
| AT-07 | unit | A director routes to exactly one team; no other team runs |
| AT-08 | unit | The director review output lands in `division_output` |
| AT-09 | unit + e2e | CEO routing; the final answer has the three sections |
| AT-10 | unit | Trace order and content across all 4 levels |
| AT-11 | unit | Routing prompts contain the required elements |
| AT-12 | unit | `RunResult` fields are populated |
| AT-13 | unit | CLI prints the trace and the answer |
| AT-14 | e2e | Routing accuracy on the reference tasks |
| AT-15 | unit | Import and build succeed without an API key; the model can be injected |
| AT-16 | e2e | LLM calls per run are within budget; every run terminates |
| AT-17 | unit | Environment overrides and validation for settings |
| AT-18 | unit | No secret-like strings in the tracked source |
| AT-19 | unit | Empty tasks are rejected |
| AT-20 | unit | `ruff` passes on `src/` (static quality) |
| AT-21 | unit + e2e | No specialist is consulted twice; the schema narrows to the remaining specialists; `FINISH` is forced when all have been consulted |
| AT-22 | unit + e2e | Prompts forbid claims of completed actions; live answers contain no such claims |
| AT-23 | unit | Timeout and retries are configured on the chat model and validated |
| AT-24 | unit | Every integration returns UNAVAILABLE when unconfigured or failing, and never fabricates |
| AT-25 | unit + e2e | The specialist prompt forbids invented inputs; CLI `--attach`; live specialists compute from supplied artifacts |
| AT-26 | unit | Input validation blocks unsafe calls; `EXPLAIN` is SELECT-only; tools make no network calls on invalid input |
| AT-27 | unit + e2e | The event stream is ordered, has no duplicated routing events, and its final result equals `run_company`'s |
| AT-28 | unit | The UI renders, runs a task with a scripted model, shows results and history, handles errors, and never renders the key |

## 6. Reference tasks (FR-14)

| ID | Task | Division | Team |
|---|---|---|---|
| T1 | Our React checkout page fails WCAG contrast checks and re-renders too often. | engineering | frontend_team |
| T2 | Design a JWT-based auth flow for our REST API split into microservices. | engineering | backend_team |
| T3 | This Postgres query on the orders table takes 9 seconds; fix the schema/indexes. | engineering | database_team |
| T4 | Build a regression test plan for the payment module before release. | operations | qa_team |
| T5 | Containerize our Flask app and deploy it to Kubernetes with a CI/CD pipeline. | operations | devops_team |
| T6 | An AWS key was committed to GitHub; assess the IAM blast radius and remediate. | operations | security_team |
