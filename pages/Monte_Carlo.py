"""'Does keeping stock central still pay when demand is random?' — MC page.

The sibling of *Where should the stock sit?*, scaled up and made stochastic.
Same lesson, sharper: you buy the right TOTAL but cannot know which of the
N SKUs will be a winner or which of the M stores will sell it. We roll the
season thousands of times — each roll redraws every (SKU, store) sell-rate
from two user-chosen distributions — and look at the DISTRIBUTION of how
much keeping a slice central beats dumping everything to stores on day 1.

Built entirely on the validated ``sim_nstores`` engine (invariant-checked:
no warehouse hoarding, exact mass conservation).
"""
from __future__ import annotations
import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from sim_nstores import simulate_mc, summarise, WEEKS
from ui_nav import top_nav

st.set_page_config(layout="wide", page_title="Central stock under uncertainty",
                   page_icon="\U0001F3B2")

st.markdown("""
<style>
.block-container { padding-top: 1.4rem; max-width: 1400px; }
section[data-testid="stSidebar"] { width: 0 !important; min-width: 0 !important; }
</style>
""", unsafe_allow_html=True)

top_nav("pages/Monte_Carlo.py")

st.markdown(
    "<h1 style='margin:0 0 4px 0; font-size:28px;'>\U0001F3B2 "
    "Does keeping stock central still pay when demand is random?</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<div style='color:#5a6a80; font-size:14px; margin-bottom:14px;'>"
    f"We roll the {WEEKS}-week season thousands of times. Same buy every time — "
    "but each roll redraws which SKUs and which stores happen to sell. "
    "<b>Where should the stock start?</b></div>",
    unsafe_allow_html=True,
)

_FAMILIES = {
    "Lognormal (fat upside tail)": "lognormal",
    "Gamma (positive, skewed)":    "gamma",
    "Normal (symmetric, clipped)": "truncnormal",
}

# ── Inputs ────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
with c1:
    forecast_per_week = st.number_input(
        "Forecast (units / week, whole assortment)",
        min_value=20, max_value=1000, value=100, step=10,
        help="Total weekly sales you THINK you'll do across every SKU and store. "
             "Sets how much you buy for the season.")
with c2:
    target_sell_through = st.slider(
        "Target sell-through for buy (%)",
        min_value=10, max_value=100, value=70, step=5,
        help="How much of the buy you aim to actually sell. 100% = buy exactly "
             "the forecast; 50% = buy double.")
with c3:
    n_sku = st.slider("Number of SKUs", min_value=1, max_value=40, value=10, step=1,
                      help="How many distinct products. You can't know in advance "
                           "which will be the winners.")
with c4:
    n_store = st.slider("Number of stores", min_value=2, max_value=60, value=20, step=1,
                        help="How many stores share the assortment.")

m1, m2, m3, m4 = st.columns(4)
with m1:
    hold_pct = st.slider(
        "**% kept central on day 1** \U0001F441 (the lever)",
        min_value=0, max_value=100, value=30, step=5,
        help="0% = dump everything to stores on day 1. 100% = hold it all "
             "central and feed stores weekly. The truth is in between.")
with m2:
    price = st.slider("Selling price (€ / unit)", min_value=2, max_value=50,
                      value=10, step=1)
with m3:
    var_cost = st.slider("Cost of goods (€ / unit)", min_value=1, max_value=40,
                         value=5, step=1)
with m4:
    runs = st.select_slider("Monte-Carlo rolls", options=[1000, 2000, 5000, 10000, 20000],
                            value=5000,
                            help="More rolls = smoother distribution, slower. "
                                 "Results are cached per setting.")

# ── The two noise sources (user picks the distribution) ──
st.markdown("<div style='font-size:13px; color:#5a6a80; margin:6px 0 2px;'>"
            "The two sources of randomness — pick a shape and how wild it is "
            "(CV = std ÷ mean):</div>", unsafe_allow_html=True)
d1, d2, d3, d4 = st.columns(4)
with d1:
    dist1_label = st.selectbox("SKU strength vs forecast — shape",
                               list(_FAMILIES), index=0,
                               help="How much actual sales of a SKU deviate from "
                                    "forecast. One draw per SKU, shared by its stores.")
with d2:
    dist1_cv = st.slider("SKU strength volatility (CV)", min_value=0.0, max_value=1.5,
                         value=0.6, step=0.1)
with d3:
    dist2_label = st.selectbox("Store vs average — shape",
                               list(_FAMILIES), index=0,
                               help="How much a store sells vs the average store. "
                                    "One draw per (SKU, store) couple.")
with d4:
    dist2_cv = st.slider("Store volatility (CV)", min_value=0.0, max_value=1.5,
                         value=0.6, step=0.1)


@st.cache_data(show_spinner="Rolling the season…")
def _run(forecast_per_week, n_sku, n_store, hold_pct, target_sell_through,
         d1_fam, d1_cv, d2_fam, d2_cv, runs, price, var_cost):
    """Cached MC run. Returns headline stats + a binned gap histogram so the
    cache stays small (no big arrays kept around)."""
    res = simulate_mc(
        forecast_per_week=forecast_per_week, n_sku=n_sku, n_store=n_store,
        hold_pct=hold_pct / 100.0, target_sell_through=target_sell_through / 100.0,
        dist1_family=d1_fam, dist1_cv=d1_cv, dist2_family=d2_fam, dist2_cv=d2_cv,
        runs=runs, price=float(price), var_cost=float(var_cost), seed=0)
    summ = summarise(res)
    counts, edges = np.histogram(res.gap, bins=40)
    summ["hist_counts"] = counts.tolist()
    summ["hist_edges"]  = edges.tolist()
    return summ


summ = _run(forecast_per_week, n_sku, n_store, hold_pct, target_sell_through,
            _FAMILIES[dist1_label], dist1_cv, _FAMILIES[dist2_label], dist2_cv,
            runs, price, var_cost)

# ── Headline ──
keep_wins = summ["keep_wins_pct"]
mean_gap  = summ["mean_gap"]
st.markdown(
    f"<div style='text-align:center; font-size:15px; color:#1a2a40; margin:14px 0 4px;'>"
    f"You buy <b>{summ['bought']:,}</b> units. Across <b>{runs:,}</b> rolled seasons, "
    f"keeping <b>{hold_pct}%</b> central beats dumping everything in "
    f"<b style='color:#1a8a4a;'>{keep_wins:.0f}%</b> of them — "
    f"average margin gained <b style='color:#1a8a4a;'>€{mean_gap:,.0f}</b>.</div>",
    unsafe_allow_html=True,
)


def _card(title, margin_mean, sellt, lost, accent):
    return (
        f"<div style='flex:1; border:1px solid #e3e8ef; border-top:3px solid {accent}; "
        f"border-radius:8px; padding:12px 16px;'>"
        f"<div style='font-weight:700; font-size:12px; letter-spacing:.5px; "
        f"text-transform:uppercase; color:{accent}; margin-bottom:8px;'>{title}</div>"
        f"<div style='display:flex; justify-content:space-between; font-size:13px; "
        f"padding:2px 0;'><span style='color:#5a6a80;'>Avg margin</span>"
        f"<b>€{margin_mean:,.0f}</b></div>"
        f"<div style='display:flex; justify-content:space-between; font-size:13px; "
        f"padding:2px 0;'><span style='color:#5a6a80;'>Avg sell-through</span>"
        f"<b>{sellt:.0f}%</b></div>"
        f"<div style='display:flex; justify-content:space-between; font-size:13px; "
        f"padding:2px 0;'><span style='color:#5a6a80;'>Avg lost sales</span>"
        f"<b>{lost:,.0f} units</b></div>"
        f"</div>")

st.markdown(
    "<div style='display:flex; gap:14px; margin:10px 0 18px;'>"
    + _card("Dump everything to stores", summ["dump_margin_mean"],
            summ["dump_sellthrough"], summ["dump_lost_mean"], "#c0392b")
    + _card(f"Keep {hold_pct}% central", summ["keep_margin_mean"],
            summ["keep_sellthrough"], summ["keep_lost_mean"], "#1a8a4a")
    + "</div>",
    unsafe_allow_html=True,
)

# ── Distribution of the margin gap (the whole point of Monte-Carlo) ──
edges  = summ["hist_edges"]
counts = summ["hist_counts"]
centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(counts))]
width   = edges[1] - edges[0]
df = pd.DataFrame({"gap": centers, "seasons": counts})

bars = (
    alt.Chart(df)
    .mark_bar(color="#1a8a4a", opacity=0.85)
    .encode(
        x=alt.X("gap:Q", title="Margin advantage of keeping central (€ per season)"),
        y=alt.Y("seasons:Q", title="Number of rolled seasons"),
        tooltip=[alt.Tooltip("gap:Q", format=",.0f", title="€ gap"),
                 alt.Tooltip("seasons:Q", title="seasons")],
    )
)
# red colour for the (rare) seasons where dumping actually won
neg = bars.transform_filter(alt.datum.gap < 0).mark_bar(color="#c0392b", opacity=0.85)
zero_line = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(
    color="#1a2a40", strokeDash=[4, 3]).encode(x="x:Q")
mean_line = alt.Chart(pd.DataFrame({"x": [mean_gap]})).mark_rule(
    color="#1a8a4a", size=2).encode(x="x:Q")

st.altair_chart((bars + neg + zero_line + mean_line).properties(height=300),
                use_container_width=True)

st.caption(
    "Each bar = how many of the rolled seasons landed in that margin-gap range. "
    "Dashed line = break-even (left of it, in red, dumping happened to win). "
    "Solid green line = the average. Push either volatility slider up and watch "
    "the whole distribution slide right: the more unpredictable demand is, the "
    "more it pays to keep stock central and react. Set both CVs to 0 and the "
    "advantage collapses to ~0 — with nothing to react to, where stock starts "
    "doesn't matter."
)
