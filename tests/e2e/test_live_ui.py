"""Live UI check (AT-28): the real Streamlit page runs an example task against OpenAI.

Run: pytest tests/e2e/test_live_ui.py -m live
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hierarchy_company.factory import Organization

pytestmark = pytest.mark.live
APP = Path(__file__).resolve().parents[2] / "src" / "hierarchy_company" / "ui" / "app.py"


def test_at28_at27_live_ui_run_streams_route_and_tools(org: Organization) -> None:
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    st.cache_resource.clear()
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    at.selectbox(key="example").select("Security · over-privileged IAM policy").run()
    at.button(key="run").click().run()
    assert not at.exception, at.exception
    assert not at.error, at.error[0].value if at.error else ""

    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Division"] == "operations" and metrics["Team"] == "security_team"
    record = at.session_state["history"][0]
    assert any(e.kind == "tool_call" and e.text.startswith("analyze_iam_policy") for e in record.events)
    assert record.result.final_answer and 0 < record.result.llm_calls <= 20
