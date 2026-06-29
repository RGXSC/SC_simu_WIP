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
    "watch one product sell down over its life. Your chain follows the "
    "<b>80/20 rule</b>: the top <b>20% of stores generate 80% of demand</b>, "
    "while the other <b>80% of stores generate just 20%</b> — so a "
    "high-selling store sells about <b>16× as fast</b> as a low-selling one. "
    "You always place the product in your best stores first, so widening the "
    "network means reaching progressively weaker stores.</div>",
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
st.markdown(
    "<div style='font-size:13px; color:#5a6a80; margin:6px 0 2px;'>"
    "<b>Demand profile</b> — the shape of weekly sales over the product's "
    "life (each little curve is units sold per week, from launch on the left "
    "to end-of-life on the right). 'Ultra-steep' front-loads almost "
    "everything into the first weeks; 'Flat' spreads it evenly.</div>",
    unsafe_allow_html=True,
)
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

    # ── Three-band decomposition of all stock on hand, week by week ──
    # Stack order (bottom→top): stock on high-selling store shelves, stock
    # on low-selling store shelves, stock still in the central warehouse.
    # The three sum EXACTLY to total stock on hand. Stranded stock (units
    # placed in low-selling stores that demand never reaches) shows up as
    # the low-stores band staying high while weekly demand evaporates --
    # that's the misplacement lesson, visible without any extra band.
    # (Under demand = buy there is no genuine "above forecast" pool, so the
    # old fourth band was always ~0 and has been removed.)
    # Pre-sales snapshot (after the warehouse refills, before the week's
    # sales). Four stacked bands, summing to total stock on hand:
    #   1-2. stock on the high / low store shelves (the network).
    #   3.   the part of the WAREHOUSE that, on its own, would cover this
    #        week's forecast demand  = min(warehouse, this-week demand).
    #   4.   the rest of the warehouse -- stock held ABOVE this week's
    #        forecast, i.e. the cushion that could serve a demand surprise
    #        (overperform) or roll to later weeks.
    # The warehouse split (3 vs 4) is computed per week from that week's
    # demand; the network bands are left untouched.
    STACK = [
        ("In network — high-selling stores",                                "#1a6b3a"),
        ("In network — low-selling stores",                                 "#7fbf7b"),
        ("Warehouse — covers current week forecast while leaving network untouched", "#5a7fb0"),
        ("Warehouse — covers potential overperformance",                    "#d97757"),
    ]
    rows = []
    for s in r["states"]:
        d_week = (s.sold_high + s.lost_high) + (s.sold_low + s.lost_low)
        wh_cover = min(s.pre_wh, d_week)        # warehouse to meet this week
        wh_over  = s.pre_wh - wh_cover          # warehouse above this week
        vals = [s.pre_high, s.pre_low, wh_cover, wh_over]
        for order, (label, _col) in enumerate(STACK):
            rows.append({"week": s.week, "kind": label,
                         "stock": vals[order], "order": order})
    df_stock = pd.DataFrame(rows)
    stock_domain = [lbl for lbl, _ in STACK]
    stock_range  = [col for _, col in STACK]

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
                                                labelFontSize=12, columns=1,
                                                labelLimit=0, symbolLimit=0)),
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

    # Grid axes: strict linear steps -- buy every 100 up to 2000, network
    # every 20 up to maison_size. Big enough to see the gradient clearly
    # in every column.
    @st.cache_data(show_spinner="Sweeping the (network × buy) grid…")
    def _run_grid(maison_size, lifespan_months, profile, price, var_cost):
        net_grid = sorted({n for n in range(20, int(maison_size) + 1, 20)})
        if not net_grid:
            net_grid = [int(maison_size)]
        buy_grid = list(range(100, 2001, 100))
        return simulate_grid(maison_size, net_grid, buy_grid,
                             lifespan_months, profile, price, var_cost)

    g = _run_grid(int(maison_size), float(lifespan_months), profile,
                  float(price), float(var_cost))
    nets, buys = g["sku_networks"], g["buys"]

    # Per-column gradient: each column (each buy quantity) is normalised
    # independently to its own (min, max), so you SEE the optimal AND the
    # suboptimal-but-still-decent zones within each column. Best cell(s)
    # within 1% of the column max get a bold outline on top.
    TOL = 0.01

    def _short_money(v: float) -> str:
        """Compact, rounded label for euro values: 1234567 -> "1.2M",
        56700 -> "57K", 800 -> "800". One decimal only when it adds info."""
        a = abs(v)
        if a >= 1_000_000:
            return f"{v/1_000_000:.1f}M"
        if a >= 10_000:
            return f"{v/1000:.0f}K"
        if a >= 1_000:
            return f"{v/1000:.1f}K"
        return f"{v:.0f}"

    def _short_pct(v: float) -> str:
        return f"{v:.1f}%"

    def _grid_chart(matrix, title, kind="money"):
        x_sorted = sorted(set(int(b) for b in buys))
        y_sorted = sorted(set(int(s) for s in nets))
        fmt_cell = _short_pct if kind == "pct" else _short_money
        tooltip_fmt = ".1f" if kind == "pct" else ",.0f"

        rows = []
        for j, N in enumerate(buys):
            col = matrix[:, j]
            col_max = float(col.max()); col_min = float(col.min())
            thresh = col_max * (1.0 - TOL) if col_max >= 0 else col_max * (1.0 + TOL)
            span = col_max - col_min
            # A column whose best and worst differ by less than the 1%
            # tolerance is effectively FLAT -- every store count is just as
            # good for that buy. Paint it all best-green instead of letting
            # the per-column normalisation amplify rounding noise into a
            # misleading pale-to-green gradient (the stray "white" cells).
            col_flat = span <= TOL * abs(col_max)
            for i, S in enumerate(nets):
                v = float(matrix[i, j])
                rel = 1.0 if (col_flat or span <= 0) else (v - col_min) / span
                rows.append({"S": int(S), "N": int(N), "value": v,
                             "rel": rel,
                             "label": fmt_cell(v),
                             "best": bool(col_flat or v >= thresh)})
        df = pd.DataFrame(rows)

        # Per-column gradient: off-white (worst in column) -> deep green
        # (best in column). Tells you "for THIS buy, here is how every
        # store count compares to the others".
        cells = (alt.Chart(df).mark_rect(stroke="#ffffff", strokeWidth=1)
                 .encode(
                     x=alt.X("N:O", sort=x_sorted,
                             axis=alt.Axis(orient="top", labelAngle=0,
                                           labelFontSize=9, labelFontWeight="bold"),
                             title="Units bought"),
                     y=alt.Y("S:O", sort=y_sorted,
                             axis=alt.Axis(labelFontSize=10, labelFontWeight="bold"),
                             title="Stores carrying the product"),
                     color=alt.Color("rel:Q",
                                      scale=alt.Scale(
                                          range=["#f6f3ee", "#f3d28b", "#d97757", "#5a8f5a", "#1a6b3a"],
                                          domain=[0.0, 0.4, 0.6, 0.8, 1.0]),
                                      legend=None),
                     tooltip=[alt.Tooltip("S:Q", title="Stores carrying the product"),
                              alt.Tooltip("N:Q", title="Units bought"),
                              alt.Tooltip("value:Q", format=tooltip_fmt, title=title)]
                 ))
        # Best cells get a bold dark outline on top of the gradient.
        marker = (alt.Chart(df[df["best"]]).mark_rect(
                     fill=None, stroke="#1a2a40", strokeWidth=2.5)
                  .encode(x=alt.X("N:O", sort=x_sorted),
                          y=alt.Y("S:O", sort=y_sorted)))
        labels = (alt.Chart(df).mark_text(fontSize=9)
                  .encode(x=alt.X("N:O", sort=x_sorted),
                          y=alt.Y("S:O", sort=y_sorted),
                          text=alt.Text("label:N"),
                          color=alt.condition("datum.rel > 0.75",
                                               alt.value("#ffffff"),
                                               alt.value("#1a2a40"))))

        return (cells + marker + labels).properties(
            height=len(nets) * 30 + 30,
            title=alt.TitleParams(text=title, fontSize=14, anchor="start"))

    st.altair_chart(_grid_chart(g["sales"],      "Sales in euros — optimal and sub-optimal store counts per buy"),
                    use_container_width=True)
    st.altair_chart(_grid_chart(g["margin"],     "Margin in euros — optimal and sub-optimal store counts per buy"),
                    use_container_width=True)
    st.altair_chart(_grid_chart(g["margin_pct"], "Margin percent — optimal and sub-optimal store counts per buy",
                                 kind="pct"),
                    use_container_width=True)

    st.caption(
        "Each column is one buy quantity. The colour gradient is computed "
        "within each column: pale = the worst store count for that buy, "
        "deep green = the best. Cells outlined in dark are within 1% of the "
        "column maximum — the optimal store count(s). Reading down a column "
        "tells you which store widths are great, decent and bad for that "
        "specific buy. The three tables disagree on purpose: **sales** "
        "rewards buying more and going wider; **margin in euros** rewards "
        "the biggest buy the network can still flow through; **margin "
        "percent** rewards staying narrow and shallow so you don't pay for "
        "stock that never sells."
    )
