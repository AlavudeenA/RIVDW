"""Screen: Query — search metadata using plain-English questions.

Phase 1 walking skeleton (see PLAIN_SUMMARY.md, "Runtime build plan"): plain vector
search over the build-time metadata, one drafted SQL query, never executed. The
retrieval/generation logic lives in rivdw_runtime/query_engine.py, not here — this
file only handles the UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

from config.strings import UILabels

_RUNTIME_ROOT = Path(__file__).resolve().parents[3] / "rivdw_runtime"


def render() -> None:
    """Render the Query screen."""
    st.title(UILabels.PAGE_QUERY_TITLE)
    st.write(UILabels.PAGE_QUERY_DESCRIPTION)

    _initialise_session_state()

    query = st.text_area(
        "Your question",
        placeholder=UILabels.QUERY_INPUT_PLACEHOLDER,
        height=100,
        key="query_input",
        label_visibility="collapsed",
    )

    submitted = st.button(
        UILabels.BTN_QUERY_SUBMIT,
        type="primary",
        disabled=not query.strip(),
    )

    if submitted and query.strip():
        st.session_state["last_query"] = query.strip()
        st.session_state["last_result"] = None
        st.session_state["last_error"] = None

    if st.session_state.get("last_query"):
        st.divider()
        st.markdown(f"**Question:** _{st.session_state['last_query']}_")

        if st.session_state.get("last_result") is None and st.session_state.get("last_error") is None:
            with st.spinner("Searching metadata and drafting SQL..."):
                _run_query(st.session_state["last_query"])

        if st.session_state.get("last_error"):
            st.error(st.session_state["last_error"])
        elif st.session_state.get("last_result") is not None:
            _render_result(st.session_state["last_result"])


def _initialise_session_state() -> None:
    defaults = {"last_query": "", "last_result": None, "last_error": None}
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default


def _run_query(question: str) -> None:
    """Call the runtime query engine and store the result (or error) in session state."""
    if str(_RUNTIME_ROOT) not in sys.path:
        sys.path.insert(0, str(_RUNTIME_ROOT))

    try:
        from query_engine import answer_question
        st.session_state["last_result"] = answer_question(question)
    except Exception as error:
        st.session_state["last_error"] = f"Query failed: {error}"


def _render_result(result) -> None:
    st.caption(
        "This is a Phase 1 walking skeleton: plain vector search only (no hybrid search, "
        "reranking, or HyDE yet), and the SQL below is drafted but never executed."
    )

    if not result.retrieved:
        st.warning("No relevant tables or columns were found for this question.")
        return

    with st.expander(f"Matched schema entries ({len(result.retrieved)})", expanded=False):
        for entry in result.retrieved:
            location = entry.table_name if not entry.column_name else f"{entry.table_name}.{entry.column_name}"
            st.markdown(f"**{location}** — score `{entry.score:.3f}`  \n{entry.description}")

    st.markdown("**Explanation**")
    st.write(result.explanation or "_No explanation returned._")

    if result.sql:
        st.markdown("**Draft SQL** _(not executed)_")
        st.code(result.sql, language="sql")
    else:
        st.info("The model didn't produce a SQL query for this question — see the explanation above.")
