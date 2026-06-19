"""'Does keeping stock central still pay when demand is random?' — MC table.

The stochastic, multi-SKU / multi-store generalisation of
*Where should the stock sit?*. You buy the right TOTAL but cannot know
which of the N SKUs will be a winner or which of the M stores will sell
it. We roll the season many times and tabulate the average margin / sell-
through / lost sales for every combination of:

  * %% kept central on day 1   (rows)        — the lever
  * target sell-through of buy (columns)     — how much you over- or under-buy

Built on the validated ``sim_nstores`` engine (no-hoarding & mass-
conservation invariants are exercised in the standalone smoke test; the
table loop runs the same code with the per-week assert turned off for
speed, after the smoke run has proven the path).
"""
from __future__ import annotations
import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from sim_nstores import simulate_grid_fast, draw_mean1, WEEKS
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
    f"For every combination of <b>% kept central</b> (lever) and "
    f"<b>target sell-through</b> (how much you buy), we roll the "
    f"{WEEKS}-week season many times and report the average outcome. "
    "Row <b>0%</b> = dumping everything to stores on day 1.</div>",
    unsafe_allow_html=True,
)

_FAMILIES = {
    "Lognormal (fat upside tail)": "lognormal",
    "Gamma (positive, skewed)":    "gamma",
    "Pareto (power-law, 80/20)":   "pareto",
    "Normal (symmetric, clipped)": "truncnormal",
}

# Table axes — fixed so the heatmap reads consistently across param changes
HOLD_PCTS  = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
TARGET_STS = [50, 60, 70, 80, 90, 100]

# Wall-time budget for the table compute (the cached _compute_table call).
# The slider's max R is set to keep first-load compute under this number,
# calibrated from measured timings at K=200..2000, M=20..500.
MAX_COMPUTE_S = 30


def _budget_max_R(K: int, M: int) -> int:
    """Largest MC rolls per cell that fits MAX_COMPUTE_S on this assortment.

    Measured timings show the per-op cost rises 2–3x once the per-cell array
    R·K·M outgrows L3 cache (~16 MB). The coefficient below picks the cap on
    the conservative side so even the cache-spillover regime stays in budget.
    """
    if K * M == 0:
        return 50
    R = 9_000_000 // (K * M)
    return int(max(10, min(R, 10_000)))


def _est_compute_s(R: int, K: int, M: int) -> float:
    """Order-of-magnitude estimate of the table compute time. Actual can
    drift ~2x either way due to cache effects; we display this purely so the
    user sees a number before clicking and isn't surprised by a 20s wait."""
    return 0.5 + 5e-8 * 66 * R * K * M


# ─────────────────────────── inputs ───────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
with c1:
    forecast_per_week = st.slider(
        "Forecast (units / week, whole assortment)",
        min_value=1000, max_value=10000, value=5000, step=500,
        help="Total weekly sales you THINK you'll do across every SKU and store. "
             "Drives how much you buy for the season.")
with c2:
    n_sku = st.slider("Number of SKUs", min_value=50, max_value=1000,
                      value=200, step=50,
                      help="How many distinct products share the buy. "
                           "You can't know up front which will be the winners.")
with c3:
    n_store = st.slider("Number of stores", min_value=2, max_value=500,
                        value=20, step=1,
                        help="How many stores share the assortment. Big store "
                             "counts make presentation minimums expensive (see below).")
with c4:
    price    = st.number_input("Selling price (€ / unit)",
                               min_value=1, max_value=200, value=10, step=1)
    var_cost = st.number_input("Cost of goods (€ / unit)",
                               min_value=1, max_value=200, value=5, step=1)

f1, f2 = st.columns(2)
with f1:
    fix_pct = st.slider(
        "Fixed cost  (% of forecast sales value)",
        min_value=0, max_value=70, value=20, step=5,
        help="Overheads (rent, staff, …) as a percentage of forecast sales "
             "value (forecast units × price × 26 weeks). Same across every "
             "cell of the table — only affects the absolute margin numbers, "
             "not the shape of the heatmap.")
with f2:
    min_per_store = st.slider(
        "Force units per store of each SKU  (presentation minimum)",
        min_value=0, max_value=10, value=0, step=1,
        help="Every store must hold at least this many units of EVERY SKU on the "
             "shelf — merchandising / assortment-breadth minimum. Seeded on day 1 "
             "and kept topped up by the warehouse. With many SKUs × many stores "
             "this floor (min × SKUs × stores) can dwarf the forecast-based buy and "
             "force you to over-buy massively. 0 = off.")

st.markdown(
    "<div style='font-size:13px; color:#5a6a80; margin:10px 0 2px;'>"
    "The two sources of randomness — pick a shape and how wild it is "
    "(CV = std ÷ mean). Multipliers are clipped to <b>[0.3, 3]</b> and "
    "re-centred so the mean stays at 1 — no SKU sells less than 30% or "
    "more than 3× the assortment-wide mean. Lognormal and gamma both "
    "give the classic <b>“few stars, many duds”</b> shape (mode below "
    "1, long upper tail) — exactly the retail pattern.</div>",
    unsafe_allow_html=True,
)

d1col, d2col = st.columns(2)
with d1col:
    st.markdown("<div style='font-size:13px; font-weight:600; margin-top:2px;'>"
                "SKU strength vs forecast <span style='color:#5a6a80; font-weight:400;'>"
                "— one draw per SKU, shared by all its stores</span></div>",
                unsafe_allow_html=True)
    dd1, dd2 = st.columns([3, 2])
    with dd1:
        dist1_label = st.selectbox("Shape", list(_FAMILIES), index=0,
                                    key="d1_shape", label_visibility="collapsed")
    with dd2:
        dist1_cv = st.slider("CV", min_value=0.0, max_value=1.5, value=0.6,
                              step=0.1, key="d1_cv", label_visibility="collapsed")
with d2col:
    st.markdown("<div style='font-size:13px; font-weight:600; margin-top:2px;'>"
                "Store vs average <span style='color:#5a6a80; font-weight:400;'>"
                "— one draw per (SKU, store)</span></div>",
                unsafe_allow_html=True)
    dd3, dd4 = st.columns([3, 2])
    with dd3:
        dist2_label = st.selectbox("Shape", list(_FAMILIES), index=0,
                                    key="d2_shape", label_visibility="collapsed")
    with dd4:
        dist2_cv = st.slider("CV", min_value=0.0, max_value=1.5, value=0.6,
                              step=0.1, key="d2_cv", label_visibility="collapsed")


# ─────────────────────────── distribution previews ────────────────────────
@st.cache_data(show_spinner=False)
def _dist_samples(family: str, cv: float) -> np.ndarray:
    """20k draws used purely to render the shape preview chart."""
    rng = np.random.default_rng(42)
    return draw_mean1(rng, family, cv, (20_000,))


def _dist_chart(family: str, cv: float, accent: str = "#1a8a4a") -> alt.Chart:
    """Pre-binned histogram of 20k mean-1 draws, with the mean line at x=1.

    Pre-binning matters: altair's default vega-lite data transformer caps a
    spec at 5,000 rows and raises MaxRowsError above that — which would
    silently break the whole page render. Sending 40 (centre, count) pairs
    instead of 20k samples both side-steps that limit and shrinks the
    websocket payload by ~1000x.
    """
    if cv <= 0:
        df = pd.DataFrame({"x": [1.0]})
        bar = alt.Chart(df).mark_rule(strokeWidth=5, color=accent).encode(
            x=alt.X("x:Q", title="multiplier (mean = 1, clipped to [0.3, 3])",
                    scale=alt.Scale(domain=[0.3, 3])))
    else:
        samples = _dist_samples(family, cv)
        counts, edges = np.histogram(samples, bins=40, range=(0.3, 3.0))
        centers = (edges[:-1] + edges[1:]) / 2
        df = pd.DataFrame({"x": centers, "n": counts})
        bar = alt.Chart(df).mark_bar(color=accent, opacity=0.85,
                                      size=max(2.0, 280.0 / 40 - 1)).encode(
            x=alt.X("x:Q",
                    title="multiplier (mean = 1, clipped to [0.3, 3])",
                    scale=alt.Scale(domain=[0.3, 3])),
            y=alt.Y("n:Q", axis=alt.Axis(labels=False, title=None,
                                          ticks=False, domain=False)),
        )
    mean_line = alt.Chart(pd.DataFrame({"x": [1.0]})).mark_rule(
        strokeDash=[3, 3], color="#1a2a40", size=2).encode(x="x:Q")
    return (bar + mean_line).properties(height=130)


prev1, prev2 = st.columns(2)
with prev1:
    st.altair_chart(_dist_chart(_FAMILIES[dist1_label], dist1_cv),
                    use_container_width=True)
with prev2:
    st.altair_chart(_dist_chart(_FAMILIES[dist2_label], dist2_cv),
                    use_container_width=True)


# ─────────────────────────── table compute ────────────────────────────────
@st.cache_data(show_spinner="Computing the table…")
def _compute_table(forecast: int, n_sku: int, n_store: int, min_per_store: int,
                   d1_fam: str, d1_cv: float, d2_fam: str, d2_cv: float,
                   price: float, var_cost: float, fixed_cost: float,
                   runs: int):
    """Sweep HOLD_PCTS x TARGET_STS via the closed-form FLAT-rate solver.

    No 26-week loop -- the solver jumps straight to the W26 outcome (the
    teaching engine assumes a flat weekly rate, so transient dynamics
    fully settle and the integral has a closed form). Verified within
    < 0.5%% of the week-by-week simulator across every regime the page
    exposes.

    `runs` is the user-picked MC rolls per cell -- the page caps this to
    fit MAX_COMPUTE_S of wall time, so the cached compute always returns
    within that budget. Returns the actual elapsed wall-time alongside the
    arrays so the page can show it (the cache stores it for free).
    """
    import time as _time
    t0 = _time.time()
    out = simulate_grid_fast(
        forecast_per_week=forecast, n_sku=n_sku, n_store=n_store,
        hold_pcts=HOLD_PCTS, target_sts=TARGET_STS,
        min_per_store=float(min_per_store),
        dist1_family=d1_fam, dist1_cv=d1_cv,
        dist2_family=d2_fam, dist2_cv=d2_cv,
        runs=runs, price=price, var_cost=var_cost,
        fixed_cost=fixed_cost, seed=0)
    return (out["margin"], out["sellthrough"], out["lost"],
            out["bought_by_st"], out["runs"], _time.time() - t0)

# Fixed cost = a percentage of forecast sales value over the season — same
# definition as on the Stash_or_Spread page so the two stay comparable.
forecast_sales_value = forecast_per_week * WEEKS * price
fixed_cost = float(fix_pct) / 100.0 * forecast_sales_value

# ── MC rolls slider: more rolls = lower MC noise, capped to MAX_COMPUTE_S ─
_max_R       = _budget_max_R(int(n_sku), int(n_store))
_default_R   = min(200, _max_R)             # snappy first load; user can crank up
_slider_step = max(1, _max_R // 50)

r1, r2 = st.columns([3, 2])
with r1:
    runs_R = st.slider(
        "Monte-Carlo rolls per cell",
        min_value=10, max_value=_max_R, value=_default_R, step=_slider_step,
        help=f"How many random seasons to average per cell of the table. "
             f"More = lower MC noise on the cell averages. Capped to keep the "
             f"first-load compute under ~{MAX_COMPUTE_S}s on the current "
             f"{n_sku:,} SKUs × {n_store} stores -- crank up if you want "
             "tighter numbers and don't mind the wait.")
with r2:
    _est_s = _est_compute_s(runs_R, int(n_sku), int(n_store))
    st.markdown(
        f"<div style='padding-top:30px; color:#5a6a80; font-size:13px;'>"
        f"Estimated compute: <b style='color:#1a2a40;'>~{_est_s:.1f}s</b> "
        f"(cached afterwards)</div>",
        unsafe_allow_html=True)

margin, sellt, lost, bought_by_st, R_used, actual_s = _compute_table(
    int(forecast_per_week), int(n_sku), int(n_store), int(min_per_store),
    _FAMILIES[dist1_label], float(dist1_cv),
    _FAMILIES[dist2_label], float(dist2_cv),
    float(price), float(var_cost), float(fixed_cost),
    int(runs_R),
)

# Surface when the presentation minimum has overridden the forecast-based buy.
forced_total = int(min_per_store) * int(n_sku) * int(n_store)
if min_per_store > 0 and forced_total > bought_by_st.min():
    st.warning(
        f"**Presentation minimum is binding.** Forcing {min_per_store} unit(s) "
        f"per store of every SKU locks **{forced_total:,} units** "
        f"({min_per_store} × {n_sku:,} SKUs × {n_store} stores). Where that "
        "exceeds the forecast-based buy, the buy is raised to honour it — so "
        "you over-buy, sell-through drops, and the hold-central lever loses "
        "its room to manoeuvre (there is no free stock left to position).",
        icon="📦")


# ─────────────────────────── derived metrics + headline ──────────────────
forecast_total = forecast_per_week * WEEKS
# Sales (€) = sold units × price. Sold per cell = sellthrough × bought / 100.
sales_eur  = sellt / 100.0 * bought_by_st[None, :] * price
# Margin (%) = margin / sales × 100. Guard against zero sales (impossible here
# in practice — every cell has positive expected sales — but cheap insurance).
with np.errstate(divide="ignore", invalid="ignore"):
    margin_pct = np.where(sales_eur > 0, margin / sales_eur * 100.0, 0.0)

def _best_card(label: str, value_str: str, grid: np.ndarray, accent: str) -> str:
    """Format one 'best cell' card (label, big value, location of the maximum)."""
    bi = np.unravel_index(grid.argmax(), grid.shape)
    return (
        f"<div style='flex:1; border:1px solid #e3e8ef; border-radius:8px; "
        f"padding:10px 14px; font-size:13px; border-top:3px solid {accent};'>"
        f"<span style='color:#5a6a80;'>{label}</span><br>"
        f"<b style='font-size:18px; color:{accent};'>{value_str}</b> "
        f"<span style='color:#5a6a80;'>at hold={HOLD_PCTS[bi[0]]}%, "
        f"target ST={TARGET_STS[bi[1]]}%</span></div>"
    )

st.markdown(
    f"<div style='display:flex; gap:10px; margin:14px 0 4px;'>"
    f"<div style='flex:0 0 170px; border:1px solid #e3e8ef; border-radius:8px; "
    f"padding:10px 14px; font-size:13px;'>"
    f"<span style='color:#5a6a80;'>Forecast season</span><br>"
    f"<b style='font-size:17px;'>{forecast_total:,} units</b><br>"
    f"<span style='color:#5a6a80; font-size:11px;'>"
    f"{R_used:,} MC rolls · {actual_s:.1f}s</span></div>"
    + _best_card("Best Sales (€)",        f"€{sales_eur.max():,.0f}", sales_eur,  "#1a8a4a")
    + _best_card("Best Margin (%)",       f"{margin_pct.max():.1f}%", margin_pct, "#1a8a4a")
    + _best_card("Best Sell-through (%)", f"{sellt.max():.1f}%",      sellt,      "#1a8a4a")
    + "</div>",
    unsafe_allow_html=True,
)


# ─────────────────────────── render every table ───────────────────────────
def _render_table(values: np.ndarray, *, title: str, text_fmt: str,
                   reverse_color: bool):
    """Render one altair heatmap of the (HOLD x TARGET_ST) grid."""
    row_labels = [f"{h}% (DUMP)" if h == 0 else f"{h}%" for h in HOLD_PCTS]
    col_labels = [f"{s}%\n(buy {b:,})" for s, b in zip(TARGET_STS, bought_by_st)]
    long_rows = [{"hold": row_labels[hi], "st": col_labels[ti],
                   "value": float(values[hi, ti])}
                  for hi in range(len(HOLD_PCTS)) for ti in range(len(TARGET_STS))]
    df_long = pd.DataFrame(long_rows)

    heat = alt.Chart(df_long).mark_rect(stroke="white", strokeWidth=2).encode(
        x=alt.X("st:O", sort=col_labels, title=None,
                axis=alt.Axis(orient="top", labelAngle=0,
                              labelFontSize=11, labelFontWeight="bold",
                              labelLineHeight=13)),
        y=alt.Y("hold:O", sort=row_labels, title="% kept central on day 1",
                axis=alt.Axis(labelFontSize=11, labelFontWeight="bold")),
        color=alt.Color("value:Q",
                         scale=alt.Scale(scheme="redyellowgreen",
                                          reverse=reverse_color),
                         legend=None),
        tooltip=[alt.Tooltip("hold:O", title="kept central"),
                 alt.Tooltip("st:O",   title="target sell-through"),
                 alt.Tooltip("value:Q", format=text_fmt, title=title)],
    )
    labels = alt.Chart(df_long).mark_text(fontSize=11, color="#1a2a40").encode(
        x=alt.X("st:O", sort=col_labels),
        y=alt.Y("hold:O", sort=row_labels),
        text=alt.Text("value:Q", format=text_fmt),
    )
    st.markdown(f"##### {title}")
    st.altair_chart(
        (heat + labels).properties(height=11 * 30 + 30),
        use_container_width=True,
    )


# All five tables, in the order the user asked for. Reverse colour only on
# "lost sales" (lower = better); everything else is "higher = better".
for _title, _values, _fmt, _reverse in [
    ("Sales (€)",          sales_eur,  ",.0f", False),
    ("Margin (€)",         margin,     ",.0f", False),
    ("Margin (%)",         margin_pct, ".1f",  False),
    ("Sell-through (%)",   sellt,      ".1f",  False),
    ("Lost sales (units)", lost,       ",.0f", True),
]:
    _render_table(_values, title=_title, text_fmt=_fmt, reverse_color=_reverse)

st.caption(
    f"Every cell averages {R_used:,} rolled seasons. Rows = % kept central on "
    "day 1; columns = target sell-through that fixes how much you bought "
    "(buy quantity in the header). "
    "**Notice the optima don't line up:** **Sales (€)** peaks at the *leftmost* "
    "column (buy more, sell more), while **Margin (€)** and **Margin (%)** peak "
    f"*bottom-right* — every unit you buy and don't sell costs €{var_cost:.0f} "
    "in stock, so over-buying erodes profit even when revenue rises. "
    "Push both CVs to 0 and the within-column gradient collapses: with no "
    "uncertainty, where stock starts doesn't matter."
)
