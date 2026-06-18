"""Shared top-of-page navigation for the teaching pages.

Streamlit auto-discovers everything in pages/ into the sidebar's page nav,
but several teaching pages collapse the sidebar (where that nav lives), so
we render an explicit link row in the page body instead. This keeps every
page reachable from every other one.

UI-only helper — safe to import from page scripts. (Do NOT add this to
sim_common, which must stay Streamlit-free.)
"""
from __future__ import annotations
import streamlit as st

# (path relative to the streamlit entrypoint, label, icon)
PAGES = [
    ("app.py",                     "Home",                "\U0001F3ED"),
    ("pages/Stash_or_Spread.py",   "Where stock sits",    "\U0001F4E6"),
    ("pages/Monte_Carlo.py",       "Random demand",       "\U0001F3B2"),
    ("pages/Regional.py",          "Regional 2-RW",       "\U0001F30D"),
]


def top_nav(current: str) -> None:
    """Render an inline link row. ``current`` = this page's path (disabled)."""
    cols = st.columns(len(PAGES))
    for col, (path, label, icon) in zip(cols, PAGES):
        with col:
            st.page_link(path, label=label, icon=icon,
                         disabled=(path == current), width="stretch")


def sidebar_nav(current: str) -> None:
    """Render the same links inside the sidebar (for pages that keep it)."""
    with st.sidebar:
        st.markdown("#### \U0001F9ED Pages")
        for path, label, icon in PAGES:
            st.page_link(path, label=label, icon=icon,
                         disabled=(path == current))
        st.divider()
