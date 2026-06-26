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
    simulate, simulate_grid, horizon_weeks, PROFILES,
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
    "Buy depth × network size — one SKU, deterministic</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<div style='color:#5a6a80; font-size:14px; margin-bottom:14px;'>"
    "Pick a buy quantity and a network footprint for ONE product, watch it "
    "sell down across its life. Stores are split 20% HIGH-selling / 80% "
    "LOW-selling (the 80/20 rule, anchored on your maison size). The SKU "
    "goes <b>top-down</b>: best stores first.</div>",
    unsafe_allow_html=True,
)


# ─────────────────────────── inputs ───────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
with c1:
    maison_size = st.slider("Maison network (total stores)",
                             min_value=10, max_value=500, value=300, step=10,
                             help="Your chain size. 20% are HIGH-selling stores "
                                  "that generate 80% of demand; the other 80% are "
                                  "LOW-selling.")
with c2:
    sku_network = st.slider("SKU network (stores selling THIS product)",
                             min_value=10, max_value=int(maison_size),
                             value=min(int(maison_size * HIGH_SHARE), int(maison_size)),
                             step=5,
                             help="How many of your stores you put this SKU in. "
                                  "Top-down: best stores first. With maison=300, "
                                  "the first 60 stores are HIGH-tier; past that "
                                  "you start adding LOW-tier stores.")
with c3:
    buy = st.slider("Buy (units of this SKU)",
                     min_value=10, max_value=2000, value=500, step=10,
                     help="Total units of the SKU you order. 1 unit per "
                          "SKU-network store sits on the shelf day 1; the rest "
                          "sits in the warehouse for refill.")
with c4:
    price    = st.slider("Selling price (€/unit)",
                          min_value=50, max_value=5000, value=2000, step=50)
    var_cost = st.slider("Cost of goods (€/unit)",
                          min_value=10, max_value=500, value=350, step=10)

l1, l2 = st.columns([1, 2])
with l1:
    lifespan_months = st.slider("Lifespan (months)",
                                 min_value=1.0, max_value=6.0, value=2.0, step=0.5,
                                 help="How long the SKU sells. The simulation "
                                      "horizon = lifespan × 4.33 weeks.")
with l2:
    profile = st.radio("Demand profile",
                        list(PROFILES.keys()), index=2, horizontal=True,
                        help="Curve shape over the life: where the peak sits, "
                             "how sharp it is.")


# ─────────────────────────── run + headline ───────────────────────────────
r = simulate(maison_size, sku_network, buy, lifespan_months, profile,
             price=float(price), var_cost=float(var_cost))

c_a, c_b, c_c, c_d, c_e = st.columns(5)
with c_a:
    st.metric("Bought (units)", f"{r['bought']:,}")
with c_b:
    st.metric("Sold", f"{r['sold']:,.0f}",
              f"{r['sell_through_pct']:.1f}% sell-through")
with c_c:
    st.metric("Lost (units)", f"{r['lost']:,.0f}")
with c_d:
    st.metric("Margin (€)", f"€{r['margin']:,.0f}",
              f"{r['margin_pct']:.1f}% of sales")
with c_e:
    st.metric("Network split",
              f"{r['S_high']}H / {r['S_low']}L",
              f"horizon {r['horizon']} wk")


# ═══════════════════════════ REVEAL 1: week-by-week ═══════════════════════
with st.expander("\U0001F4CA  Reveal 1 — What happens week by week",
                  expanded=True):

    # Build a long-form dataframe from the per-week states. The numeric
    # `order` column controls the stacking sequence (LOW at bottom, then
    # HIGH, then warehouse on top).
    rows = []
    for s in r["states"]:
        rows.append({"week": s.week, "kind": "LOW-tier stores",
                     "stock": s.stock_low_total,  "order": 0})
        rows.append({"week": s.week, "kind": "HIGH-tier stores",
                     "stock": s.stock_high_total, "order": 1})
        rows.append({"week": s.week, "kind": "Warehouse",
                     "stock": s.wh,               "order": 2})
    df_stock = pd.DataFrame(rows)

    # Per-week sales + lost (flat dataframe for two charts)
    rows_flow = []
    for s in r["states"][1:]:                # skip week 0 (pre-sales)
        rows_flow.append({"week": s.week, "kind": "Sold (HIGH)",
                          "units": s.sold_high})
        rows_flow.append({"week": s.week, "kind": "Sold (LOW)",
                          "units": s.sold_low})
        rows_flow.append({"week": s.week, "kind": "Lost (HIGH)",
                          "units": s.lost_high})
        rows_flow.append({"week": s.week, "kind": "Lost (LOW)",
                          "units": s.lost_low})
    df_flow = pd.DataFrame(rows_flow)

    # Coverage (% stocked) per tier
    rows_cov = []
    for s in r["states"]:
        rows_cov.append({"week": s.week, "tier": "HIGH-tier stores",
                         "pct": s.pct_high_stocked * 100})
        rows_cov.append({"week": s.week, "tier": "LOW-tier stores",
                         "pct": s.pct_low_stocked  * 100})
    df_cov = pd.DataFrame(rows_cov)

    palette = {
        "Warehouse":         "#9aa6b8",
        "HIGH-tier stores":  "#1a8a4a",
        "LOW-tier stores":   "#7fbf7b",
        "Sold (HIGH)":       "#1a8a4a",
        "Sold (LOW)":        "#7fbf7b",
        "Lost (HIGH)":       "#c0392b",
        "Lost (LOW)":        "#e88c7d",
    }
    domain_stock = ["LOW-tier stores", "HIGH-tier stores", "Warehouse"]

    # ── Chart A: stacked stock by location ──
    stock_chart = (
        alt.Chart(df_stock)
        .mark_area(opacity=0.85, interpolate="monotone")
        .encode(
            x=alt.X("week:Q", title="week",
                    scale=alt.Scale(domain=[0, r["horizon"]])),
            y=alt.Y("stock:Q", title="units of stock",
                    stack="zero"),
            color=alt.Color("kind:N",
                             scale=alt.Scale(domain=domain_stock,
                                              range=[palette[k] for k in domain_stock]),
                             legend=alt.Legend(title=None, orient="top")),
            order=alt.Order("order:Q"),
            tooltip=["week:Q", "kind:N",
                     alt.Tooltip("stock:Q", format=",.0f")],
        )
        .properties(height=250, title="Stock by location, week by week")
    )

    # ── Chart B: per-tier coverage ──
    cov_chart = (
        alt.Chart(df_cov)
        .mark_line(point=True, strokeWidth=3)
        .encode(
            x=alt.X("week:Q", title="week",
                    scale=alt.Scale(domain=[0, r["horizon"]])),
            y=alt.Y("pct:Q", title="% of tier's stores still able to sell",
                    scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("tier:N",
                             scale=alt.Scale(domain=["HIGH-tier stores",
                                                      "LOW-tier stores"],
                                              range=["#1a8a4a", "#7fbf7b"]),
                             legend=alt.Legend(title=None, orient="top")),
            tooltip=["week:Q", "tier:N",
                     alt.Tooltip("pct:Q", format=".1f")],
        )
        .properties(height=200,
                    title="Coverage: % of each tier's network still stocked")
    )

    # ── Chart C: per-week sales & lost (split by tier) ──
    domain_flow = ["Sold (HIGH)", "Sold (LOW)", "Lost (HIGH)", "Lost (LOW)"]
    flow_chart = (
        alt.Chart(df_flow)
        .mark_bar()
        .encode(
            x=alt.X("week:Q", title="week"),
            y=alt.Y("units:Q", title="units"),
            color=alt.Color("kind:N",
                             scale=alt.Scale(domain=domain_flow,
                                              range=[palette[k] for k in domain_flow]),
                             legend=alt.Legend(title=None, orient="top")),
            xOffset=alt.XOffset("kind:N"),
            tooltip=["week:Q", "kind:N",
                     alt.Tooltip("units:Q", format=",.1f")],
        )
        .properties(height=200,
                    title="Sales and lost demand each week, by tier")
    )

    st.altair_chart(stock_chart, use_container_width=True)
    cc1, cc2 = st.columns(2)
    with cc1:
        st.altair_chart(cov_chart, use_container_width=True)
    with cc2:
        st.altair_chart(flow_chart, use_container_width=True)

    st.caption(
        f"With **maison = {maison_size}** there are "
        f"**{r['M_high']} HIGH** and **{r['M_low']} LOW** stores. Your SKU "
        f"network of **{sku_network}** stores top-down captures "
        f"**{r['S_high']} HIGH** + **{r['S_low']} LOW**. The HIGH tier "
        f"sells {HIGH_RATE_MULT:.0f}× faster per store than the LOW tier."
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

    # Build long-form dataframe combining all three metrics, with separate
    # 'best' columns we can use to draw the highlighted markers.
    def _grid_chart(matrix, title, fmt, scheme="redyellowgreen",
                     reverse_color=False, highlight_color="#1a2a40"):
        rows = []
        for i, S in enumerate(nets):
            for j, N in enumerate(buys):
                rows.append({"S": f"{S}", "N": f"{N}",
                             "S_int": S, "N_int": N,
                             "value": float(matrix[i, j])})
        df = pd.DataFrame(rows)
        bi = np.unravel_index(matrix.argmax(), matrix.shape)
        best = (f"{nets[bi[0]]}", f"{buys[bi[1]]}")
        bv = float(matrix[bi])

        heat = (alt.Chart(df).mark_rect(stroke="white", strokeWidth=2)
                .encode(
                    x=alt.X("N:O", sort=[f"{n}" for n in buys],
                            axis=alt.Axis(orient="top", labelAngle=0,
                                          labelFontSize=11, labelFontWeight="bold"),
                            title="Buy (units)"),
                    y=alt.Y("S:O", sort=[f"{n}" for n in nets],
                            axis=alt.Axis(labelFontSize=11, labelFontWeight="bold"),
                            title="SKU network (stores)"),
                    color=alt.Color("value:Q",
                                     scale=alt.Scale(scheme=scheme,
                                                     reverse=reverse_color),
                                     legend=None),
                    tooltip=[alt.Tooltip("S_int:Q", title="SKU network"),
                             alt.Tooltip("N_int:Q", title="Buy"),
                             alt.Tooltip("value:Q", format=fmt, title=title)]
                ))
        labels = (alt.Chart(df).mark_text(fontSize=10, color="#1a2a40")
                  .encode(x="N:O", y="S:O", text=alt.Text("value:Q", format=fmt)))

        # Draw a bold outline on the best cell
        best_df = pd.DataFrame([{"S": best[0], "N": best[1], "value": bv}])
        marker = (alt.Chart(best_df).mark_rect(
                    fill=None, stroke=highlight_color, strokeWidth=4)
                  .encode(x="N:O", y="S:O"))

        return (heat + labels + marker).properties(
            height=len(nets) * 36 + 30,
            title=alt.TitleParams(text=f"{title}  (best @ S={best[0]}, N={best[1]} → {format(bv, fmt)})",
                                   fontSize=12, anchor="start"))

    st.altair_chart(
        _grid_chart(g["sales"],     "Sales (€)",   ",.0f",
                     highlight_color="#c0392b"),
        use_container_width=True)
    st.altair_chart(
        _grid_chart(g["margin"],    "Margin (€)",  ",.0f",
                     highlight_color="#1f5fa8"),
        use_container_width=True)
    st.altair_chart(
        _grid_chart(g["margin_pct"], "Margin (%)", ".1f",
                     highlight_color="#1a8a4a"),
        use_container_width=True)

    st.caption(
        "The three optima rarely land in the same cell. **Sales (€)** "
        "favours buying more *and* widening the network (more places to "
        "sell, even if the marginal store is small). **Margin (€)** likes "
        "the largest buy that the network can still flow through. "
        "**Margin (%)** prefers narrower / shallower — keep stock close to "
        "the HIGH-tier stores, don't pay for stuck stock in slow stores."
    )
