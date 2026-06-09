"""Regional 2-RW supply chain — interactive page.

Drop-in alongside the main app. Streamlit auto-discovers any .py file
in pages/ and adds it to the left-rail navigation.

UI surface for the sim_regional.run_simulation_regional engine:
  - Region split slider (10..90, step 10)
  - 5 stage LTs (incl. new CW->RW)
  - Initial stock split: Material / Semi / CW / RW / Stores
  - 9 quick-preset buttons (3 splits x 3 distributions)
  - Aggregate KPI row + per-region row (Service A|B, Sales A|B, Missed A|B, Stockout A|B)
  - 9-cell grid summary (runs the spec grid in-page and shows margins)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import streamlit as st
import pandas as pd
import altair as alt
import sim_regional as sim
# Import seasonal-curve helpers from the main app (defined at module top)
from app import seasonal_curve, seasonal_curve_float, TIER_SHARE

st.set_page_config(layout="wide", page_title="Regional 2-RW Simulator", page_icon="\U0001F30D")
st.title("\U0001F30D  Regional 2-RW Simulator")
st.caption("Chain: Supplier → Material → Semi → FP → **CW → RW A / RW B → Stores**. "
           "Planner discovers the regional demand split at the first review.")

with st.expander("ℹ️ Model rules", expanded=False):
    st.markdown("""
**W0 stock** is split 50/50 between Region A and Region B (both RW and stores)
regardless of the demand-split slider. The operator does not yet know the
regional split at week 0; the planner discovers it at the first review and
the smart allocator routes the CW pool to whichever region is short from
then on.

**Within each region, store stock is split by tier-bucket share** — 40% to
the 5 high-selling stores, 46% to the 15 medium, 14% to the 30 small —
and uniform within each tier. Per-week per-store demand follows the same
15:6:1 weight ratio.

**Strict regional isolation**: stock at RW A only feeds Region A's stores;
same for B. No cross-region transfers. The CW pool can flow to either
region as the planner directs.
    """)

# ── Defaults (session_state persistence) ─────────────────────────────────
def _set(k, v):
    if k not in st.session_state: st.session_state[k] = v

_set("reg_split", 50)
_set("reg_weeks", 26)
_set("reg_n_per_region", 50)
_set("reg_demand_per_wk", 100)
_set("reg_demand_mode", "Flat")
_set("reg_seas_sub", "Steep")
_set("reg_seas_avg", 100)
_set("reg_mat_lt", 4)
_set("reg_semi_lt", 2)
_set("reg_fp_lt", 1)
_set("reg_cw_rw_lt", 1)
_set("reg_rw_store_lt", 1)
_set("reg_order_freq", 1)
_set("reg_total_init", 800)
_set("reg_dist_mat", 0)
_set("reg_dist_semi", 0)
_set("reg_dist_cw", 0)
_set("reg_dist_rw", 0)
_set("reg_dist_store", 100)
_set("reg_prod_cap_on", False)
_set("reg_prod_cap", 800)
_set("reg_smart_distrib", True)
_set("reg_price", 10.0)
_set("reg_var_cost", 5.0)
_set("reg_fixed_pct", 20)

# ── Sidebar ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Simulation")
    st.session_state.reg_weeks         = st.slider("Weeks", 4, 52, st.session_state.reg_weeks, key="w_reg_weeks")
    st.session_state.reg_n_per_region  = st.slider("Stores per region (N)", 10, 250, st.session_state.reg_n_per_region, step=10, key="w_reg_n")

    st.markdown("### \U0001F4C8 Demand")
    st.session_state.reg_demand_mode = st.radio(
        "Demand profile", ["Flat", "Seasonal"],
        index=0 if st.session_state.reg_demand_mode == "Flat" else 1,
        horizontal=True, key="w_reg_dmode",
    )
    if st.session_state.reg_demand_mode == "Flat":
        st.session_state.reg_demand_per_wk = st.slider(
            "Flat demand (pcs/wk)", 0, 500, st.session_state.reg_demand_per_wk, step=10, key="w_reg_dem")
    else:
        st.session_state.reg_seas_sub = st.radio(
            "Curve shape", ["Very Steep", "Steep", "~Flat"],
            index=["Very Steep","Steep","~Flat"].index(st.session_state.reg_seas_sub),
            horizontal=True, key="w_reg_seas_sub")
        st.session_state.reg_seas_avg = st.slider(
            "Seasonal avg (pcs/wk)", 0, 500, st.session_state.reg_seas_avg, step=10, key="w_reg_seas_avg",
            help="The actual demand curve will be scaled so its average matches this. "
                 "The planner's BELIEF is always avg=100 — it discovers the magnitude at first review.",
        )
        # Inline preview of the demand curve
        preview = seasonal_curve(st.session_state.reg_weeks, st.session_state.reg_seas_sub, st.session_state.reg_seas_avg)
        st.caption(f"Total demand: **{sum(preview)} pcs** · peak **{max(preview)}/wk** at W{preview.index(max(preview))+1}")

    st.markdown("### \U0001F4CD Region split")
    st.session_state.reg_split = st.slider(
        "% to Region A", 10, 90,
        st.session_state.reg_split, step=10, key="w_reg_split",
        help="Ground-truth demand share for Region A. The planner discovers this at the first review.",
    )
    st.caption(f"**A = {st.session_state.reg_split}%  ·  B = {100 - st.session_state.reg_split}%**")

    st.markdown("### \U0001F4E6 Lead times (weeks)")
    c1, c2 = st.columns(2)
    with c1:
        st.session_state.reg_mat_lt    = st.number_input("Material",  1, 20, st.session_state.reg_mat_lt,  1, key="w_reg_mat")
        st.session_state.reg_fp_lt     = st.number_input("FP prod.",  1, 20, st.session_state.reg_fp_lt,   1, key="w_reg_fp")
        st.session_state.reg_rw_store_lt = st.number_input("RW→Store", 1, 20, st.session_state.reg_rw_store_lt, 1, key="w_reg_rwst")
    with c2:
        st.session_state.reg_semi_lt   = st.number_input("Semi",      1, 20, st.session_state.reg_semi_lt, 1, key="w_reg_semi")
        st.session_state.reg_cw_rw_lt  = st.number_input("CW→RW",     1, 20, st.session_state.reg_cw_rw_lt, 1, key="w_reg_cwrw")
        st.session_state.reg_order_freq = st.number_input("Order freq", 1, 8, st.session_state.reg_order_freq, 1, key="w_reg_freq")

    total_lt = (st.session_state.reg_mat_lt + st.session_state.reg_semi_lt + st.session_state.reg_fp_lt
                + st.session_state.reg_cw_rw_lt + st.session_state.reg_rw_store_lt)
    cov_sup  = total_lt + st.session_state.reg_order_freq
    st.caption(f"Total LT **{total_lt} wk**  ·  Supplier coverage **{cov_sup} wk**")

    st.markdown("### \U0001F4E6 Initial stock")
    st.session_state.reg_total_init = st.slider(
        "Total initial stock (pcs)", 0, 10000, st.session_state.reg_total_init, step=50, key="w_reg_init",
    )
    rec = cov_sup * st.session_state.reg_demand_per_wk
    st.caption(f"_Recommended ≈ coverage × demand = **{rec}** pcs._")

    st.caption("Distribution (% of total, must sum to 100):")
    c1, c2, c3 = st.columns(3)
    with c1: st.session_state.reg_dist_mat   = st.number_input("Material %", 0, 100, st.session_state.reg_dist_mat,   5, key="w_reg_dmat")
    with c2: st.session_state.reg_dist_semi  = st.number_input("Semi %",     0, 100, st.session_state.reg_dist_semi,  5, key="w_reg_dsemi")
    with c3: st.session_state.reg_dist_cw    = st.number_input("CW %",       0, 100, st.session_state.reg_dist_cw,    5, key="w_reg_dcw")
    c1, c2 = st.columns(2)
    with c1: st.session_state.reg_dist_rw    = st.number_input("RW total %", 0, 100, st.session_state.reg_dist_rw,    5, key="w_reg_drw",
                                                              help="Split A/B by region-split slider.")
    with c2: st.session_state.reg_dist_store = st.number_input("Stores %",   0, 100, st.session_state.reg_dist_store, 5, key="w_reg_dst",
                                                              help="Split A/B by region-split slider; within region by tier.")
    dist_sum = (st.session_state.reg_dist_mat + st.session_state.reg_dist_semi
                + st.session_state.reg_dist_cw + st.session_state.reg_dist_rw
                + st.session_state.reg_dist_store)
    if dist_sum != 100:
        st.warning(f"⚠️ Distribution sums to {dist_sum}% (should be 100%). Engine will normalize.")

    st.markdown("### \U0001F3ED Production cap")
    st.session_state.reg_prod_cap_on = st.toggle("Enforce lifetime cap", value=st.session_state.reg_prod_cap_on, key="w_reg_cap_on")
    if st.session_state.reg_prod_cap_on:
        st.session_state.reg_prod_cap = st.slider("Max total products (lifetime)",
            max(10, st.session_state.reg_total_init), 20000,
            max(st.session_state.reg_prod_cap, st.session_state.reg_total_init),
            step=50, key="w_reg_cap")
        st.caption(f"_Initial stock counts; supplier orders forced to 0 once headroom = 0._")

    st.markdown("### ⚖️ Policy")
    st.session_state.reg_smart_distrib = st.toggle("Smart distribution", value=st.session_state.reg_smart_distrib, key="w_reg_smart")

    st.markdown("### \U0001F4B0 Finance")
    c1, c2 = st.columns(2)
    with c1: st.session_state.reg_price    = st.number_input("Price (€/u)",    0.0, 1000.0, st.session_state.reg_price,    0.5, key="w_reg_price")
    with c2: st.session_state.reg_var_cost = st.number_input("Var cost (€/u)", 0.0, 1000.0, st.session_state.reg_var_cost, 0.5, key="w_reg_vc")
    st.session_state.reg_fixed_pct = st.slider("Fixed cost (% of base rev)", 0, 50, st.session_state.reg_fixed_pct, 1, key="w_reg_fix")

# ── Preset buttons (9 cells = 3 splits × 3 stock distributions) ────────────
st.markdown("### Quick presets")
st.caption("Each preset writes the sidebar sliders. Result panel updates automatically. "
           "Common base: Agile LTs (mat=semi=fp=cw→rw=rw→store=1), Flat 100/wk × current weeks, init = LT+1.")

REG_SPLITS = [50, 70, 90]
STOCK_DISTS = [
    ("0/0/100", 0, 0, 100),
    ("0/30/70", 0, 30, 70),
    ("30/30/40", 30, 30, 40),
]

def _apply_preset(split_pct, cw_pct, rw_pct, store_pct):
    st.session_state.reg_split = split_pct
    st.session_state.reg_mat_lt = 1
    st.session_state.reg_semi_lt = 1
    st.session_state.reg_fp_lt = 1
    st.session_state.reg_cw_rw_lt = 1
    st.session_state.reg_rw_store_lt = 1
    st.session_state.reg_order_freq = 1
    st.session_state.reg_demand_per_wk = 100
    total_lt = 5
    st.session_state.reg_total_init = (total_lt + 1) * 100
    st.session_state.reg_dist_mat = 0
    st.session_state.reg_dist_semi = 0
    st.session_state.reg_dist_cw = cw_pct
    st.session_state.reg_dist_rw = rw_pct
    st.session_state.reg_dist_store = store_pct
    st.session_state.reg_prod_cap_on = False
    st.session_state.reg_smart_distrib = True

cols = st.columns(9)
for i, sp in enumerate(REG_SPLITS):
    for j, (lbl, cwp, rwp, stp) in enumerate(STOCK_DISTS):
        idx = i * 3 + j
        with cols[idx]:
            if st.button(f"{sp}/{100-sp}\n{lbl}", key=f"pre_{sp}_{lbl}", use_container_width=True):
                _apply_preset(sp, cwp, rwp, stp)
                st.rerun()

# ── Run the simulation ──────────────────────────────────────────────────────
def _normalize_dist():
    """Return ints summing to 100 (largest-remainder)."""
    parts = {
        'mat':   st.session_state.reg_dist_mat,
        'semi':  st.session_state.reg_dist_semi,
        'cw':    st.session_state.reg_dist_cw,
        'rw':    st.session_state.reg_dist_rw,
        'store': st.session_state.reg_dist_store,
    }
    tot = sum(parts.values())
    if tot == 0:
        return {**parts, 'store': 100}
    # Scale to 100 — just use values as % directly
    return parts

dist = _normalize_dist()
T = st.session_state.reg_total_init
init_mat   = int(round(T * dist['mat']   / 100))
init_semi  = int(round(T * dist['semi']  / 100))
init_cw    = int(round(T * dist['cw']    / 100))
init_rw    = int(round(T * dist['rw']    / 100))
init_store = T - init_mat - init_semi - init_cw - init_rw

if st.session_state.reg_demand_mode == "Flat":
    demand_curve = [st.session_state.reg_demand_per_wk] * st.session_state.reg_weeks
    planner_curve_arg = None
else:
    demand_curve = seasonal_curve(
        st.session_state.reg_weeks, st.session_state.reg_seas_sub,
        st.session_state.reg_seas_avg)
    # Planner believes the same SHAPE normalised to avg=100/wk
    planner_curve_arg = list(seasonal_curve_float(
        st.session_state.reg_weeks, st.session_state.reg_seas_sub, 100))

r = sim.run_simulation_regional(
    weeks=st.session_state.reg_weeks,
    n_per_region=st.session_state.reg_n_per_region,
    demand_curve=demand_curve,
    region_split_pct=st.session_state.reg_split,
    mat_lt=st.session_state.reg_mat_lt,
    semi_lt=st.session_state.reg_semi_lt,
    fp_lt=st.session_state.reg_fp_lt,
    cw_rw_lt=st.session_state.reg_cw_rw_lt,
    rw_store_lt=st.session_state.reg_rw_store_lt,
    order_freq=st.session_state.reg_order_freq,
    init_rawmat=init_mat, init_semi=init_semi,
    init_cw=init_cw, init_rw_total=init_rw, init_store_total=init_store,
    cap_start=1000, cap_ramp=0.0,
    smart_distrib=st.session_state.reg_smart_distrib,
    prod_cap=st.session_state.reg_prod_cap if st.session_state.reg_prod_cap_on else None,
    var_cost=st.session_state.reg_var_cost,
    price=st.session_state.reg_price,
    fixed_pct=st.session_state.reg_fixed_pct / 100.0,
    base_forecast=st.session_state.reg_demand_per_wk or 100,
    planner_curve=planner_curve_arg,
)

# ── KPI rows ──────────────────────────────────────────────────────────────
def _kpi(label, value, color="#1a2a40", sub=None):
    sub_html = f'<div style="color:#5a6a80;font-size:10.5px;margin-top:2px;">{sub}</div>' if sub else ""
    return (
        f'<div style="background:#fff;border-radius:8px;padding:10px 14px;'
        f'box-shadow:0 1px 2px rgba(0,0,0,.05);height:100%;">'
        f'<div style="color:#5a6a80;font-size:10.5px;text-transform:uppercase;letter-spacing:.4px;font-weight:600;">{label}</div>'
        f'<div style="color:{color};font-size:22px;font-weight:700;margin-top:4px;">{value}</div>'
        f'{sub_html}</div>'
    )

def _svc_color(p):
    return "#1a8a4a" if p >= 0.95 else "#e67e22" if p >= 0.80 else "#c0392b"

# Aggregate row
st.markdown("### \U0001F4CA Aggregate KPIs")
ks = st.columns(6)
ks[0].markdown(_kpi("Service Level", f"{r['svc']*100:.1f}%", _svc_color(r['svc'])), unsafe_allow_html=True)
ks[1].markdown(_kpi("Sales (units)", f"{r['tot_sales']:,}"), unsafe_allow_html=True)
ks[2].markdown(_kpi("Produced", f"{r['tot_produced']:,}"), unsafe_allow_html=True)
ks[3].markdown(_kpi("Sell-through", f"{r['sell_through']*100:.1f}%",
                    "#1a8a4a" if r['sell_through']>=0.80 else "#c0392b"), unsafe_allow_html=True)
ks[4].markdown(_kpi("Missed", f"{r['tot_missed']:,}", "#c0392b"), unsafe_allow_html=True)
margin_color = "#1a8a4a" if r['margin'] >= 0 else "#c0392b"
ks[5].markdown(_kpi("Margin (€)", f"{r['margin']:+,.0f}", margin_color,
                    sub=f"{r['margin_pct_rev']*100:+.1f}% of revenue"), unsafe_allow_html=True)

# Per-region row
st.markdown("### \U0001F30D Per-region")
# Compute per-region missed + stockout-weeks
states = r['states'][1:]
missed_a = sum(s['missed_a'] for s in states)
missed_b = sum(s['missed_b'] for s in states)
sw_a = sum(1 for s in states if s['missed_a'] > 0)
sw_b = sum(1 for s in states if s['missed_b'] > 0)

ks = st.columns(4)
ks[0].markdown(_kpi("Service A | B",
    f"{r['svc_a']*100:.1f}% | {r['svc_b']*100:.1f}%",
    _svc_color(min(r['svc_a'], r['svc_b']))), unsafe_allow_html=True)
ks[1].markdown(_kpi("Sales A | B",
    f"{r['sales_a']:,} | {r['sales_b']:,}"), unsafe_allow_html=True)
ks[2].markdown(_kpi("Missed A | B",
    f"{missed_a:,} | {missed_b:,}", "#c0392b"), unsafe_allow_html=True)
ks[3].markdown(_kpi("Stockout wks A | B",
    f"{sw_a}/{st.session_state.reg_weeks} | {sw_b}/{st.session_state.reg_weeks}",
    "#1a8a4a" if (sw_a + sw_b) == 0 else "#c0392b"), unsafe_allow_html=True)

_locks = []
if r['share_a_locked'] is not None:
    _locks.append(f"share_A = **{r['share_a_locked']*100:.0f}%** (ground truth {st.session_state.reg_split}%)")
if r.get('f_seasonal_locked') is not None:
    _locks.append(f"f_seasonal = **{r['f_seasonal_locked']:.2f}**")
if _locks:
    st.caption("_Planner locked at first review:_ " + " · ".join(_locks))

# ── Chain diagram (2-RW topology) ──────────────────────────────────────────
st.markdown("### \U0001F3ED Chain diagram")

_set("reg_diag_week", st.session_state.reg_weeks)
max_w = st.session_state.reg_weeks
if st.session_state.reg_diag_week > max_w:
    st.session_state.reg_diag_week = max_w

# Week scrubber: prev / slider / next / end
dc1, dc2, dc3, dc4, dc5 = st.columns([1, 1, 6, 1, 1])
with dc1:
    if st.button("⏮ W0", key="diag_w0", use_container_width=True):
        st.session_state.reg_diag_week = 0
with dc2:
    if st.button("◀ −1", key="diag_prev", use_container_width=True,
                 disabled=st.session_state.reg_diag_week <= 0):
        st.session_state.reg_diag_week -= 1
with dc3:
    st.session_state.reg_diag_week = st.slider(
        "Week to display", 0, max_w, st.session_state.reg_diag_week,
        key="w_diag_slider", label_visibility="collapsed")
with dc4:
    if st.button("+1 ▶", key="diag_next", use_container_width=True,
                 disabled=st.session_state.reg_diag_week >= max_w):
        st.session_state.reg_diag_week += 1
with dc5:
    if st.button(f"W{max_w} ⏭", key="diag_end", use_container_width=True):
        st.session_state.reg_diag_week = max_w

_st = r['states'][st.session_state.reg_diag_week]

def _box(title, value, sub="", colour="#1a2a40", bg="#fff", w=92):
    return (
        f'<div style="background:{bg};border:1px solid #e6ecf2;border-radius:6px;'
        f'padding:8px 10px;min-width:{w}px;text-align:center;'
        f'box-shadow:0 1px 1px rgba(0,0,0,.04);">'
        f'<div style="font-size:9.5px;text-transform:uppercase;letter-spacing:.4px;'
        f'color:#5a6a80;font-weight:600;">{title}</div>'
        f'<div style="font-size:18px;font-weight:700;color:{colour};line-height:1.1;margin-top:3px;">'
        f'{value}</div>'
        f'<div style="font-size:9.5px;color:#7a8a9e;margin-top:2px;">{sub}</div></div>'
    )

def _arrow(label_top="", label_bot=""):
    return (
        f'<div style="display:flex;flex-direction:column;align-items:center;'
        f'justify-content:center;color:#7a8a9e;font-size:9.5px;padding:0 4px;">'
        f'<div>{label_top}</div>'
        f'<div style="font-size:18px;color:#1a2a40;line-height:1;">→</div>'
        f'<div>{label_bot}</div></div>'
    )

cw_v       = int(round(_st['cw']))
rw_a_v     = int(round(_st['rw_a']))
rw_b_v     = int(round(_st['rw_b']))
stores_a_v = int(sum(_st['stores_a']))
stores_b_v = int(sum(_st['stores_b']))
raw_v      = int(round(_st.get('raw_mat', 0)))
semi_v     = int(round(_st.get('semi', 0)))
pb_v       = int(round(_st.get('pb', 0)))
mat_pipe_v = int(round(sum(_st.get('mat_pipe', []))))
semi_pipe_v= int(round(sum(_st.get('semi_pipe', []))))
fp_pipe_v  = int(round(sum(_st.get('fp_pipe', []))))
cwrw_a_v   = int(round(sum(_st.get('cw_rw_pipe_a', []))))
cwrw_b_v   = int(round(sum(_st.get('cw_rw_pipe_b', []))))
dist_a_v   = int(round(_st.get('dist_pipe_a_sum', 0)))
dist_b_v   = int(round(_st.get('dist_pipe_b_sum', 0)))
ship_a_v   = int(round(_st.get('ship_backlog_a', 0)))
ship_b_v   = int(round(_st.get('ship_backlog_b', 0)))

dem_a_v    = _st.get('demand_a', 0); dem_b_v = _st.get('demand_b', 0)
sal_a_v    = _st.get('sales_a', 0);  sal_b_v = _st.get('sales_b', 0)
mis_a_v    = _st.get('missed_a', 0); mis_b_v = _st.get('missed_b', 0)

N = st.session_state.reg_n_per_region

# Upstream chain (single track until CW)
upstream = (
    _box("Supplier", f"pb {pb_v}", "backlog → produce") +
    _arrow() +
    _box(f"Mat ({st.session_state.reg_mat_lt}wk)", f"{raw_v + mat_pipe_v}",
         f"buf {raw_v} · pipe {mat_pipe_v}") +
    _arrow() +
    _box(f"Semi ({st.session_state.reg_semi_lt}wk)", f"{semi_v + semi_pipe_v}",
         f"buf {semi_v} · pipe {semi_pipe_v}") +
    _arrow() +
    _box(f"FP ({st.session_state.reg_fp_lt}wk)", f"{fp_pipe_v}",
         f"pipe {fp_pipe_v}") +
    _arrow() +
    _box("CW", f"{cw_v}", "central pool", colour="#1a2a40", bg="#eef2f6")
)

# Region A branch
region_a_html = (
    _box(f"CW→RW A ({st.session_state.reg_cw_rw_lt}wk)", f"{cwrw_a_v}",
         f"pipe · ord {ship_a_v}", bg="#f3f7fc") +
    _arrow() +
    _box("RW A", f"{rw_a_v}", "regional pool", colour="#2c5f8a", bg="#e8eff7") +
    _arrow() +
    _box(f"RW→Store A ({st.session_state.reg_rw_store_lt}wk)", f"{dist_a_v}",
         "pipe", bg="#f3f7fc") +
    _arrow() +
    _box(f"Stores A (×{N})", f"{stores_a_v}",
         f"dem {dem_a_v} · sold {sal_a_v}" + (f" · LOST {mis_a_v}" if mis_a_v else ""),
         colour="#2c5f8a",
         bg="#fef0f0" if mis_a_v else "#e8eff7")
)

# Region B branch
region_b_html = (
    _box(f"CW→RW B ({st.session_state.reg_cw_rw_lt}wk)", f"{cwrw_b_v}",
         f"pipe · ord {ship_b_v}", bg="#fcf6ef") +
    _arrow() +
    _box("RW B", f"{rw_b_v}", "regional pool", colour="#c97a2c", bg="#fbf1e6") +
    _arrow() +
    _box(f"RW→Store B ({st.session_state.reg_rw_store_lt}wk)", f"{dist_b_v}",
         "pipe", bg="#fcf6ef") +
    _arrow() +
    _box(f"Stores B (×{N})", f"{stores_b_v}",
         f"dem {dem_b_v} · sold {sal_b_v}" + (f" · LOST {mis_b_v}" if mis_b_v else ""),
         colour="#c97a2c",
         bg="#fef0f0" if mis_b_v else "#fbf1e6")
)

diagram_html = (
    f'<div style="overflow-x:auto;padding:12px 0;background:#fafbfc;'
    f'border-radius:8px;border:1px solid #ecf0f4;">'
    # Row 1: upstream chain
    f'<div style="display:flex;align-items:stretch;gap:0;padding:0 12px 14px;'
    f'border-bottom:1px dashed #d8e0e8;justify-content:flex-start;min-width:780px;">'
    f'{upstream}'
    f'</div>'
    # Row 2: two regional branches sharing the CW
    f'<div style="display:grid;grid-template-columns:auto 1fr;gap:14px 18px;'
    f'padding:14px 12px;min-width:780px;align-items:center;">'
    f'<div style="font-weight:700;color:#2c5f8a;font-size:12px;">→ Region A</div>'
    f'<div style="display:flex;align-items:stretch;gap:0;">{region_a_html}</div>'
    f'<div style="font-weight:700;color:#c97a2c;font-size:12px;">→ Region B</div>'
    f'<div style="display:flex;align-items:stretch;gap:0;">{region_b_html}</div>'
    f'</div></div>'
)

st.markdown(
    f'<div style="font-size:11.5px;color:#5a6a80;margin-bottom:6px;">'
    f'Showing <b>week {st.session_state.reg_diag_week}</b> '
    f'(end-of-week stock & in-transit). Sales/demand shown for stores.'
    f'</div>{diagram_html}',
    unsafe_allow_html=True,
)

# ── Weekly chart: aggregate demand vs sales ────────────────────────────────
st.markdown("### \U0001F4C8 Weekly demand vs sales (aggregate)")
chart_data = pd.DataFrame({
    'Week': list(range(1, st.session_state.reg_weeks + 1)),
    'Demand': [s['demand_a'] + s['demand_b'] for s in states],
    'Sales':  [s['sales_a']  + s['sales_b']  for s in states],
    'Missed': [s['missed_a'] + s['missed_b'] for s in states],
})
long = chart_data.melt('Week', ['Demand', 'Sales', 'Missed'], var_name='Series', value_name='Units')
ch = alt.Chart(long).mark_line(point=True).encode(
    x='Week:O', y='Units:Q',
    color=alt.Color('Series:N', scale=alt.Scale(domain=['Demand','Sales','Missed'], range=['#1a2a40','#1a8a4a','#c0392b']))
).properties(height=280)
st.altair_chart(ch, use_container_width=True)

# ── Per-region weekly sales ────────────────────────────────────────────────
st.markdown("### \U0001F4C8 Weekly sales per region")
region_data = pd.DataFrame({
    'Week':   list(range(1, st.session_state.reg_weeks + 1)) * 2,
    'Region': ['A'] * st.session_state.reg_weeks + ['B'] * st.session_state.reg_weeks,
    'Sales':  [s['sales_a'] for s in states] + [s['sales_b'] for s in states],
    'Missed': [s['missed_a'] for s in states] + [s['missed_b'] for s in states],
})
ch2 = alt.Chart(region_data).mark_bar().encode(
    x='Week:O',
    y='Sales:Q',
    color=alt.Color('Region:N', scale=alt.Scale(domain=['A','B'], range=['#2c5f8a','#c97a2c']))
).properties(height=220)
st.altair_chart(ch2, use_container_width=True)

# ── Per-region heatmaps (side-by-side) ─────────────────────────────────────
st.markdown("### \U0001F525 Per-region sales matrix")
st.caption(
    "🟢 **Sold** (had stock, demand met) · 🔴 **Missed** (demand but empty) · "
    "🟡 **Held** (had stock, no demand) · ⚪ **Idle** (empty, no demand). "
    "Stores in each region ordered top-to-bottom: high-selling → medium → small."
)

def _heatmap_for_region(side):
    """Build a sales-matrix heatmap for Region A ('a') or B ('b')."""
    n = st.session_state.reg_n_per_region
    # Tier ordering label for the y-axis (consistent with single-region heatmap)
    rows_data = []
    for w_idx in range(1, len(r['states'])):
        st_obj = r['states'][w_idx]
        per_dem = st_obj[f'per_store_dem_{side}']
        per_sal = st_obj[f'per_store_sales_{side}']
        per_mis = st_obj[f'per_store_missed_{side}']
        stk_arr = st_obj[f'stores_{side}']
        for i in range(n):
            dem    = per_dem[i] if i < len(per_dem) else 0
            sales  = per_sal[i] if i < len(per_sal) else 0
            missed = per_mis[i] if i < len(per_mis) else 0
            stk    = stk_arr[i] if i < len(stk_arr) else 0
            if missed > 0.5:    state = "Missed"
            elif dem > 0.5:     state = "Sold"
            elif stk >= 1:      state = "Held"
            else:               state = "Idle"
            rows_data.append({
                'store': i + 1, 'week': w_idx, 'state': state,
                'demand': dem, 'sales': sales, 'missed': missed, 'stock': stk,
            })
    df = pd.DataFrame(rows_data)
    unique_stores = sorted(df['store'].unique()) if not df.empty else [1]
    n_rows = len(unique_stores)
    if n_rows <= 50: row_h = 12
    elif n_rows <= 100: row_h = 8
    elif n_rows <= 200: row_h = 5
    else: row_h = max(3, 1200 // n_rows)
    try:
        alt.data_transformers.disable_max_rows()
    except Exception:
        pass
    return (
        alt.Chart(df).mark_rect(stroke='white', strokeWidth=0.4).encode(
            x=alt.X('week:O', title='Week'),
            y=alt.Y('store:O', sort=unique_stores, title=f'Region {side.upper()} stores (top: high-selling)'),
            color=alt.Color('state:N',
                scale=alt.Scale(domain=['Sold','Missed','Held','Idle'],
                                range=['#1a8a4a','#c0392b','#f1c40f','#e8e8e8']),
                legend=alt.Legend(title='State', orient='top')),
            tooltip=[
                alt.Tooltip('store:O', title='Store (within region)'),
                alt.Tooltip('week:O',  title='Week'),
                alt.Tooltip('demand:Q', title='Demand'),
                alt.Tooltip('sales:Q', title='Sales'),
                alt.Tooltip('missed:Q', title='Missed'),
                alt.Tooltip('stock:Q', title='End-of-week stock'),
                alt.Tooltip('state:N', title='State'),
            ],
        ).properties(height=max(180, row_h * n_rows))
    )

hc1, hc2 = st.columns(2)
with hc1:
    st.markdown("**Region A**")
    st.altair_chart(_heatmap_for_region('a'), use_container_width=True)
with hc2:
    st.markdown("**Region B**")
    st.altair_chart(_heatmap_for_region('b'), use_container_width=True)

# ── Chain inventory chart ──────────────────────────────────────────────────
st.markdown("### \U0001F4E6 End-of-week stock by location")
inv_data = pd.DataFrame({
    'Week':  list(range(0, st.session_state.reg_weeks + 1)),
    'CW':    [s['cw']   for s in r['states']],
    'RW A':  [s['rw_a'] for s in r['states']],
    'RW B':  [s['rw_b'] for s in r['states']],
    'Stores A': [sum(s['stores_a']) for s in r['states']],
    'Stores B': [sum(s['stores_b']) for s in r['states']],
})
inv_long = inv_data.melt('Week', ['CW', 'RW A', 'RW B', 'Stores A', 'Stores B'], var_name='Where', value_name='Units')
ch3 = alt.Chart(inv_long).mark_area(opacity=0.7).encode(
    x='Week:O', y=alt.Y('Units:Q', stack='zero'),
    color=alt.Color('Where:N', scale=alt.Scale(
        domain=['CW','RW A','RW B','Stores A','Stores B'],
        range=['#1a2a40','#2c5f8a','#c97a2c','#5a8fc0','#e0a878']
    ))
).properties(height=260)
st.altair_chart(ch3, use_container_width=True)

# ── 9-cell summary (re-runs the grid in-page) ──────────────────────────────
with st.expander("\U0001F4CA Run the 9-cell grid (current LT + demand)", expanded=False):
    st.caption("Replays the current sidebar config across the 3 region splits × 3 stock distributions. "
               "Useful for sanity-checking the spread.")
    if st.button("Run 9-cell grid", key="run_grid"):
        rows = []
        for sp in REG_SPLITS:
            for lbl, cwp, rwp, stp in STOCK_DISTS:
                T = st.session_state.reg_total_init
                rg = sim.run_simulation_regional(
                    weeks=st.session_state.reg_weeks,
                    n_per_region=st.session_state.reg_n_per_region,
                    demand_curve=demand_curve,
                    region_split_pct=sp,
                    mat_lt=st.session_state.reg_mat_lt,
                    semi_lt=st.session_state.reg_semi_lt,
                    fp_lt=st.session_state.reg_fp_lt,
                    cw_rw_lt=st.session_state.reg_cw_rw_lt,
                    rw_store_lt=st.session_state.reg_rw_store_lt,
                    order_freq=st.session_state.reg_order_freq,
                    init_rawmat=0, init_semi=0,
                    init_cw=int(T * cwp / 100),
                    init_rw_total=int(T * rwp / 100),
                    init_store_total=T - int(T * cwp / 100) - int(T * rwp / 100),
                    cap_start=1000, cap_ramp=0.0,
                    smart_distrib=st.session_state.reg_smart_distrib,
                    prod_cap=st.session_state.reg_prod_cap if st.session_state.reg_prod_cap_on else None,
                    var_cost=st.session_state.reg_var_cost,
                    price=st.session_state.reg_price,
                    fixed_pct=st.session_state.reg_fixed_pct / 100.0,
                    base_forecast=st.session_state.reg_demand_per_wk or 100,
                    planner_curve=planner_curve_arg,
                )
                rows.append({
                    'Split': f"{sp}/{100-sp}",
                    'Distribution': lbl,
                    'Service': f"{rg['svc']*100:.1f}%",
                    'Svc A': f"{rg['svc_a']*100:.1f}%",
                    'Svc B': f"{rg['svc_b']*100:.1f}%",
                    'Sales': f"{rg['tot_sales']:,}",
                    'Produced': f"{rg['tot_produced']:,}",
                    'Sell-thru': f"{rg['sell_through']*100:.1f}%",
                    'Margin (€)': f"{rg['margin']:+,.0f}",
                    'Margin %': f"{rg['margin_pct_rev']*100:+.1f}%",
                })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ── Per-week detail table ──────────────────────────────────────────────────
with st.expander("\U0001F50E Per-week detail (current scenario)"):
    rows = []
    for i, s in enumerate(r['states']):
        rows.append({
            'W': s['week'],
            'CW': int(s['cw']),
            'RW A': int(s['rw_a']),
            'RW B': int(s['rw_b']),
            'Stores A': sum(s['stores_a']),
            'Stores B': sum(s['stores_b']),
            'Sales A': s['sales_a'],
            'Sales B': s['sales_b'],
            'Missed A': s['missed_a'],
            'Missed B': s['missed_b'],
            'Sup. order': s['sup_order'],
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
