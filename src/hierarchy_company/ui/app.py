"""Streamlit page for hierarchy_company (FR-20). Layout and widgets only; logic lives in ui/services.py.

Run: hierarchy-company-ui   (or: streamlit run src/hierarchy_company/ui/app.py)
"""

from __future__ import annotations

import streamlit as st

from hierarchy_company import __version__
from hierarchy_company.runner import RunEvent, RunResult
from hierarchy_company.ui import services

st.set_page_config(page_title="hierarchy-company", page_icon=":material/account_tree:", layout="wide")


@st.cache_resource(show_spinner="Building the organization…")
def get_org(model: str, max_turns: int, api_key: str | None):
    return services.build_org(model, max_turns, api_key)


def use_example() -> None:
    # Writing a widget's own state from a callback is ignored by the live frontend, so the task box gets a
    # new key (a fresh widget) seeded with the example text instead.
    choice = st.session_state.get("example")
    if choice in services.EXAMPLES:
        st.session_state["task_seed"] = services.EXAMPLES[choice]
        st.session_state["task_version"] = st.session_state.get("task_version", 0) + 1


# ── Sidebar: settings and what the tools can reach ───────────────────
def sidebar() -> tuple[str, int, str | None, bool]:
    with st.sidebar:
        st.header("Settings")
        model = st.selectbox("Model", services.MODELS, key="model",
                             help="Used at every level: CEO, directors, supervisors and specialists.")
        turns = st.slider("Specialists per team (max)", 1, 3, 3, key="turns",
                          help="Upper bound on specialist calls per team. Lower is cheaper and faster.")

        api_key: str | None = None
        if services.openai_key_in_env():
            st.success("OpenAI key loaded from the environment.", icon=":material/key:")
            has_key = True
        else:
            api_key = st.text_input("OpenAI API key", type="password", key="api_key",
                                    help="Kept only in this browser session; never shown or saved.") or None
            has_key = bool(api_key)
            if not has_key:
                st.info("Add an OpenAI API key to run tasks.", icon=":material/info:")

        st.subheader("Tool integrations")
        for item in services.integration_status():
            dot = ":green[●]" if item.ready else ":gray[○]"
            st.markdown(f"{dot} **{item.name}**  \n<small>{item.detail}</small>", unsafe_allow_html=True)
        st.caption(f"hierarchy-company v{__version__}")
    return model, turns, api_key, has_key


# ── Result rendering ─────────────────────────────────────────────────
def render_record(record: services.RunRecord, key: str) -> None:
    r = record.result
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Division", r.division)
    c2.metric("Team", r.team)
    c3.metric("LLM calls", r.llm_calls)
    c4.metric("Time", f"{r.latency_s:.1f}s")
    if r.ceo_reason:
        st.caption(f"CEO's reason: {r.ceo_reason}")

    answer, route, specialists, tools, raw = st.tabs(["Answer", "Route", "Specialists", "Tool calls", "JSON"])
    with answer:
        st.markdown(r.final_answer)
    with route:
        st.code("\n".join(f"{i}. {step}" for i, step in enumerate(r.trace, 1)), language=None)
    with specialists:
        notes = record.notes()
        if not notes:
            st.write("No specialist notes were recorded.")
        for note in notes:
            with st.expander(note.actor, expanded=len(notes) == 1):
                st.markdown(note.text)
        for label, kind in (("Team report", "team_output"), ("Director review", "division_output")):
            text = record.output(kind)
            if text:
                with st.expander(label):
                    st.markdown(text)
    with tools:
        pairs = record.tool_calls()
        if not pairs:
            st.write("No tools were called in this run.")
        for call, result in pairs:
            status = "no result"
            if result is not None:
                status = result.text.split(" ", 1)[0] if result.text.startswith(("UNAVAILABLE", "INPUT")) else "ok"
            with st.expander(f"{call.actor} · {call.text.split('(', 1)[0]} · {status}"):
                st.code(call.text, language=None)
                if result is not None:
                    st.code(result.text, language=None)
    with raw:
        st.download_button("Download JSON", record.to_json(), file_name="hierarchy-company-run.json",
                           mime="application/json", key=f"dl-{key}")
        st.json(record.to_json(), expanded=False)


# ── Run tab ──────────────────────────────────────────────────────────
def run_tab(model: str, turns: int, api_key: str | None, has_key: bool) -> None:
    st.selectbox("Start from an example", ["Choose an example…", *services.EXAMPLES], key="example",
                 on_change=use_example)
    task = st.text_area("Task", value=st.session_state.get("task_seed", ""),
                        key=f"task-{st.session_state.get('task_version', 0)}", height=140,
                        placeholder="Describe the problem. Paste code, SQL, a policy or a Dockerfile, "
                                    "or attach files below.")
    files = st.file_uploader("Attach files (optional)", accept_multiple_files=True, key="files",
                             help="Dockerfile, SQL, IAM policy JSON, compose or Kubernetes YAML, source code. "
                                  "Up to 50 KB of each file is sent.")
    run = st.button("Run", type="primary", disabled=not has_key, key="run", icon=":material/play_arrow:")

    if run:
        if not task.strip():
            st.warning("Enter a task first.")
        else:
            full_task = services.compose_task(task, [(f.name, f.getvalue()) for f in files or []])
            events: list[RunEvent] = []
            result: RunResult | None = None
            with st.status("Routing the task…", expanded=True) as status:
                try:
                    org = get_org(model, turns, api_key)
                    for item in services.run_stream(full_task, org):
                        if isinstance(item, RunResult):
                            result = item
                            continue
                        events.append(item)
                        line = services.progress_line(item)
                        if line:
                            st.markdown(line, unsafe_allow_html=True)
                except Exception as exc:
                    status.update(label="Run failed", state="error", expanded=True)
                    st.error(services.describe_error(exc))
                if result is not None:
                    status.update(label=f"Done in {result.latency_s:.1f}s · {result.llm_calls} LLM calls",
                                  state="complete", expanded=False)
            if result is not None:
                record = services.RunRecord(result, tuple(events))
                st.session_state.setdefault("history", []).insert(0, record)
                st.session_state["last"] = record

    last = st.session_state.get("last")
    if last is not None:
        st.divider()
        render_record(last, "last")


def history_tab() -> None:
    history: list[services.RunRecord] = st.session_state.get("history", [])
    if not history:
        st.write("Runs from this session will appear here.")
        return
    for i, record in enumerate(history):
        r = record.result
        title = r.task.splitlines()[0][:90]
        with st.expander(f"{r.division} / {r.team} · {r.llm_calls} calls · {title}"):
            render_record(record, f"h{i}")


def architecture_tab() -> None:
    st.markdown(f"Interactive version: [architecture page]({services.ARCHITECTURE_URL})")
    for name, caption in (("hierarchy", "One route through four levels"),
                          ("team-loop", "Why the team loop always ends"),
                          ("tools", "Real tools that fail honestly"),
                          ("gates", "Built gate by gate")):
        path = services.DIAGRAMS / f"{name}.svg"
        if path.exists():
            st.subheader(caption)
            st.image(path.read_text(), width="stretch")


def main() -> None:
    model, turns, api_key, has_key = sidebar()
    st.title("hierarchy-company")
    st.caption("A CEO routes your task to one division, one team and up to three specialists, "
               "each using real tools. Watch the route as it happens.")
    run, history, arch = st.tabs(["Run", "History", "Architecture"])
    with run:
        run_tab(model, turns, api_key, has_key)
    with history:
        history_tab()
    with arch:
        architecture_tab()


main()
