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

lifespan_months = st.slider("Lifespan (months)",
                             min_value=1.0, max_value=6.0, value=2.0, step=0.5,
                             help="How long the SKU sells. The simulation horizon = "
                                  "lifespan × 4.33 weeks.")

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
    help="Curve shape over the SKU's life: where the peak sits and how sharp.",
)
for col, name in zip(profile_thumb_cols, profile_keys):
    with col:
        st.markdown(
            f"<div style='text-align:center; font-size:12px; font-weight:600; "
            f"color:{'#1a4a8a' if name == profile else '#5a6a80'};"
            f"margin:0 0 -10px;'>{name}</div>",
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
        f"You picked an SKU network of **{r['S_chosen']}** stores but only "
        f"bought **{r['bought']}** units. With 1 unit per store on day 1, "
        f"only **{r['S_effective']}** stores can actually be stocked. The "
        "matrix below and the chart use that effective network.",
        icon="⚠️")

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

    # Shared X-axis spec: weeks are INTEGERS, no 0.4 / 0.8 nonsense.
    # `tickMinStep=1` forces ≥1-week ticks and `format='d'` strips decimals.
    H = r["horizon"]
    week_axis = alt.Axis(format="d", tickMinStep=1,
                         labelFontSize=11, titleFontSize=12)
    week_scale = alt.Scale(domain=[0, H], nice=False)

    # Build a long-form dataframe from the per-week states. The numeric
    # `order` column controls the stacking sequence (LOW at bottom, then
    # HIGH, then warehouse on top). Legend labels match the sketch:
    # "Mid stock" = warehouse, "In store …" = on the shelf.
    rows = []
    for s in r["states"]:
        rows.append({"week": s.week, "kind": "In store (LOW)",
                     "stock": s.stock_low_total,  "order": 0})
        rows.append({"week": s.week, "kind": "In store (HIGH)",
                     "stock": s.stock_high_total, "order": 1})
        rows.append({"week": s.week, "kind": "Mid stock (WH)",
                     "stock": s.wh,               "order": 2})
    df_stock = pd.DataFrame(rows)

    # Weekly demand line (HIGH + LOW = total weekly demand on the SKU)
    df_demand = pd.DataFrame([
        {"week": s.week,
         "demand": (s.sold_high + s.lost_high) + (s.sold_low + s.lost_low)}
        for s in r["states"]
    ])

    # Per-week sales + lost (flat dataframe for the bar chart)
    rows_flow = []
    for s in r["states"][1:]:                # skip week 0 (pre-sales)
        rows_flow.append({"week": s.week, "kind": "% sold (HIGH)",
                          "units": s.sold_high})
        rows_flow.append({"week": s.week, "kind": "% sold (LOW)",
                          "units": s.sold_low})
        rows_flow.append({"week": s.week, "kind": "Lost sales (HIGH)",
                          "units": s.lost_high})
        rows_flow.append({"week": s.week, "kind": "Lost sales (LOW)",
                          "units": s.lost_low})
    df_flow = pd.DataFrame(rows_flow)

    # Coverage (% stocked) per tier — "% network well" in the sketch.
    rows_cov = []
    for s in r["states"]:
        rows_cov.append({"week": s.week, "tier": "HIGH-tier",
                         "pct": s.pct_high_stocked * 100})
        rows_cov.append({"week": s.week, "tier": "LOW-tier",
                         "pct": s.pct_low_stocked  * 100})
    df_cov = pd.DataFrame(rows_cov)

    palette = {
        "Mid stock (WH)":     "#9aa6b8",
        "In store (HIGH)":    "#1a8a4a",
        "In store (LOW)":     "#7fbf7b",
        "% sold (HIGH)":      "#1a8a4a",
        "% sold (LOW)":       "#7fbf7b",
        "Lost sales (HIGH)":  "#c0392b",
        "Lost sales (LOW)":   "#e88c7d",
    }
    domain_stock = ["In store (LOW)", "In store (HIGH)", "Mid stock (WH)"]

    # ── Chart A: stacked stock by location + actual demand as a line ──
    stock_area = (
        alt.Chart(df_stock)
        .mark_area(opacity=0.85, interpolate="monotone")
        .encode(
            x=alt.X("week:Q", title="week", axis=week_axis, scale=week_scale),
            y=alt.Y("stock:Q", title="units of stock", stack="zero"),
            color=alt.Color("kind:N",
                             scale=alt.Scale(domain=domain_stock,
                                              range=[palette[k] for k in domain_stock]),
                             legend=alt.Legend(title=None, orient="top",
                                                labelFontSize=12)),
            order=alt.Order("order:Q"),
            tooltip=[alt.Tooltip("week:Q", title="week", format="d"),
                     alt.Tooltip("kind:N",  title="location"),
                     alt.Tooltip("stock:Q", format=",.0f", title="units")],
        )
    )
    demand_line = (
        alt.Chart(df_demand)
        .mark_line(color="#1a4a8a", strokeWidth=2.5,
                    point=alt.OverlayMarkDef(filled=True, size=50,
                                              color="#1a4a8a"))
        .encode(
            x=alt.X("week:Q", axis=week_axis, scale=week_scale),
            y=alt.Y("demand:Q"),
            tooltip=[alt.Tooltip("week:Q", title="week", format="d"),
                     alt.Tooltip("demand:Q", format=",.0f",
                                  title="actual demand")],
        )
    )
    stock_chart = (
        (stock_area + demand_line)
        .properties(height=420,
                    title=alt.TitleParams(
                        text="Stock by location (areas) and actual demand (line)",
                        fontSize=14))
    )

    # ── Chart B: per-tier coverage — sketch's "% network well" ──
    cov_chart = (
        alt.Chart(df_cov)
        .mark_line(point=True, strokeWidth=3)
        .encode(
            x=alt.X("week:Q", title="week", axis=week_axis, scale=week_scale),
            y=alt.Y("pct:Q", title="% of tier's stores still able to sell",
                    scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("tier:N",
                             scale=alt.Scale(domain=["HIGH-tier", "LOW-tier"],
                                              range=["#1a8a4a", "#7fbf7b"]),
                             legend=alt.Legend(title=None, orient="top",
                                                labelFontSize=12)),
            tooltip=[alt.Tooltip("week:Q", title="week", format="d"),
                     alt.Tooltip("tier:N", title="tier"),
                     alt.Tooltip("pct:Q",  format=".1f", title="% stocked")],
        )
        .properties(height=320,
                    title=alt.TitleParams(
                        text="% network well — coverage week by week",
                        fontSize=14))
    )

    # ── Chart C: per-week sales & lost (split by tier) ──
    domain_flow = ["% sold (HIGH)", "% sold (LOW)",
                   "Lost sales (HIGH)", "Lost sales (LOW)"]
    flow_chart = (
        alt.Chart(df_flow)
        .mark_bar()
        .encode(
            x=alt.X("week:Q", title="week", axis=week_axis, scale=week_scale),
            y=alt.Y("units:Q", title="units"),
            color=alt.Color("kind:N",
                             scale=alt.Scale(domain=domain_flow,
                                              range=[palette[k] for k in domain_flow]),
                             legend=alt.Legend(title=None, orient="top",
                                                labelFontSize=12)),
            xOffset=alt.XOffset("kind:N"),
            tooltip=[alt.Tooltip("week:Q", title="week", format="d"),
                     alt.Tooltip("kind:N",  title="kind"),
                     alt.Tooltip("units:Q", format=",.0f", title="units")],
        )
        .properties(height=320,
                    title=alt.TitleParams(
                        text="Sales and lost demand each week, by tier",
                        fontSize=14))
    )

    st.altair_chart(stock_chart, use_container_width=True)
    st.altair_chart(cov_chart,   use_container_width=True)
    st.altair_chart(flow_chart,  use_container_width=True)

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
        # Use the INTEGER fields for the axis encoding so altair sorts
        # numerically (10, 30, 60, 120, 200, 300, 400, 500), not as strings
        # (10, 120, 200, 30, 300, 400, 500, 60). Same for the column axis.
        rows = []
        for i, S in enumerate(nets):
            for j, N in enumerate(buys):
                rows.append({"S": int(S), "N": int(N),
                             "value": float(matrix[i, j])})
        df = pd.DataFrame(rows)
        bi = np.unravel_index(matrix.argmax(), matrix.shape)
        best_S, best_N = int(nets[bi[0]]), int(buys[bi[1]])
        bv = float(matrix[bi])

        x_sorted = sorted(set(int(b) for b in buys))
        y_sorted = sorted(set(int(s) for s in nets))

        heat = (alt.Chart(df).mark_rect(stroke="white", strokeWidth=2)
                .encode(
                    x=alt.X("N:O", sort=x_sorted,
                            axis=alt.Axis(orient="top", labelAngle=0,
                                          labelFontSize=11, labelFontWeight="bold"),
                            title="Buy (units)"),
                    y=alt.Y("S:O", sort=y_sorted,
                            axis=alt.Axis(labelFontSize=11, labelFontWeight="bold"),
                            title="SKU network (stores)"),
                    color=alt.Color("value:Q",
                                     scale=alt.Scale(scheme=scheme,
                                                     reverse=reverse_color),
                                     legend=None),
                    tooltip=[alt.Tooltip("S:Q", title="SKU network"),
                             alt.Tooltip("N:Q", title="Buy"),
                             alt.Tooltip("value:Q", format=fmt, title=title)]
                ))
        labels = (alt.Chart(df).mark_text(fontSize=10, color="#1a2a40")
                  .encode(x=alt.X("N:O", sort=x_sorted),
                          y=alt.Y("S:O", sort=y_sorted),
                          text=alt.Text("value:Q", format=fmt)))

        # Bold outline on the best cell
        best_df = pd.DataFrame([{"S": best_S, "N": best_N, "value": bv}])
        marker = (alt.Chart(best_df).mark_rect(
                    fill=None, stroke=highlight_color, strokeWidth=4)
                  .encode(x=alt.X("N:O", sort=x_sorted),
                          y=alt.Y("S:O", sort=y_sorted)))

        return (heat + labels + marker).properties(
            height=len(nets) * 36 + 30,
            title=alt.TitleParams(
                text=f"{title}  (best @ S={best_S}, N={best_N} → {format(bv, fmt)})",
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
