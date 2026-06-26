"""Shared cross-page navigation.

Streamlit auto-discovers everything in pages/ and renders an unstyled
list with raw filenames at the top of the sidebar (the so-called
"master app" nav: "app nstores", "Lifecycle", "Monte Carlo", ...). It
also shows the entrypoint file's filename above the page links, which
made the sidebar a cluttered mess.

This module:
  * Hides the auto-nav with a CSS rule that targets every selector
    Streamlit has ever used for it (the selector has changed across
    versions; we cover them all to be safe).
  * Renders ONE curated link list, ordered by the teaching progression
    (1 → 4: simplest to most complex). Each entry is prefixed with its
    step number so the order is unambiguous.
  * Keeps Streamlit's collapse arrow so the sidebar can be hidden when
    the user wants more screen room.

The entrypoint script is whatever the user passed to ``streamlit run`` --
in this repo that can be ``app.py`` directly OR ``app_nstores.py``.
Streamlit's ``page_link`` resolves paths relative to the entrypoint's
directory, so we detect the entrypoint filename at runtime.
"""
from __future__ import annotations
import os
import streamlit as st

# Teaching-arc order: simplest first, complexity climbs as you go.
#   1. Stash_or_Spread   2 stores, 1 lever -- the warm-up.
#   2. Lifecycle         1 SKU, N stores, deterministic.
#   3. Monte_Carlo       whole assortment, stochastic.
#   4. Regional          two-region warehouse chain (specialised).
# Each tuple = (path, label, icon).
_STATIC_PAGES: list[tuple[str, str, str]] = [
    ("pages/Stash_or_Spread.py", "1 · Where stock sits", "\U0001F4E6"),
    ("pages/Lifecycle.py",       "2 · Buy × network",    "\U0001F3AF"),
    ("pages/Monte_Carlo.py",     "3 · Random demand",    "\U0001F3B2"),
    ("pages/Regional.py",        "4 · Regional 2-RW",    "\U0001F30D"),
]
_HOME_LABEL = "\U0001F3ED  Full simulator (home)"


# ── CSS: hide Streamlit's default nav across versions; polish ours. ──
_NAV_CSS = """
<style>
/* Hide Streamlit's auto-generated page list -- selector has churned
   across versions, so cover them all. */
section[data-testid="stSidebarNav"],
[data-testid="stSidebarNav"],
div[data-testid="stSidebarNavItems"],
ul[data-testid="stSidebarNavItems"] { display: none !important; }

/* Hide the entrypoint-script filename Streamlit shows above the
   auto-nav. */
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] {
    display: none !important;
}

/* Tighten the page-link rows so they read as a list, not chunks. */
section[data-testid="stSidebar"] [data-testid="stPageLink"] {
    margin-bottom: 0 !important;
}
section[data-testid="stSidebar"] [data-testid="stPageLink"] a {
    padding: 7px 10px !important;
    border-radius: 6px;
    transition: background-color 120ms ease;
}
section[data-testid="stSidebar"] [data-testid="stPageLink"] a:hover {
    background: rgba(26, 138, 74, 0.10);
}

/* Section header inside the sidebar. */
.ui-nav-section {
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #5a6a80;
    font-size: 11px;
    font-weight: 700;
    margin: 4px 0 6px 4px;
}
.ui-nav-rule {
    border: none;
    border-top: 1px solid #eef0f4;
    margin: 10px 0;
}
.ui-nav-tagline {
    color: #7a8497;
    font-size: 11.5px;
    margin: 6px 4px 0;
    line-height: 1.35;
}
</style>
"""


def _entrypoint_filename() -> str:
    """Return the basename Streamlit was launched with."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx()
        if ctx is not None and ctx.main_script_path:
            return os.path.basename(ctx.main_script_path)
    except Exception:
        pass
    return "app.py"


def _pages() -> list[tuple[str, str, str]]:
    """Home (entry script) + the curated teaching pages."""
    return [(_entrypoint_filename(), _HOME_LABEL, "")] + _STATIC_PAGES


def _inject_css() -> None:
    """Emit the nav CSS. Streamlit reruns the script on every interaction
    and only keeps what was emitted in the LAST run, so we always emit --
    no deduplication, otherwise the rules vanish after the first rerun."""
    st.markdown(_NAV_CSS, unsafe_allow_html=True)


def top_nav(current: str | None = None) -> None:
    """Render an inline link row at the top of a page.

    Used by pages that collapse the sidebar. ``current`` = this page's
    path (disabled). Pass the hardcoded path from each page."""
    _inject_css()
    pages = _pages()
    cur = current or pages[0][0]
    cols = st.columns(len(pages))
    for col, (path, label, icon) in zip(cols, pages):
        with col:
            st.page_link(path, label=label,
                         icon=(icon if icon else None),
                         disabled=(path == cur), width="stretch")


def sidebar_nav(current: str | None = None) -> None:
    """Render the curated nav inside the sidebar (used by the home app)."""
    _inject_css()
    pages = _pages()
    cur = current or pages[0][0]
    with st.sidebar:
        # Home link (no number prefix, distinct from the lesson list)
        path, label, icon = pages[0]
        st.page_link(path, label=label, disabled=(path == cur))

        st.markdown("<hr class='ui-nav-rule'/>", unsafe_allow_html=True)
        st.markdown(
            "<div class='ui-nav-section'>Teaching walk-through</div>",
            unsafe_allow_html=True,
        )

        # Numbered lesson pages in pedagogical order.
        for path, label, icon in pages[1:]:
            st.page_link(path, label=label, icon=icon,
                         disabled=(path == cur))

        st.markdown(
            "<div class='ui-nav-tagline'>Lessons climb from one lever "
            "on two stores to a full stochastic assortment. Walk in order "
            "or jump around — every page is self-contained.</div>",
            unsafe_allow_html=True,
        )
        st.markdown("<hr class='ui-nav-rule'/>", unsafe_allow_html=True)
