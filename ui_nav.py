"""Shared cross-page navigation for the teaching pages.

Streamlit auto-discovers everything in pages/ into the sidebar's default
page-list, which renders raw filenames (``app_nstores`` etc.) -- ugly and
redundant once we render our own labelled nav. We hide that default
list via CSS and present a single curated nav instead. The same helper
also serves the inline top-of-page link row that pages with a collapsed
sidebar use.

The entrypoint script is whatever the user passed to ``streamlit run`` --
in this repo that can be ``app.py`` directly OR ``app_nstores.py`` (a
trivial launcher that runpy's app.py). Streamlit's ``page_link`` resolves
paths relative to the entrypoint's directory, so we detect the entrypoint
filename at runtime rather than hardcoding it.

UI-only helper -- safe to import from page scripts. (Do NOT add this to
sim_common, which must stay Streamlit-free.)
"""
from __future__ import annotations
import os
import streamlit as st

# Pages ordered by pedagogical progression: from the simplest 2-store
# lesson up to the full stochastic Monte-Carlo and then the specialised
# regional model. Each tuple = (path, label, icon, one-line subtitle).
_STATIC_PAGES: list[tuple[str, str, str, str]] = [
    ("pages/Stash_or_Spread.py", "Where stock sits",
        "\U0001F4E6", "2 stores, 1 lever — central vs spread"),
    ("pages/Lifecycle.py",       "Buy × network",
        "\U0001F3AF", "Single SKU, deterministic, week by week"),
    ("pages/Monte_Carlo.py",     "Random demand",
        "\U0001F3B2", "Whole assortment, Pareto noise"),
    ("pages/Regional.py",        "Regional 2-RW",
        "\U0001F30D", "Two-region warehouse chain"),
]

# CSS that hides Streamlit's auto-generated page list AND tightens the
# spacing/colour of our own page_link rows. Applied once per page render.
_HIDE_DEFAULT_NAV_CSS = """
<style>
section[data-testid="stSidebarNav"] { display: none; }
section[data-testid="stSidebar"] .stPageLink a {
    padding: 6px 10px; border-radius: 6px;
}
section[data-testid="stSidebar"] .stPageLink a:hover {
    background: rgba(26, 138, 74, 0.08);
}
.ui-nav-subtitle {
    color: #7a8497; font-size: 11.5px;
    margin: -4px 0 6px 36px; line-height: 1.25;
}
.ui-nav-section-label {
    text-transform: uppercase; letter-spacing: 0.06em;
    color: #5a6a80; font-size: 11px; font-weight: 600;
    margin: 6px 0 4px 4px;
}
</style>
"""


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


def _pages() -> list[tuple[str, str, str, str]]:
    """Home (entry script) + the curated teaching pages."""
    return [(_entrypoint_filename(), "Home",
             "\U0001F3ED", "Full single-scenario simulator")] + _STATIC_PAGES


def _inject_css() -> None:
    """Hide Streamlit's default page list and polish our link styling.

    Streamlit re-renders the whole script on every interaction, so emitting
    the same <style> block twice is harmless -- but cheaper to skip on
    second emission. We dedupe via session_state when it's available."""
    key = "_ui_nav_css_injected"
    try:
        if st.session_state.get(key):
            return
        st.session_state[key] = True
    except Exception:
        pass
    st.markdown(_HIDE_DEFAULT_NAV_CSS, unsafe_allow_html=True)


def top_nav(current: str | None = None) -> None:
    """Render an inline link row at the top of a page.

    Used by pages that collapse the sidebar. ``current`` = this page's path
    (disabled). Pages under ``pages/`` should pass their hardcoded path."""
    _inject_css()
    pages = _pages()
    cur = current or pages[0][0]
    cols = st.columns(len(pages))
    for col, (path, label, icon, _sub) in zip(cols, pages):
        with col:
            st.page_link(path, label=label, icon=icon,
                         disabled=(path == cur), width="stretch")


def sidebar_nav(current: str | None = None) -> None:
    """Render the curated nav inside the sidebar (used by app.py).

    Hides Streamlit's default page-list (which would show raw filenames
    like 'app nstores') and lays out our links with subtitles so each
    page advertises what it actually teaches."""
    _inject_css()
    pages = _pages()
    cur = current or pages[0][0]
    with st.sidebar:
        st.markdown(
            "<div class='ui-nav-section-label'>Teaching pages</div>",
            unsafe_allow_html=True,
        )
        for i, (path, label, icon, subtitle) in enumerate(pages):
            st.page_link(path, label=label, icon=icon,
                         disabled=(path == cur))
            st.markdown(
                f"<div class='ui-nav-subtitle'>{subtitle}</div>",
                unsafe_allow_html=True,
            )
            # Visual separator between Home and the lesson pages.
            if i == 0:
                st.markdown(
                    "<hr style='margin:8px 0 10px; border:none; "
                    "border-top:1px solid #eef0f4;'>",
                    unsafe_allow_html=True,
                )
        st.markdown(
            "<hr style='margin:16px 0 4px; border:none; "
            "border-top:1px solid #eef0f4;'>",
            unsafe_allow_html=True,
        )
