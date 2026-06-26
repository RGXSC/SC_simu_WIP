"""Single-SKU lifecycle teaching page.

You buy N units of one SKU and choose to sell it in S of your M maison
stores. The page shows what happens week by week and where the optimal
(network, buy) cell sits for sales / margin (€) / margin (%).

Deterministic, by design — built on top of sim_lifecycle.simulate(). All
the randomness lives in the Monte-Carlo page; this one isolates the
buy-depth × network-size trade-off.
"""
from __future__ import annotations
import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from sim_lifecycle import (
    simulate, simulate_grid, horizon_weeks, demand_curve, PROFILES,
    HIGH_SHARE, HIGH_RATE_MULT,
)
from ui_nav import top_nav

st.set_page_config(layout="wide", page_title="Buy × Network lifecycle",
                   page_icon="\U0001F3AF")

st.markdown("""
<style>
.block-container { padding-top: 1.4rem; max-width: 1400px; }
section[data-testid="stSidebar"] { width: 0 !important; min-width: 0 !important; }
</style>
""", unsafe_allow_html=True)

top_nav("pages/Lifecycle.py")

st.markdown(
    "<h1 style='margin:0 0 4px 0; font-size:28px;'>\U0001F3AF "
    "Buy depth × network size — one product, step by step</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<div style='color:#5a6a80; font-size:14px; margin-bottom:14px;'>"
    "Pick how many units to buy and how many stores to sell them in, then "
    "watch one product sell down over its life. Your stores split into "
    "20% high-selling and 80% low-selling (the 80/20 rule, measured on your "
    "whole chain). You always place the product in your best stores "
    "first.</div>",
    unsafe_allow_html=True,
)


# ─────────────────────────── inputs ───────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
with c1:
    maison_size = st.slider("Stores in the whole chain",
                             min_value=10, max_value=500, value=300, step=10,
                             help="Your total store count. 20% are high-selling "
                                  "stores that generate 80% of demand; the other "
                                  "80% are low-selling.")
with c2:
    sku_network = st.slider("Stores selling this product",
                             min_value=10, max_value=int(maison_size),
                             value=min(int(maison_size * HIGH_SHARE), int(maison_size)),
                             step=5,
                             help="How many of your stores carry this product. "
                                  "Best stores first: with 300 stores in the chain, "
                                  "the first 60 are high-selling; beyond that you "
                                  "start adding low-selling stores.")
with c3:
    buy = st.slider("Units bought of this product",
                     min_value=10, max_value=2000, value=500, step=10,
                     help="Total units you order. One unit per store sits on the "
                          "shelf on day one; the rest waits in the warehouse for "
                          "instant refill.")
with c4:
    price    = st.slider("Selling price (euros per unit)",
                          min_value=50, max_value=5000, value=2000, step=50)
    var_cost = st.slider("Cost of goods (euros per unit)",
                          min_value=10, max_value=500, value=350, step=10)

lifespan_months = st.slider("Lifespan (months)",
                             min_value=1.0, max_value=6.0, value=2.0, step=0.5,
                             help="How long the product sells. The simulation runs "
                                  "for lifespan × 4.33 weeks.")

# ── Demand profile picker with curve thumbnails ───────────────────────────
# Matches the user's sketch: show a tiny curve of each shape next to its
# name so you pick visually, not from a word in a radio button. The radio
# stays as the source of truth; the thumbnails are decoration that updates
# when you pick a new shape (the selected one renders in the accent blue).
profile_keys = list(PROFILES.keys())
_h_preview = horizon_weeks(lifespan_months)
profile_thumb_cols = st.columns(len(profile_keys))

def _thumb_chart(name: str, selected: bool) -> alt.Chart:
    w = demand_curve(_h_preview, name)
    df = pd.DataFrame({"week": np.arange(1, _h_preview + 1), "demand": w})
    accent = "#1a4a8a" if selected else "#cfcfcf"
    return (
        alt.Chart(df)
        .mark_area(opacity=0.85, color=accent, interpolate="monotone")
        .encode(
            x=alt.X("week:Q", axis=None,
                    scale=alt.Scale(domain=[1, _h_preview])),
            y=alt.Y("demand:Q", axis=None),
        )
        .properties(height=60)
    )

# Render the thumbnail row first, then the radio below. (We need to know
# the selected profile to highlight the thumb, so the radio happens first
# in code -- visually it sits below.)
profile = st.radio(
    "Demand profile shape", profile_keys, index=2, horizontal=True,
    label_visibility="collapsed",
    help="Curve shape over the product's life: where the peak sits and how sharp.",
)
for col, name in zip(profile_thumb_cols, profile_keys):
    with col:
        st.markdown(
            f"<div style='text-align:center; font-size:12.5px; font-weight:600; "
            f"line-height:1.5; padding-bottom:3px; "
            f"color:{'#1a4a8a' if name == profile else '#5a6a80'};'>{name}</div>",
            unsafe_allow_html=True,
        )
        st.altair_chart(_thumb_chart(name, name == profile),
                         use_container_width=True)


# ─────────────────────────── run + headline ───────────────────────────────
r = simulate(maison_size, sku_network, buy, lifespan_months, profile,
             price=float(price), var_cost=float(var_cost))

# Surface when the user's network choice exceeds the buy (1 unit/store rule
# caps it). We don't fail silently -- the metric strip below still reflects
# the EFFECTIVE network, but this banner explains why.
if r["S_effective"] < r["S_chosen"]:
    st.warning(
        f"You chose **{r['S_chosen']}** stores but only bought "
        f"**{r['bought']}** units. With one unit per store on day one, only "
        f"**{r['S_effective']}** stores can actually be stocked. The tables "
        "below and the charts use that smaller store count.",
        icon="⚠️")

c_a, c_b, c_c, c_d, c_e = st.columns(5)
with c_a:
    st.metric("Units bought", f"{r['bought']:,}")
with c_b:
    st.metric("Units sold", f"{r['sold']:,.0f}",
              f"{r['sell_through_pct']:.1f}% sell-through")
with c_c:
    st.metric("Units lost", f"{r['lost']:,.0f}")
with c_d:
    st.metric("Margin (euros)", f"€{r['margin']:,.0f}",
              f"{r['margin_pct']:.1f}% of sales")
with c_e:
    st.metric("Stores carrying the product",
              f"{r['S_high']} high + {r['S_low']} low",
              f"selling for {r['horizon']} weeks")


# ═══════════════════════════ REVEAL 1: week-by-week ═══════════════════════
with st.expander("\U0001F4CA  Reveal 1 — What happens week by week",
                  expanded=True):

    # Shared X-axis spec: weeks are INTEGERS, no 0.4 / 0.8 nonsense.
    # `tickMinStep=1` forces ≥1-week ticks and `format='d'` strips decimals.
    H = r["horizon"]
    week_axis = alt.Axis(format="d", tickMinStep=1,
                         labelFontSize=11, titleFontSize=12)
    week_scale = alt.Scale(domain=[0, H], nice=False)

    # ── Four-way decomposition of all stock on hand, week by week ──
    # Stack order (bottom→top): stock in high-selling stores (raw), stock
    # in low-selling stores (raw), warehouse stock committed to remaining
    # forecast demand, warehouse buffer beyond forecast needs. The four
    # series sum to total stock on hand. Stranded stock (units placed in
    # low-selling stores that won't get served by demand) shows up as the
    # low-stores band staying high while demand evaporates.
    STOCK_SERIES = [
        ("Stock in high-selling stores",         "stock_high_total", 0, "#1a6b3a"),
        ("Stock in low-selling stores",          "stock_low_total",  1, "#7fbf7b"),
        ("Available to perform (in warehouse)",  "wh_perform",       2, "#5a7fb0"),
        ("Available to overperform (in warehouse)", "wh_overperform", 3, "#c9d2de"),
    ]
    rows = []
    for s in r["states"]:
        for label, attr, order, _col in STOCK_SERIES:
            rows.append({"week": s.week, "kind": label,
                         "stock": getattr(s, attr), "order": order})
    df_stock = pd.DataFrame(rows)
    stock_domain = [lbl for lbl, *_ in STOCK_SERIES]
    stock_range  = [col for *_, col in STOCK_SERIES]

    # Weekly actual demand (high + low) as a line on top of the areas.
    df_demand = pd.DataFrame([
        {"week": s.week,
         "demand": (s.sold_high + s.lost_high) + (s.sold_low + s.lost_low)}
        for s in r["states"]
    ])

    # Per-week sales + lost, split by store tier — STACKED (one wide bar per
    # week) so the bars are large and each week reads as total demand.
    FLOW_SERIES = [
        ("Sold by high-selling stores", "sold_high", 0, "#1a6b3a"),
        ("Sold by low-selling stores",  "sold_low",  1, "#7fbf7b"),
        ("Lost by high-selling stores", "lost_high", 2, "#c0392b"),
        ("Lost by low-selling stores",  "lost_low",  3, "#e88c7d"),
    ]
    rows_flow = []
    for s in r["states"][1:]:                # skip week 0 (pre-sales)
        for label, attr, order, _col in FLOW_SERIES:
            rows_flow.append({"week": s.week, "kind": label,
                              "units": getattr(s, attr), "order": order})
    df_flow = pd.DataFrame(rows_flow)
    flow_domain = [lbl for lbl, *_ in FLOW_SERIES]
    flow_range  = [col for *_, col in FLOW_SERIES]

    # Coverage (% of each tier's stores still holding stock).
    rows_cov = []
    for s in r["states"]:
        rows_cov.append({"week": s.week, "tier": "High-selling stores",
                         "pct": s.pct_high_stocked * 100})
        rows_cov.append({"week": s.week, "tier": "Low-selling stores",
                         "pct": s.pct_low_stocked  * 100})
    df_cov = pd.DataFrame(rows_cov)

    # ── Chart A: stacked stock decomposition + actual demand line ──
    stock_area = (
        alt.Chart(df_stock)
        .mark_area(opacity=0.9, interpolate="monotone")
        .encode(
            x=alt.X("week:Q", title="week", axis=week_axis, scale=week_scale),
            y=alt.Y("stock:Q", title="units of stock", stack="zero"),
            color=alt.Color("kind:N",
                             scale=alt.Scale(domain=stock_domain, range=stock_range),
                             legend=alt.Legend(title=None, orient="top",
                                                labelFontSize=12, columns=2)),
            order=alt.Order("order:Q"),
            tooltip=[alt.Tooltip("week:Q", title="week", format="d"),
                     alt.Tooltip("kind:N",  title="stock pool"),
                     alt.Tooltip("stock:Q", format=",.0f", title="units")],
        )
    )
    demand_line = (
        alt.Chart(df_demand)
        .mark_line(color="#1a2a40", strokeWidth=2.5,
                    point=alt.OverlayMarkDef(filled=True, size=55, color="#1a2a40"))
        .encode(
            x=alt.X("week:Q", axis=week_axis, scale=week_scale),
            y=alt.Y("demand:Q"),
            tooltip=[alt.Tooltip("week:Q", title="week", format="d"),
                     alt.Tooltip("demand:Q", format=",.0f",
                                  title="actual demand (units)")],
        )
    )
    stock_chart = (
        (stock_area + demand_line)
        .properties(height=440,
                    title=alt.TitleParams(
                        text="Where the stock sits each week (areas) and "
                             "actual weekly demand (line)",
                        fontSize=14))
    )

    # ── Chart B: per-tier coverage ──
    cov_chart = (
        alt.Chart(df_cov)
        .mark_line(point=True, strokeWidth=3)
        .encode(
            x=alt.X("week:Q", title="week", axis=week_axis, scale=week_scale),
            y=alt.Y("pct:Q", title="percent of the tier's stores still in stock",
                    scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("tier:N",
                             scale=alt.Scale(domain=["High-selling stores",
                                                      "Low-selling stores"],
                                              range=["#1a6b3a", "#7fbf7b"]),
                             legend=alt.Legend(title=None, orient="top",
                                                labelFontSize=12)),
            tooltip=[alt.Tooltip("week:Q", title="week", format="d"),
                     alt.Tooltip("tier:N", title="store tier"),
                     alt.Tooltip("pct:Q",  format=".1f", title="percent in stock")],
        )
        .properties(height=320,
                    title=alt.TitleParams(
                        text="How much of each tier's network is still in stock",
                        fontSize=14))
    )

    # ── Chart C: per-week sales & lost, STACKED (large bars) ──
    flow_chart = (
        alt.Chart(df_flow)
        .mark_bar(size=26)
        .encode(
            x=alt.X("week:Q", title="week", axis=week_axis, scale=week_scale),
            y=alt.Y("units:Q", title="units", stack="zero"),
            color=alt.Color("kind:N",
                             scale=alt.Scale(domain=flow_domain, range=flow_range),
                             legend=alt.Legend(title=None, orient="top",
                                                labelFontSize=12, columns=2)),
            order=alt.Order("order:Q"),
            tooltip=[alt.Tooltip("week:Q", title="week", format="d"),
                     alt.Tooltip("kind:N",  title="flow"),
                     alt.Tooltip("units:Q", format=",.0f", title="units")],
        )
        .properties(height=340,
                    title=alt.TitleParams(
                        text="Units sold and units lost each week, by store tier",
                        fontSize=14))
    )

    st.altair_chart(stock_chart, use_container_width=True)
    st.altair_chart(cov_chart,   use_container_width=True)
    st.altair_chart(flow_chart,  use_container_width=True)

    st.caption(
        f"Your chain has **{r['M_high']} high-selling** and **{r['M_low']} "
        f"low-selling** stores. Placing the product in your best "
        f"**{sku_network}** stores reaches **{r['S_high']} high-selling** + "
        f"**{r['S_low']} low-selling**. A high-selling store sells "
        f"{HIGH_RATE_MULT:.0f} times faster than a low-selling one. "
        "Replenishment from the warehouse is **instant** (no delay), so a "
        "store only loses a sale when the warehouse itself has run dry — "
        "which happens when too much stock was committed on day one to "
        "low-selling stores that will never sell it (the grey "
        "**available to overperform** band)."
    )


# ═══════════════════════════ REVEAL 2: optimal matrix ═════════════════════
with st.expander("\U0001F4C8  Reveal 2 — Optimal (Network × Buy) matrix",
                  expanded=False):
    st.markdown(
        "<div style='color:#5a6a80; font-size:13px; margin-bottom:8px;'>"
        "We re-run the deterministic simulation across a grid of network "
        "sizes and buy quantities, then highlight the cell that maximises "
        "each metric.</div>", unsafe_allow_html=True)

    # Grid axes
    @st.cache_data(show_spinner="Sweeping the (network × buy) grid…")
    def _run_grid(maison_size, lifespan_months, profile, price, var_cost):
        # Log-ish network ladder up to maison_size
        net_grid = sorted(set([10, 30, 60, 120, 200, 300, 400, 500]))
        net_grid = [n for n in net_grid if n <= maison_size]
        if maison_size not in net_grid:
            net_grid.append(maison_size)
        net_grid = sorted(set(net_grid))
        # Buy ladder
        buy_grid = [50, 100, 200, 400, 700, 1000, 1400, 2000]
        return simulate_grid(maison_size, net_grid, buy_grid,
                             lifespan_months, profile, price, var_cost)

    g = _run_grid(int(maison_size), float(lifespan_months), profile,
                  float(price), float(var_cost))
    nets, buys = g["sku_networks"], g["buys"]

    # For each column (each buy quantity) we highlight the best network
    # size(s) — the rows within 1% of that column's maximum. No colour
    # gradient: cells are plain, only the per-column winners are tinted.
    # This reads as "for THIS buy, here is the best store count to choose".
    TOL = 0.01

    def _grid_chart(matrix, title, fmt=",.0f", highlight="#1a6b3a"):
        x_sorted = sorted(set(int(b) for b in buys))
        y_sorted = sorted(set(int(s) for s in nets))

        rows = []
        for j, N in enumerate(buys):
            col = matrix[:, j]
            col_max = float(col.max())
            thresh = col_max * (1.0 - TOL) if col_max >= 0 else col_max * (1.0 + TOL)
            for i, S in enumerate(nets):
                v = float(matrix[i, j])
                rows.append({"S": int(S), "N": int(N), "value": v,
                             "best": bool(v >= thresh)})
        df = pd.DataFrame(rows)

        # Background: best cells tinted, the rest plain white. No gradient.
        cells = (alt.Chart(df).mark_rect(stroke="#e6e6e6", strokeWidth=1)
                 .encode(
                     x=alt.X("N:O", sort=x_sorted,
                             axis=alt.Axis(orient="top", labelAngle=0,
                                           labelFontSize=11, labelFontWeight="bold"),
                             title="Units bought"),
                     y=alt.Y("S:O", sort=y_sorted,
                             axis=alt.Axis(labelFontSize=11, labelFontWeight="bold"),
                             title="Stores carrying the product"),
                     color=alt.Color("best:N",
                                      scale=alt.Scale(domain=[True, False],
                                                      range=[highlight, "#ffffff"]),
                                      legend=None),
                     tooltip=[alt.Tooltip("S:Q", title="Stores carrying the product"),
                              alt.Tooltip("N:Q", title="Units bought"),
                              alt.Tooltip("value:Q", format=fmt, title=title)]
                 ))
        # Best cells get a bold dark outline on top of the tint.
        marker = (alt.Chart(df[df["best"]]).mark_rect(
                     fill=None, stroke="#1a2a40", strokeWidth=2.5)
                  .encode(x=alt.X("N:O", sort=x_sorted),
                          y=alt.Y("S:O", sort=y_sorted)))
        labels = (alt.Chart(df).mark_text(fontSize=10)
                  .encode(x=alt.X("N:O", sort=x_sorted),
                          y=alt.Y("S:O", sort=y_sorted),
                          text=alt.Text("value:Q", format=fmt),
                          color=alt.condition("datum.best",
                                               alt.value("#ffffff"),
                                               alt.value("#1a2a40"))))

        return (cells + marker + labels).properties(
            height=len(nets) * 38 + 30,
            title=alt.TitleParams(text=title, fontSize=14, anchor="start"))

    st.altair_chart(_grid_chart(g["sales"],      "Sales in euros — best store count for each buy"),
                    use_container_width=True)
    st.altair_chart(_grid_chart(g["margin"],     "Margin in euros — best store count for each buy"),
                    use_container_width=True)
    st.altair_chart(_grid_chart(g["margin_pct"], "Margin percent — best store count for each buy",
                                 fmt=".1f"),
                    use_container_width=True)

    st.caption(
        "Each column is one buy quantity. The highlighted cell(s) are the "
        "store count that gives the best result for that buy (everything "
        "within 1% of the column's best is highlighted, so a near-tie shows "
        "as more than one cell). Reading down a column tells you how wide a "
        "network to choose once you have decided how much to buy. The three "
        "tables disagree on purpose: **sales in euros** rewards buying more "
        "and going wider; **margin in euros** rewards the biggest buy the "
        "network can still flow through; **margin percent** rewards staying "
        "narrow and shallow so you do not pay for stock that never sells."
    )
