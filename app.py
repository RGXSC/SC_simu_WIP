"""
Supply Chain Agility Simulator — v2
=====================================

Teaching/consulting tool: compares Push vs Agile supply chain strategies across
9 operational scenarios (3 LT profiles × 3 demand shapes) plus 9 seasonal
scenarios (3 LT profiles × 3 seasonal volumes). Simulates a 26-week, 2-store,
4-stage supply chain (Material → Semi → Finishing+CW → Distribution).

Key accounting convention (v2 entering-stage model)
---------------------------------------------------
Costs are booked at the moment units ENTER each stage:

    cost_mat  = shipped × VC × 0.50   (supplier ships into mat_pipe)
    cost_semi = si      × VC × 0.25   (RM → Semi processing starts)
    cost_fp   = fi      × VC × 0.25   (Semi → FP processing starts)

Initial stock (pre-positioned at W0) is valued separately via init_stock_value:
    RM @ 50%, Semi @ 75%, WH/Store @ 100% — these units already "entered"
    their stages at sim start. This avoids double-counting and the P&L
    reconciles exactly:

        Revenue == Initial stock value + production costs + Fixed + Margin

Single-file deployment constraint
---------------------------------
Kept as one app.py for Streamlit Cloud. Section headers below delineate logical
groups: Constants → Math helpers → Engine → KPIs → Diagram → Presets → Sidebar
→ Main page.
"""

import streamlit as st
import json
import math
import pandas as pd
import numpy as np
from math import gamma as gamma_fn, exp as math_exp
from datetime import datetime

st.set_page_config(layout="wide", page_title="Supply Chain Agility Simulator", page_icon="\U0001f3ed")


# ════════════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════════════

# Stage valorization — fraction of full variable cost
VALOR_RAW_MAT  = 0.50
VALOR_SEMI     = 0.75
VALOR_FINISHED = 1.00

# Fixed base forecast (planner's nominal demand signal)
BASE_FORECAST = 100

# Demand shape labels (visible in the selector)
DEMAND_SHAPES = [
    "➡️ Flat (constant demand)",
    "\U0001f4c8 Linear ramp then flat",
    "\U0001f4c9 Linear drop then flat",
    "\U0001f30a Seasonal (curve profile)",
]

# Lead-time profiles used by presets
LT_PROFILES = {
    "Agile":  {"mat_lt": 4,  "semi_lt": 2, "fp_lt": 1, "dist_lt": 1, "order_freq": 1},
    "Medium": {"mat_lt": 8,  "semi_lt": 4, "fp_lt": 2, "dist_lt": 2, "order_freq": 2},
    "Push":   {"mat_lt": 12, "semi_lt": 6, "fp_lt": 3, "dist_lt": 3, "order_freq": 4},
}

# Stock distribution per LT profile (% across store / WH / semi — RM = remainder)
STOCK_DIST = {
    "Agile":  {"store_pct": 40, "wh_pct": 20, "semi_pct": 10},   # RM = 30
    "Medium": {"store_pct": 70, "wh_pct": 20, "semi_pct": 10},   # RM = 0
    "Push":   {"store_pct": 100, "wh_pct": 0, "semi_pct": 0},    # RM = 0
}

# All 18 presets use total init stock = 2600 over 26 weeks
PRESET_INIT_STOCK = 2600
PRESET_WEEKS = 26

# Seasonal sub-profile parameters: (peak_position_ratio, shape_k)
# theta is computed from ratio × weeks / (k - 1); peak position scales with sim length
SEASONAL_PARAMS = {
    "Very Steep": (3.0 / 26.0, 2.5),   # peak W3 in 26wk, ~4× avg, fast decay
    "Steep":      (6.0 / 26.0, 3.0),   # peak W6 in 26wk, ~2.4× avg
    "~Flat":      (6.0 / 26.0, 1.8),   # peak W6, ~1.6× avg, gentle dome (tail ~35%)
}

# Session-state defaults (initialized at app start)
_DEFAULTS = {
    "mat_lt": 4, "semi_lt": 2, "fp_lt": 1, "dist_lt": 1,
    "order_freq": 1,
    "total_stock": PRESET_INIT_STOCK,
    "store_pct": 40, "wh_pct": 20, "semi_pct": 10,
    "store_a_pct": 60, "smart_distrib": True,
    "demand_shape": DEMAND_SHAPES[0],
    "kickstart": True,
    "debug_mode": False,
    "week_num": 0,
    "seas_sub": "Steep",
    "seas_avg": 100,
    "lr_end": 300, "lr_wks": 5,
    "ld_end": 30,  "ld_wks": 1,
}
for _k, _v in _DEFAULTS.items():
    st.session_state.setdefault(_k, _v)


# ════════════════════════════════════════════════════════════════
# MATH HELPERS — pure functions
# ════════════════════════════════════════════════════════════════

def gamma_pdf(x: float, k: float, theta: float) -> float:
    """Gamma PDF value at x (shape k, scale theta). Returns 0 for x <= 0."""
    if x <= 0:
        return 0.0
    return (x ** (k - 1)) * math_exp(-x / theta) / ((theta ** k) * gamma_fn(k))


def seasonal_curve(weeks: int, sub_shape: str, avg: float) -> list[int]:
    """
    Build a length-weeks seasonal demand list scaled so its total = avg × weeks.

    Uses a gamma distribution whose peak position scales with the simulation
    length (peak at ratio × weeks). This keeps the shape recognizable when
    users change sim length.
    """
    ratio, k = SEASONAL_PARAMS.get(sub_shape, SEASONAL_PARAMS["Steep"])
    theta = (ratio * weeks) / max(k - 1, 0.1)
    pdf_vals = [gamma_pdf(w, k, theta) for w in range(1, weeks + 1)]
    pdf_sum = sum(pdf_vals) or 1.0
    total = avg * weeks
    return [max(0, int(round(v * total / pdf_sum))) for v in pdf_vals]


def build_demand_curve(shape: str, weeks: int, *,
                       base: int = BASE_FORECAST,
                       lr_end: int = 300, lr_wks: int = 5,
                       ld_end: int = 30,  ld_wks: int = 1,
                       seas_sub: str = "Steep", seas_avg: int = 100) -> list[int]:
    """
    Return a length-(weeks+1) demand list where index 0 = 0 (W0) and indices
    1..weeks = demand at each week. Shape is dispatched by the emoji-prefixed
    shape string from DEMAND_SHAPES.
    """
    out = [0]
    if "Flat" in shape:
        out.extend([base] * weeks)
    elif "ramp" in shape.lower():
        for w in range(1, weeks + 1):
            if w <= lr_wks:
                val = base + (lr_end - base) * w / lr_wks
            else:
                val = lr_end
            out.append(max(0, int(round(val))))
    elif "drop" in shape.lower():
        for w in range(1, weeks + 1):
            if w <= ld_wks:
                val = base + (ld_end - base) * w / ld_wks
            else:
                val = ld_end
            out.append(max(0, int(round(val))))
    else:  # Seasonal
        out.extend(seasonal_curve(weeks, seas_sub, seas_avg))
    return out


def recommend_initial_stock(shape: str, weeks: int, coverage: int, *,
                            base: int = BASE_FORECAST,
                            lr_end: int = 300, lr_wks: int = 5,
                            ld_end: int = 30,  ld_wks: int = 1,
                            seas_sub: str = "Steep", seas_avg: int = 100) -> tuple[int, str]:
    """
    Smart initial-stock recommendation: sum of actual demand over the first
    `coverage` weeks of the chosen shape. Returns (units, human_description).
    """
    n = min(coverage, weeks)
    curve = build_demand_curve(shape, weeks,
                               base=base, lr_end=lr_end, lr_wks=lr_wks,
                               ld_end=ld_end, ld_wks=ld_wks,
                               seas_sub=seas_sub, seas_avg=seas_avg)
    total = sum(curve[1:n + 1])
    if "Seasonal" in shape:
        detail = f"sum of first {n} wks of {seas_sub} curve (avg {seas_avg}/wk)"
    elif "ramp" in shape.lower():
        detail = f"first {n} wks of ramp curve"
    elif "drop" in shape.lower():
        detail = f"first {n} wks of drop curve"
    else:
        detail = f"{base}/wk × {n} wks coverage"
    return int(total), detail


# ════════════════════════════════════════════════════════════════
# SIMULATION ENGINE — pure function, cached by Streamlit
# ════════════════════════════════════════════════════════════════

@st.cache_data
def run_simulation(weeks, init_store, init_cw, init_semi, init_rawmat,
                   order_freq, mat_lt, semi_lt, fp_lt, dist_lt,
                   cap_start, cap_ramp, base_forecast,
                   price, var_cost, fixed_pct, store_a_pct, smart_distrib,
                   kickstart=True, debug=False,
                   custom_demand=None):
    """
    Run the weekly supply-chain simulation and return a list of per-week state
    dicts (index 0 = W0 initial state, indices 1..weeks = simulated weeks).

    Accounting convention (entering-stage)
    --------------------------------------
    Costs are booked at the moment units *enter* each stage:
        cost_mat  = shipped × VC × 0.50   (supplier ship → mat_pipe)
        cost_semi = si      × VC × 0.25   (RM → Semi processing)
        cost_fp   = fi      × VC × 0.25   (Semi → FP processing)
    W0 has zero production costs — initial stock is valued separately in
    compute_kpis via init_stock_value (no double-counting).

    Factory activation & capacity ramp
    ----------------------------------
    One unified flag `factory_active_from` controls both Semi/FP processing
    gating and the capacity ramp counters (pn/sn/fn). It is set to 1 at sim
    start if kickstart=True and any init RM/Semi exists; otherwise it is set
    to the week of the first supplier order. Ramp counters advance starting
    the week AFTER factory activation.

    Parameters
    ----------
    kickstart : bool
        If True, factory becomes active at W1 when initial RM/Semi exist
        (prevents initial stock from being stuck forever in seasonal/flat
        scenarios where the planner never orders).
    debug : bool
        If True, runtime sanity assertions are active (conservation checks).
    custom_demand : sequence or None
        Per-week demand override indexed 0..weeks (index 0 ignored).

    Returns
    -------
    list[dict]
        Per-week state dicts. See the W0 state constructor below for the
        full schema of keys.
    """
    phys_lt = mat_lt + semi_lt + fp_lt + dist_lt
    coverage = phys_lt + order_freq
    pct_a = store_a_pct / 100.0
    pct_b = 1.0 - pct_a

    # --- Demand curve ---
    demand = {0: 0}
    if custom_demand is not None:
        for w in range(1, weeks + 1):
            demand[w] = int(custom_demand[w]) if w < len(custom_demand) else int(custom_demand[-1])
    else:
        for w in range(1, weeks + 1):
            demand[w] = base_forecast

    # --- Pipes (index 0 = front, about to arrive; index -1 = back, just entered) ---
    mat_pipe    = [0.0] * max(1, mat_lt)
    semi_pipe   = [0.0] * max(1, semi_lt)
    fp_pipe     = [0.0] * max(1, fp_lt)
    dist_pipe_a = [0.0] * max(1, dist_lt)
    dist_pipe_b = [0.0] * max(1, dist_lt)

    # --- Buffers ---
    # Initial store stock always split 50/50 — planner hasn't reviewed yet
    store_a = float(init_store) / 2.0
    store_b = float(init_store) / 2.0
    raw_mat = float(init_rawmat)
    semi    = float(init_semi)
    cw      = float(init_cw)

    # --- Supplier / factory state ---
    pb = 0.0                   # supplier backlog
    pn = sn = fn = 0           # ramp counters (all gated on factory_active_from)
    co = 0.0                   # cumulative orders placed
    cas = 0.0                  # cumulative units arrived at stores
    smart_discovered = False   # planner discovers A/B imbalance at first review

    # Unified factory-activation flag (see docstring)
    if kickstart and (init_rawmat > 0 or init_semi > 0):
        factory_active_from = 1
    else:
        factory_active_from = None

    order_weeks = list(range(order_freq, weeks + 1, order_freq)) if order_freq > 1 else list(range(1, weeks + 1))
    ff = float(base_forecast)  # forecast — updates on review weeks only

    # --- W0 initial state ---
    s0 = {
        'week': 0,
        'demand': 0, 'demand_a': 0, 'demand_b': 0,
        'forecast': base_forecast,
        'mat_arr': 0, 'semi_arr': 0, 'fp_arr': 0,
        'dist_arr_a': 0, 'dist_arr_b': 0, 'dist_arr': 0,
        'sales_a': 0, 'sales_b': 0, 'sales': 0,
        'missed_a': 0, 'missed_b': 0, 'missed': 0,
        'store_a': store_a, 'store_b': store_b, 'store_stock': store_a + store_b,
        'supplier_shipped': 0, 'supplier_cap': cap_start,
        'raw_mat_before_prod': raw_mat, 'raw_mat_stock': raw_mat,
        'semi_input': 0, 'semi_cap': cap_start, 'semi_stock': semi,
        'fp_input': 0, 'fp_cap': cap_start,
        'cw_shipped': 0, 'cw_stock': cw,
        'alloc_a': 0, 'alloc_b': 0,
        'mat_pipe':    list(mat_pipe),
        'semi_pipe':   list(semi_pipe),
        'fp_pipe':     list(fp_pipe),
        'dist_pipe_a': list(dist_pipe_a),
        'dist_pipe_b': list(dist_pipe_b),
        'order': 0, 'pending': 0, 'backlog': 0,
        'wip_total': 0,
        # W0 has no production costs — initial stock is valued separately
        'cost_mat': 0.0, 'cost_semi': 0.0, 'cost_fp': 0.0,
        'coverage': coverage,
        'comment': "Week 0 — initial state (pre-positioned stock).",
    }
    states = [s0]

    for w in range(1, weeks + 1):
        s = {'week': w}
        dem_total = demand[w]
        dem_a = round(dem_total * pct_a)
        dem_b = dem_total - dem_a

        # Forecast updates only at review weeks (periodic-review blind between)
        if w in order_weeks:
            ff = float(dem_total)
            if smart_distrib and not smart_discovered:
                smart_discovered = True
        s['demand'] = dem_total
        s['demand_a'] = dem_a
        s['demand_b'] = dem_b
        s['forecast'] = round(ff, 1)

        # 1. Arrivals — clear pipe fronts
        m_arr  = mat_pipe[0]
        sm_arr = semi_pipe[0]
        fp_arr = fp_pipe[0]
        da_arr = dist_pipe_a[0]
        db_arr = dist_pipe_b[0]
        d_arr_total = da_arr + db_arr
        mat_pipe[0] = 0.0; semi_pipe[0] = 0.0; fp_pipe[0] = 0.0
        dist_pipe_a[0] = 0.0; dist_pipe_b[0] = 0.0
        s['mat_arr']    = round(m_arr, 1)
        s['semi_arr']   = round(sm_arr, 1)
        s['fp_arr']     = round(fp_arr, 1)
        s['dist_arr_a'] = round(da_arr, 1)
        s['dist_arr_b'] = round(db_arr, 1)
        s['dist_arr']   = round(d_arr_total, 1)
        cas += d_arr_total

        # 2. Store sales (per store)
        avail_a = store_a + da_arr
        sales_a = min(dem_a, avail_a)
        missed_a = max(0, dem_a - sales_a)
        store_a = avail_a - sales_a

        avail_b = store_b + db_arr
        sales_b = min(dem_b, avail_b)
        missed_b = max(0, dem_b - sales_b)
        store_b = avail_b - sales_b

        sales = sales_a + sales_b
        missed = missed_a + missed_b
        s.update({
            'store_a': round(store_a, 1), 'store_b': round(store_b, 1),
            'sales_a': round(sales_a, 1), 'sales_b': round(sales_b, 1),
            'missed_a': round(missed_a, 1), 'missed_b': round(missed_b, 1),
            'sales': round(sales, 1), 'missed': round(missed, 1),
            'store_stock': round(store_a + store_b, 1),
        })

        # 3. Supplier ships — capacity ramps after factory activation
        pc = min(cap_start * (1 + pn * cap_ramp), cap_start * 10)
        if pb > 0.01:
            shipped = math.ceil(min(pb, pc))
            pb -= shipped
        else:
            shipped = 0.0
        s['supplier_shipped'] = round(shipped, 1)
        s['supplier_cap']     = round(pc, 0)

        # 4. Arrivals update buffers
        raw_mat += m_arr
        semi    += sm_arr
        cw      += fp_arr
        s['raw_mat_before_prod'] = round(raw_mat, 1)

        # 5. Order decision (before processing — factory sees the order)
        pre_wip = (sum(mat_pipe) + sum(semi_pipe) + sum(fp_pipe)
                   + sum(dist_pipe_a) + sum(dist_pipe_b)
                   + raw_mat + semi + cw + pb)
        od = 0
        if w in order_weeks:
            tgt = ff * coverage
            existing = (store_a + store_b) + pre_wip
            od = math.ceil(max(0, tgt - existing))
            co += od
            pb += od
            if od > 0 and factory_active_from is None:
                factory_active_from = w
        s['order'] = round(od, 0)

        # 6. Semi processing (RM → Semi) — gated on factory activation
        sc_ = min(cap_start * (1 + sn * cap_ramp), cap_start * 10)
        if raw_mat > 0.01 and factory_active_from is not None:
            si = math.ceil(min(raw_mat, sc_))
            raw_mat -= si
        else:
            si = 0.0
        s['semi_input'] = round(si, 1)
        s['semi_cap']   = round(sc_, 0)
        s['raw_mat_stock'] = round(raw_mat, 1)

        # 7. FP processing (Semi → FP) — gated on factory activation
        fpc = min(cap_start * (1 + fn * cap_ramp), cap_start * 10)
        if semi > 0.01 and factory_active_from is not None:
            fi = math.ceil(min(semi, fpc))
            semi -= fi
        else:
            fi = 0.0
        s['fp_input']   = round(fi, 1)
        s['fp_cap']     = round(fpc, 0)
        s['semi_stock'] = round(semi, 1)

        # 8. CW → stores (push all, allocate per-store)
        ship_out = math.ceil(cw) if cw > 0.01 else 0.0
        cw -= ship_out
        s['cw_shipped'] = round(ship_out, 1)
        s['cw_stock']   = round(cw, 1)

        if ship_out > 0:
            if smart_distrib and smart_discovered:
                # Smart: fill worst-covered store first to equalize weeks-of-cover
                dem_a_wk = max(ff * pct_a, 0.01)
                dem_b_wk = max(ff * pct_b, 0.01)
                cover_a = (store_a + sum(dist_pipe_a)) / dem_a_wk
                cover_b = (store_b + sum(dist_pipe_b)) / dem_b_wk
                if cover_a < cover_b:
                    gap = math.ceil(max(0, (cover_b - cover_a) * dem_a_wk))
                    priority_a = min(ship_out, gap)
                    remaining = ship_out - priority_a
                    alloc_a = priority_a + round(remaining * pct_a)
                    alloc_b = ship_out - alloc_a
                elif cover_b < cover_a:
                    gap = math.ceil(max(0, (cover_a - cover_b) * dem_b_wk))
                    priority_b = min(ship_out, gap)
                    remaining = ship_out - priority_b
                    alloc_b = priority_b + round(remaining * pct_b)
                    alloc_a = ship_out - alloc_b
                else:
                    alloc_a = round(ship_out * pct_a)
                    alloc_b = ship_out - alloc_a
            else:
                # Push or smart-not-yet-discovered: 50/50 blind
                alloc_a = round(ship_out * 0.5)
                alloc_b = ship_out - alloc_a
        else:
            alloc_a = 0
            alloc_b = 0
        s['alloc_a'] = round(alloc_a, 1)
        s['alloc_b'] = round(alloc_b, 1)

        # 9. Update pipes (shift, append new entrants)
        mat_pipe    = mat_pipe[1:]    + [shipped]
        semi_pipe   = semi_pipe[1:]   + [si]
        fp_pipe     = fp_pipe[1:]     + [fi]
        dist_pipe_a = dist_pipe_a[1:] + [alloc_a]
        dist_pipe_b = dist_pipe_b[1:] + [alloc_b]
        s['mat_pipe']    = [round(x, 1) for x in mat_pipe]
        s['semi_pipe']   = [round(x, 1) for x in semi_pipe]
        s['fp_pipe']     = [round(x, 1) for x in fp_pipe]
        s['dist_pipe_a'] = [round(x, 1) for x in dist_pipe_a]
        s['dist_pipe_b'] = [round(x, 1) for x in dist_pipe_b]

        # --- COST BOOKING — entering-stage convention ---
        # shipped units enter Material stage this week → book RM cost (50% VC)
        # si      units enter Semi stage this week      → book +25% VC
        # fi      units enter FP stage this week        → book +25% VC
        s['cost_mat']  = round(shipped * var_cost * VALOR_RAW_MAT, 1)
        s['cost_semi'] = round(si      * var_cost * (VALOR_SEMI - VALOR_RAW_MAT), 1)
        s['cost_fp']   = round(fi      * var_cost * (VALOR_FINISHED - VALOR_SEMI), 1)

        # 10. Ramp counters advance the week AFTER factory activation
        if factory_active_from is not None and w > factory_active_from:
            pn += 1; sn += 1; fn += 1

        # 11. Post-processing WIP (for display)
        total_wip = (sum(mat_pipe) + sum(semi_pipe) + sum(fp_pipe)
                     + sum(dist_pipe_a) + sum(dist_pipe_b)
                     + raw_mat + semi + cw + pb)
        s['wip_total'] = round(total_wip, 1)
        s['pending']   = round(co - cas, 0)
        s['backlog']   = round(pb, 0)
        s['coverage']  = coverage

        # Commentary
        parts = []
        if missed_a > 0.5:
            parts.append(f"A: lost {missed_a:.0f}/{dem_a:.0f}.")
        else:
            parts.append(f"A: sold {sales_a:.0f}/{dem_a:.0f}, stk {store_a:.0f}.")
        if missed_b > 0.5:
            parts.append(f"B: lost {missed_b:.0f}/{dem_b:.0f}.")
        else:
            parts.append(f"B: sold {sales_b:.0f}/{dem_b:.0f}, stk {store_b:.0f}.")
        if s['order'] > 0:
            parts.append(f"ORDER {od:.0f}.")
        if shipped > 0.5:
            parts.append(f"Supplier {shipped:.0f}.")
        if ship_out > 0.5:
            mode = "smart" if (smart_distrib and smart_discovered) else "push 50/50"
            parts.append(f"WH→A:{alloc_a:.0f} B:{alloc_b:.0f} ({mode}).")
        s['comment'] = " ".join(parts)

        # Debug assertions — conservation within single-week state
        if debug:
            assert store_a >= -0.5 and store_b >= -0.5, f"W{w}: negative store stock"
            assert raw_mat >= -0.5 and semi >= -0.5 and cw >= -0.5, f"W{w}: negative buffer"
            assert pb >= -0.5, f"W{w}: negative backlog"

        states.append(s)

    # Debug: end-of-run unit conservation
    if debug:
        init_units = init_store + init_cw + init_semi + init_rawmat
        total_shipped = sum(s['supplier_shipped'] for s in states)
        total_sales = sum(s['sales'] for s in states)
        end_stock_u = states[-1]['store_a'] + states[-1]['store_b'] + cw + semi + raw_mat
        end_pipe_u = (sum(mat_pipe) + sum(semi_pipe) + sum(fp_pipe)
                      + sum(dist_pipe_a) + sum(dist_pipe_b))
        lhs = init_units + total_shipped
        rhs = total_sales + end_stock_u + end_pipe_u + pb
        assert abs(lhs - rhs) < 2.0, f"Unit conservation failed: {lhs:.1f} != {rhs:.1f}"

    return states


# ════════════════════════════════════════════════════════════════
# KPI COMPUTATION
# ════════════════════════════════════════════════════════════════

def _value_stock(store, cw_, semi_, rm_, var_cost):
    """Valorize stock units by their current stage position."""
    return (store  * var_cost * VALOR_FINISHED
          + cw_    * var_cost * VALOR_FINISHED
          + semi_  * var_cost * VALOR_SEMI
          + rm_    * var_cost * VALOR_RAW_MAT)


def _value_pipes(state, var_cost):
    """Valorize in-transit pipe units by stage position."""
    return (sum(state.get('mat_pipe', [0]))    * var_cost * VALOR_RAW_MAT
          + sum(state.get('semi_pipe', [0]))   * var_cost * VALOR_SEMI
          + sum(state.get('fp_pipe', [0]))     * var_cost * VALOR_FINISHED
          + sum(state.get('dist_pipe_a', [0])) * var_cost * VALOR_FINISHED
          + sum(state.get('dist_pipe_b', [0])) * var_cost * VALOR_FINISHED)


def compute_kpis(states, price, var_cost, fixed_pct, base_forecast, weeks,
                 init_store=0, init_cw=0, init_semi=0, init_rawmat=0,
                 debug=False):
    """
    Compute end-of-simulation KPIs and the P&L identity.

    P&L identity (must hold exactly under entering-stage accounting):

        Revenue = Total VC + Fixed + Margin
        Total VC = init_stock_value + Σ cost_mat + Σ cost_semi + Σ cost_fp

    Parameters
    ----------
    states : list[dict]
        Per-week states (typically `states[1:]` to exclude W0).

    Returns
    -------
    dict
        Includes revenue, margins, unit flows, stage-wise cost totals,
        leftover stock/pipe values, and efficiency metrics.
    """
    ts  = sum(s['sales']  for s in states)
    tm  = sum(s['missed'] for s in states)
    td  = sum(s['demand'] for s in states)
    tfp = sum(s['fp_input'] for s in states)

    # Initial stock value — pre-positioned, valued at stage of placement
    init_stock_value = _value_stock(init_store, init_cw, init_semi, init_rawmat, var_cost)

    # Production costs (incremental, no double-counting) — booked on entry
    cost_mat_total  = sum(s.get('cost_mat', 0)  for s in states)
    cost_semi_total = sum(s.get('cost_semi', 0) for s in states)
    cost_fp_total   = sum(s.get('cost_fp', 0)   for s in states)
    prod_cost = cost_mat_total + cost_semi_total + cost_fp_total

    vc  = init_stock_value + prod_cost
    rev = ts * price
    gm  = rev - vc
    fx  = base_forecast * weeks * price * fixed_pct
    mg  = gm - fx

    # End-of-sim stock & pipe values (valorized by stage)
    last = states[-1] if states else {}
    end_store    = last.get('store_a', 0) + last.get('store_b', 0)
    end_cw       = last.get('cw_stock', 0)
    end_semi     = last.get('semi_stock', 0)
    end_rawmat   = last.get('raw_mat_stock', 0)
    end_stock_units = end_store + end_cw + end_semi + end_rawmat
    end_stock_value = _value_stock(end_store, end_cw, end_semi, end_rawmat, var_cost)

    end_pipe_units = (sum(last.get('mat_pipe', [0]))
                    + sum(last.get('semi_pipe', [0]))
                    + sum(last.get('fp_pipe', [0]))
                    + sum(last.get('dist_pipe_a', [0]))
                    + sum(last.get('dist_pipe_b', [0])))
    end_pipe_value = _value_pipes(last, var_cost)

    # Efficiency — how much of "system throughput" actually sold?
    total_system_units = ts + end_stock_units + end_pipe_units
    useful_pct  = (ts / total_system_units * 100) if total_system_units > 0 else 0
    useless_units = end_stock_units + end_pipe_units
    useless_pct = (useless_units / total_system_units * 100) if total_system_units > 0 else 0

    cost_of_sold   = ts * var_cost
    cost_of_unsold = max(0, vc - cost_of_sold)

    # P&L identity check (always compute; optionally assert)
    pl_residual = rev - (vc + fx + mg)
    if debug:
        assert abs(pl_residual) < 0.5, f"P&L identity failed: residual {pl_residual:.3f}"

    return {
        'total_demand': td, 'total_sales': ts, 'total_missed': tm,
        'svc_level':      ts / td if td > 0 else 0,
        'stockout_weeks': sum(1 for s in states if s['missed'] > 0.5),
        'revenue': rev, 'var_cost': vc, 'gm': gm, 'fixed': fx,
        'margin': mg, 'margin_pct': mg / rev if rev > 0 else 0,
        'produced': tfp,
        'init_stock_value': init_stock_value,
        'prod_cost':       prod_cost,
        'cost_mat_total':  cost_mat_total,
        'cost_semi_total': cost_semi_total,
        'cost_fp_total':   cost_fp_total,
        'end_stock_value': end_stock_value,
        'end_pipe_value':  end_pipe_value,
        'end_stock_units': end_stock_units,
        'end_pipe_units':  end_pipe_units,
        'store_end':       end_store,
        'lost_rev':        tm * price,
        'missed_a': sum(s['missed_a'] for s in states),
        'missed_b': sum(s['missed_b'] for s in states),
        'sales_a':  sum(s['sales_a']  for s in states),
        'sales_b':  sum(s['sales_b']  for s in states),
        'useful_pct':   useful_pct,  'useless_pct':   useless_pct,
        'useful_units': ts,          'useless_units': useless_units,
        'total_system_units': total_system_units,
        'cost_of_sold': cost_of_sold, 'cost_of_unsold': cost_of_unsold,
        'leftover_value': end_stock_value + end_pipe_value,
        'pl_residual': pl_residual,
    }


def cumulative_kpis(states, week, price, var_cost, fixed_pct, base_forecast, total_weeks,
                    init_store=0, init_cw=0, init_semi=0, init_rawmat=0):
    """
    Compute KPIs cumulative up to (not including) `week`. Used by the header
    KPI cards which update as the user navigates the timeline.
    """
    sub = states[:week]
    ts = sum(s['sales']  for s in sub)
    tm = sum(s['missed'] for s in sub)
    td = sum(s['demand'] for s in sub)
    tfp = sum(s['fp_input'] for s in sub)
    init_stock_value = _value_stock(init_store, init_cw, init_semi, init_rawmat, var_cost)
    prod_cost = (sum(s.get('cost_mat', 0)  for s in sub)
               + sum(s.get('cost_semi', 0) for s in sub)
               + sum(s.get('cost_fp', 0)   for s in sub))
    vc  = init_stock_value + prod_cost
    rev = ts * price
    gm  = rev - vc
    fx  = base_forecast * week * price * fixed_pct
    mg  = gm - fx

    init_total = init_store + init_cw + init_semi + init_rawmat
    total_in = init_total + tfp
    useful_pct = (ts / total_in * 100) if total_in > 0 else 0

    return {
        'sales': ts, 'missed': tm, 'demand': td, 'revenue': rev,
        'svc_level': ts / td if td > 0 else 0, 'margin': mg,
        'stockout_wks': sum(1 for s in sub if s['missed'] > 0.5),
        'missed_a': sum(s['missed_a'] for s in sub),
        'missed_b': sum(s['missed_b'] for s in sub),
        'useful_pct': useful_pct, 'useless_pct': 100 - useful_pct,
    }


def reconciliation_report(states, kpis, params):
    """
    Build a list of reconciliation checks for the Debug expander.
    Each entry: (label, passed_bool, detail_string).
    """
    var_cost   = params['var_cost']
    init_units = params['init_store'] + params['init_cw'] + params['init_semi'] + params['init_rawmat']
    total_shipped = sum(s['supplier_shipped'] for s in states)
    total_sales   = sum(s['sales'] for s in states)
    last = states[-1]
    end_stock_u = (last['store_a'] + last['store_b']
                 + last.get('cw_stock', 0) + last.get('semi_stock', 0)
                 + last.get('raw_mat_stock', 0))
    end_pipe_u = (sum(last.get('mat_pipe', []))    + sum(last.get('semi_pipe', []))
                + sum(last.get('fp_pipe', []))     + sum(last.get('dist_pipe_a', []))
                + sum(last.get('dist_pipe_b', [])))
    backlog = last.get('backlog', 0)

    checks = []

    # Check 1: physical unit conservation
    lhs = init_units + total_shipped
    rhs = total_sales + end_stock_u + end_pipe_u + backlog
    checks.append((
        "Physical units conserved",
        abs(lhs - rhs) < 2.0,
        f"init+shipped ({lhs:.0f}) ≈ sales+stock+pipe+backlog ({rhs:.0f}), Δ={lhs-rhs:+.1f}",
    ))

    # Check 2: value conservation (in = out + remaining)
    value_in = kpis['init_stock_value'] + kpis['prod_cost']
    value_remaining = kpis['leftover_value']
    value_sold = kpis['cost_of_sold']
    checks.append((
        "Stage value conserved",
        abs(value_in - value_sold - value_remaining) < 1.0,
        f"VC (€{value_in:,.0f}) ≈ sold (€{value_sold:,.0f}) + leftover (€{value_remaining:,.0f})",
    ))

    # Check 3: P&L identity
    checks.append((
        "P&L identity (Rev = VC + Fixed + Margin)",
        abs(kpis['pl_residual']) < 0.5,
        f"residual €{kpis['pl_residual']:+,.2f}",
    ))

    # Check 4: W0 has zero production costs
    s0 = states[0] if states and states[0]['week'] == 0 else None
    w0_zero = True if s0 is None else (s0['cost_mat'] == 0 and s0['cost_semi'] == 0 and s0['cost_fp'] == 0)
    checks.append((
        "W0 production costs = 0",
        w0_zero,
        "confirmed" if w0_zero else "FAIL — W0 has non-zero costs",
    ))

    # Check 5: no negative stocks across the run
    any_neg = any(s['store_a'] < -0.5 or s['store_b'] < -0.5
                  or s.get('cw_stock', 0) < -0.5 or s.get('semi_stock', 0) < -0.5
                  or s.get('raw_mat_stock', 0) < -0.5
                  for s in states)
    checks.append((
        "No negative stocks anywhere",
        not any_neg,
        "all non-negative" if not any_neg else "NEGATIVE FOUND — check engine",
    ))

    return checks


# ════════════════════════════════════════════════════════════════
# DIAGRAM BUILDERS — module-level helpers
# ════════════════════════════════════════════════════════════════
# (filled in next chunk)


# ════════════════════════════════════════════════════════════════
# PRESET APPLICATION
# ════════════════════════════════════════════════════════════════
# (filled in next chunk)


# ════════════════════════════════════════════════════════════════
# SIDEBAR UI
# ════════════════════════════════════════════════════════════════
# (filled in next chunk)


# ════════════════════════════════════════════════════════════════
# MAIN PAGE UI
# ════════════════════════════════════════════════════════════════
# (filled in next chunk)
