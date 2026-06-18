"""Shared top-of-page navigation for the teaching pages.

Streamlit auto-discovers everything in pages/ into the sidebar's page nav,
but several teaching pages collapse the sidebar (where that nav lives), so
we render an explicit link row in the page body instead. This keeps every
page reachable from every other one.

The entrypoint script is whatever the user passed to ``streamlit run`` —
in this repo that may be ``app.py`` directly OR ``app_nstores.py`` (a
trivial launcher that runpy's app.py). Streamlit's ``page_link`` resolves
paths relative to the entrypoint's directory, so we detect the entrypoint
filename at runtime rather than hardcoding it.

UI-only helper — safe to import from page scripts. (Do NOT add this to
sim_common, which must stay Streamlit-free.)
"""
from __future__ import annotations
import os
import streamlit as st

# Pages under pages/ — their paths are stable regardless of entrypoint.
_STATIC_PAGES = [
    ("pages/Stash_or_Spread.py", "Where stock sits", "\U0001F4E6"),
    ("pages/Monte_Carlo.py",     "Random demand",    "\U0001F3B2"),
    ("pages/Regional.py",        "Regional 2-RW",    "\U0001F30D"),
]


def _entrypoint_filename() -> str:
    """Return the basename Streamlit was launched with (e.g. 'app.py' or
    'app_nstores.py'). Falls back to 'app.py' if the runtime context is not
    available (e.g. during import outside Streamlit)."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx()
        if ctx is not None and ctx.main_script_path:
            return os.path.basename(ctx.main_script_path)
    except Exception:
        pass
    return "app.py"


def _pages() -> list[tuple[str, str, str]]:
    return [(_entrypoint_filename(), "Home", "\U0001F3ED")] + _STATIC_PAGES


def top_nav(current: str | None = None) -> None:
    """Render an inline link row. ``current`` = this page's path (disabled).

    Pages under ``pages/`` should pass their hardcoded ``pages/<name>.py``.
    Home pages can leave it None and the detected entrypoint will be used.
    """
    pages = _pages()
    cur = current or pages[0][0]
    cols = st.columns(len(pages))
    for col, (path, label, icon) in zip(cols, pages):
        with col:
            st.page_link(path, label=label, icon=icon,
                         disabled=(path == cur), width="stretch")


def sidebar_nav(current: str | None = None) -> None:
    """Render the same links inside the sidebar (for pages that keep it)."""
    pages = _pages()
    cur = current or pages[0][0]
    with st.sidebar:
        st.markdown("#### \U0001F9ED Pages")
        for path, label, icon in pages:
            st.page_link(path, label=label, icon=icon,
                         disabled=(path == cur))
        st.divider()
