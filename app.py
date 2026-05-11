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

# Stock distribution per LT profile.
# Two variants — operational presets keep the original v1 setup (gated by
# first-order, so Drop scenarios stay efficient); seasonal presets use a
# more pre-positioned distribution and rely on Kickstart to unstick the
# "well-sized stock + no order placed" edge case.
STOCK_DIST_OPERATIONAL = {
    "Agile":  {"store_pct": 60, "wh_pct": 20, "semi_pct": 10},   # RM = 10
    "Medium": {"store_pct": 80, "wh_pct": 20, "semi_pct": 0},    # RM = 0
    "Push":   {"store_pct": 100, "wh_pct": 0, "semi_pct": 0},    # RM = 0
}
STOCK_DIST_SEASONAL = {
    "Agile":  {"store_pct": 40, "wh_pct": 20, "semi_pct": 10},   # RM = 30
    "Medium": {"store_pct": 70, "wh_pct": 20, "semi_pct": 10},   # RM = 0
    "Push":   {"store_pct": 100, "wh_pct": 0, "semi_pct": 0},    # RM = 0
}

# Seasonal presets size initial stock by sell-through target: longer LT
# profiles must over-order to hedge demand uncertainty. Agile can run
# near 100% sell-through; Push can't react, so it needs a larger buffer
# and accepts that much of the stock won't sell.
SEASONAL_BASE_STOCK = 2600
SELL_THROUGH_SEASONAL = {
    "Agile":  1.00,   # 100% sell-through target → 2600 pcs
    "Medium": 0.85,   # 15% buffer               → 3059 pcs
    "Push":   0.60,   # 40% buffer               → 4333 pcs
}
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
    "total_stock": 1500,
    "store_pct": 40, "wh_pct": 20, "semi_pct": 10,
    "store_a_pct": 60, "smart_distrib": True,
    "demand_shape": DEMAND_SHAPES[0],
    "kickstart": False,
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
    Two independent gates control processing:

      1. `factory_active_from` is set ONLY by the first supplier order. Once
         set, Semi and FP processing run every week (capacity-limited) and
         the ramp counters (pn/sn/fn) advance from the next week.

      2. `kickstart` (one-shot at W1): if True and initial RM or Semi exists,
         Semi and FP each run ONCE at W1 only — pushing one wave of initial
         WIP one stage forward. This unsticks seasonal scenarios where the
         planner never orders because initial stock is well-sized. After W1,
         processing pauses again and waits for the first real order.

    Parameters
    ----------
    kickstart : bool
        If True, do a one-shot Semi+FP processing pass at W1 when initial
        RM/Semi exist. Does NOT permanently activate the factory.
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

    # Two independent gates (see docstring):
    #   factory_active_from — set on first supplier order, then permanent
    #   do_kickstart        — one-shot processing at W1 only
    factory_active_from = None
    do_kickstart = kickstart and (init_rawmat > 0 or init_semi > 0)

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

        # 3. Arrivals update buffers
        raw_mat += m_arr
        semi    += sm_arr
        cw      += fp_arr
        s['raw_mat_before_prod'] = round(raw_mat, 1)

        # 4. Order decision (Monday morning — planner reviews against full
        #    inventory position BEFORE this week's supplier ship). Placing
        #    the order before the ship in the same week mirrors how a real
        #    planner operates and eliminates a timing artifact in which
        #    units just shipped by the supplier were briefly invisible to
        #    the planner (between pb and mat_pipe), causing systematic
        #    over-ordering by `shipped` on every review week.
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

        # 5. Supplier ships (Monday afternoon — against the now-updated
        #    backlog, so a fresh order placed this week can start shipping
        #    immediately if capacity allows). Capacity ramps after factory
        #    activation.
        pc = min(cap_start * (1 + pn * cap_ramp), cap_start * 10)
        if pb > 0.01:
            shipped = math.ceil(min(pb, pc))
            pb -= shipped
        else:
            shipped = 0.0
        s['supplier_shipped'] = round(shipped, 1)
        s['supplier_cap']     = round(pc, 0)

        # Processing is allowed when either:
        #   (a) the factory has been activated by a real order, OR
        #   (b) we're in the one-shot kickstart window (W1 only)
        allow_proc = (factory_active_from is not None) or (do_kickstart and w == 1)

        # 6. Semi processing (RM → Semi)
        sc_ = min(cap_start * (1 + sn * cap_ramp), cap_start * 10)
        if raw_mat > 0.01 and allow_proc:
            si = math.ceil(min(raw_mat, sc_))
            raw_mat -= si
        else:
            si = 0.0
        s['semi_input'] = round(si, 1)
        s['semi_cap']   = round(sc_, 0)
        s['raw_mat_stock'] = round(raw_mat, 1)

        # 7. FP processing (Semi → FP)
        fpc = min(cap_start * (1 + fn * cap_ramp), cap_start * 10)
        if semi > 0.01 and allow_proc:
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
    # Backlog (pb) is phantom — outstanding orders the supplier hasn't yet shipped,
    # so those units don't physically exist in our system. Don't count them here.
    if debug:
        init_units = init_store + init_cw + init_semi + init_rawmat
        total_shipped = sum(s['supplier_shipped'] for s in states)
        total_sales = sum(s['sales'] for s in states)
        end_stock_u = states[-1]['store_a'] + states[-1]['store_b'] + cw + semi + raw_mat
        end_pipe_u = (sum(mat_pipe) + sum(semi_pipe) + sum(fp_pipe)
                      + sum(dist_pipe_a) + sum(dist_pipe_b))
        lhs = init_units + total_shipped
        rhs = total_sales + end_stock_u + end_pipe_u
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

    checks = []

    # Check 1: physical unit conservation
    # Backlog is excluded on purpose — it represents orders not yet materialized
    # (phantom units the supplier still owes us).
    lhs = init_units + total_shipped
    rhs = total_sales + end_stock_u + end_pipe_u
    checks.append((
        "Physical units conserved",
        abs(lhs - rhs) < 2.0,
        f"init+shipped ({lhs:.0f}) ≈ sales+stock+pipe ({rhs:.0f}), Δ={lhs-rhs:+.1f}",
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
#
# Design rules (all enforced below):
#   - Stage columns ALWAYS in one horizontal row; only weeks within a stage
#     wrap (after 8 boxes, wrap to a second row WITHIN that stage).
#   - No black contours — fill-only, borderless, rounded 6px.
#   - Arial font throughout; grey-blue neutral palette.
#   - CARD_W (supplier/store) = 98px; week-box width adaptive.
#   - Exactly one `st.components.v1.html` call at render time (see main page).
#   - JS wrapper scales the diagram to fit parent width via transform:scale().

# --- Palette ---
C_TXT        = '#2a3a4e'
C_TXT_L      = '#5a6a7e'
C_BOX_FILL   = '#4a6280'
C_BOX_FG     = '#ffffff'
C_BOX_EMPTY  = '#e8ecf2'
C_BOX_EMPTY_FG = '#8a96a6'
C_HEADER_BG  = '#dce3ed'
C_HEADER_FG  = '#2a3a4e'
C_WIP_BG     = '#e4e9f0'
C_WIP_ACCENT = '#4a6280'
C_PROC_SHADOW = '#2a4058'
C_SUP_BG     = '#2a3a52'
C_STORE_BG   = '#f4f6f9'
C_LOST_BG    = '#c05050'
C_LOST_FG    = '#ffffff'

CARD_W = 98
MAX_PER_ROW = 8   # wrap after 8 boxes within a stage
GAP_PX = 3


def _compute_box_dims(total_boxes_row: int) -> tuple[int, int]:
    """
    Adaptive week-box dimensions. Returns (width, height) in px.

        width  = (1300 − 3×CARD_W − 60) / total_boxes_row − GAP_PX,
                 clamped to [44, 110]
        height = width − 8, clamped to [50, 74]
    """
    available = 1300 - 3 * CARD_W - 60
    target_w = (available / max(total_boxes_row, 1)) - GAP_PX
    box_w = max(44, min(110, int(target_w)))
    box_h = max(50, min(74, box_w - 8))
    return box_w, box_h


def week_box(qty: float, box_w: int, box_h: int, is_proc: bool = False) -> str:
    """
    One week slot within a stage band. Rounded, borderless, filled if qty > 0.

    The 'processing' (last) week of a stage gets a subtle left inset shadow
    instead of a contour to avoid visual noise.
    """
    if qty > 0.5:
        bg, fg, weight = C_BOX_FILL, C_BOX_FG, "700"
        content = f"{qty:.0f}"
    else:
        bg, fg, weight = C_BOX_EMPTY, C_BOX_EMPTY_FG, "400"
        content = ""
    proc = f"box-shadow: inset 3px 0 0 {C_PROC_SHADOW};" if is_proc else ""
    return (
        f'<div style="width:{box_w}px;height:{box_h}px;background:{bg};'
        f'border:none;border-radius:6px;display:flex;align-items:center;'
        f'justify-content:center;font-size:15px;font-weight:{weight};'
        f'color:{fg};{proc}box-sizing:border-box;">{content}</div>'
    )


def band_header(label: str, width_px: int) -> str:
    """Soft header strip above a stage's week boxes."""
    return (
        f'<div style="width:{width_px}px;background:{C_HEADER_BG};'
        f'border-radius:6px;padding:6px 4px;text-align:center;font-size:12px;'
        f'font-weight:600;color:{C_HEADER_FG};box-sizing:border-box;">{label}</div>'
    )


def wip_label(label: str, value: float, width_px: int) -> str:
    """WIP total row — visible accent anchoring it to its stage."""
    return (
        f'<div style="width:{width_px}px;background:{C_WIP_BG};'
        f'border-left:3px solid {C_WIP_ACCENT};border-radius:4px;padding:5px 8px;'
        f'display:flex;justify-content:space-between;align-items:center;'
        f'font-size:11px;color:{C_TXT};box-sizing:border-box;">'
        f'<span style="font-weight:600;color:{C_WIP_ACCENT};">{label}</span>'
        f'<span style="font-weight:800;color:#1a2a3e;font-size:12px;">{value:.0f}</span></div>'
    )


def boxes_row(weeks: list[float], box_w: int, box_h: int,
              proc_last: bool = True, weeks_labels_start: int = 1) -> str:
    """
    Render a stage's week boxes with W-labels above. Wraps to multiple rows
    if len(weeks) > MAX_PER_ROW.
    """
    n = len(weeks)
    if n == 0:
        return ""
    rows_html = []
    for start in range(0, n, MAX_PER_ROW):
        end = min(start + MAX_PER_ROW, n)
        chunk = weeks[start:end]
        labels_html = "".join(
            f'<div style="width:{box_w}px;text-align:center;font-size:10px;'
            f'color:{C_TXT_L};font-weight:600;margin-bottom:2px;">W{weeks_labels_start + start + i}</div>'
            for i in range(len(chunk))
        )
        boxes_html = "".join(
            week_box(chunk[i], box_w, box_h, is_proc=(proc_last and (start + i) == n - 1))
            for i in range(len(chunk))
        )
        rows_html.append(
            f'<div style="display:flex;gap:{GAP_PX}px;">{labels_html}</div>'
            f'<div style="display:flex;gap:{GAP_PX}px;margin-top:2px;margin-bottom:4px;">{boxes_html}</div>'
        )
    return "".join(rows_html)


def _band_width(n_weeks: int, box_w: int) -> int:
    """Visual width of a band header = width of one capped row of boxes."""
    cols = min(n_weeks, MAX_PER_ROW)
    return cols * box_w + (cols - 1) * GAP_PX + 8


def stage_col(label: str, weeks_list: list[float], box_w: int, box_h: int,
              wip_value: float, wip_txt: str, weeks_start: int) -> str:
    """One complete stage column: header, labelled week boxes, WIP total."""
    band_w = _band_width(len(weeks_list), box_w)
    return (
        f'<div style="display:flex;flex-direction:column;align-items:center;gap:4px;">'
        f'{band_header(label, band_w)}'
        f'<div>{boxes_row(weeks_list, box_w, box_h, proc_last=True, weeks_labels_start=weeks_start)}</div>'
        f'{wip_label(wip_txt, wip_value, band_w)}'
        f'</div>'
    )


def supplier_card(backlog: float, cap: float, box_h: int) -> str:
    """Left-hand supplier card: Order header, backlog count, capacity footer."""
    return (
        f'<div style="display:flex;flex-direction:column;align-items:center;gap:4px;">'
        f'{band_header("Order", CARD_W)}'
        f'<div style="width:{CARD_W}px;height:{box_h + 20}px;background:{C_SUP_BG};'
        f'border-radius:6px;padding:4px 6px;display:flex;flex-direction:column;'
        f'align-items:center;justify-content:center;color:#fff;box-sizing:border-box;">'
        f'<div style="font-size:10px;font-weight:500;color:#9aaec6;'
        f'text-transform:uppercase;letter-spacing:0.5px;">Supplier</div>'
        f'<div style="font-size:20px;font-weight:700;">{backlog:.0f}</div>'
        f'</div>'
        f'<div style="width:{CARD_W}px;background:#f4f6f9;border-radius:5px;'
        f'padding:5px 8px;font-size:10px;color:{C_TXT};display:flex;'
        f'justify-content:space-between;box-sizing:border-box;">'
        f'<span style="color:{C_TXT_L};font-weight:500;">Cap</span>'
        f'<span style="font-weight:700;color:#2a3a4e;">{cap:.0f}</span></div>'
        f'</div>'
    )


def store_card(letter: str, stock: float, dem: float, sales: float, lost: float) -> str:
    """
    Compact store card: STOCK value + DEM/SOLD row + LOST pill if lost > 0.5.
    LOST badge is rendered inline-block with margin-top to avoid clipping.
    """
    is_alert = lost > 0.5
    bg = '#fef0f0' if is_alert else C_STORE_BG
    accent = '#c05050' if is_alert else 'transparent'
    alert_shadow = f"box-shadow: inset 3px 0 0 {accent};" if is_alert else ""
    lost_badge = (
        f'<div style="margin-top:5px;background:{C_LOST_BG};color:{C_LOST_FG};'
        f'padding:1px 5px;border-radius:3px;font-size:9px;font-weight:700;'
        f'letter-spacing:0.3px;display:inline-block;">LOST {int(lost)}</div>'
    ) if is_alert else ''
    return (
        f'<div style="display:flex;flex-direction:column;gap:3px;">'
        f'{band_header(f"Store {letter}", CARD_W)}'
        f'<div style="width:{CARD_W}px;background:{bg};{alert_shadow}'
        f'border-radius:6px;padding:8px 6px;text-align:center;'
        f'box-sizing:border-box;color:{C_TXT};">'
        f'<div style="color:{C_TXT_L};font-weight:500;font-size:9px;'
        f'text-transform:uppercase;letter-spacing:0.3px;">Stock</div>'
        f'<div style="font-size:20px;font-weight:700;color:{C_TXT};line-height:1.1;'
        f'margin:2px 0 6px;">{stock:.0f}</div>'
        f'<div style="display:flex;justify-content:space-between;padding:0 6px;'
        f'gap:8px;font-size:9px;">'
        f'<div style="text-align:center;"><div style="color:{C_TXT_L};'
        f'text-transform:uppercase;letter-spacing:0.3px;">Dem</div>'
        f'<div style="font-size:13px;font-weight:600;color:{C_TXT};'
        f'line-height:1.1;margin-top:2px;">{dem:.0f}</div></div>'
        f'<div style="text-align:center;"><div style="color:{C_TXT_L};'
        f'text-transform:uppercase;letter-spacing:0.3px;">Sold</div>'
        f'<div style="font-size:13px;font-weight:600;color:#2a5a3a;'
        f'line-height:1.1;margin-top:2px;">{sales:.0f}</div></div>'
        f'</div>{lost_badge}</div></div>'
    )


def make_sc_html(state: dict, params: dict) -> str:
    """
    Render the full week-by-week supply-chain flow diagram.

    Layout: supplier card | [Material | Semi | Finish+CW | Distribution] | stores
    Stages are always in a single horizontal row; weeks within a stage wrap
    after MAX_PER_ROW. Stores are stacked vertically on the right.

    Returns a single HTML string (one st.components.v1.html call expected).
    """
    mat_lt  = params['mat_lt']
    semi_lt = params['semi_lt']
    fp_lt   = params['fp_lt']
    dist_lt = params['dist_lt']

    total_boxes_row = (min(mat_lt, MAX_PER_ROW) + min(semi_lt, MAX_PER_ROW)
                     + min(fp_lt, MAX_PER_ROW)  + min(dist_lt, MAX_PER_ROW))
    box_w, box_h = _compute_box_dims(total_boxes_row)

    # Pipe → displayed weeks: reversed so W1 is leftmost, W_last is rightmost.
    # Stage buffer stock is added to the LAST week of its band (about to exit).
    def _reversed(lst): return list(reversed(lst)) if lst else []

    mat_weeks  = _reversed(state.get('mat_pipe', []))
    semi_weeks = _reversed(state.get('semi_pipe', []))
    fp_weeks   = _reversed(state.get('fp_pipe', []))
    raw_mat = state.get('raw_mat_stock', 0)
    semi    = state.get('semi_stock', 0)
    cw      = state.get('cw_stock', 0)

    if mat_weeks:  mat_weeks[-1]  = mat_weeks[-1]  + raw_mat
    if semi_weeks: semi_weeks[-1] = semi_weeks[-1] + semi
    if fp_weeks:   fp_weeks[-1]   = fp_weeks[-1]   + cw

    dist_a = _reversed(state.get('dist_pipe_a', []))
    dist_b = _reversed(state.get('dist_pipe_b', []))
    dist_combined = [dist_a[i] + dist_b[i] for i in range(len(dist_a))] if dist_a else []

    # WIP per band
    wip_mat  = sum(state.get('mat_pipe', []))    + raw_mat
    wip_semi = sum(state.get('semi_pipe', []))   + semi
    wip_fp   = sum(state.get('fp_pipe', []))     + cw
    wip_da   = sum(state.get('dist_pipe_a', []))
    wip_db   = sum(state.get('dist_pipe_b', []))

    # Stage columns
    mat_label  = f"Mat ({mat_lt}wk)"       if mat_lt <= 2 else f"Material ({mat_lt}wk)"
    semi_label = f"Semi ({semi_lt}wk)"
    fp_label   = f"Finish ({fp_lt}wk)"     if fp_lt <= 2 else f"Finish+CW ({fp_lt}wk)"
    dist_label = f"Dist ({dist_lt}wk)"     if dist_lt <= 2 else f"Distribution ({dist_lt}wk)"

    mat_col = stage_col(mat_label, mat_weeks, box_w, box_h, wip_mat, "WIP", 1)
    semi_col = stage_col(semi_label, semi_weeks, box_w, box_h, wip_semi, "WIP", mat_lt + 1)
    fp_col = stage_col(fp_label, fp_weeks, box_w, box_h, wip_fp, "WIP", mat_lt + semi_lt + 1)

    dist_band_w = _band_width(dist_lt, box_w)
    dist_col = (
        f'<div style="display:flex;flex-direction:column;align-items:center;gap:4px;">'
        f'{band_header(dist_label, dist_band_w)}'
        f'<div>{boxes_row(dist_combined, box_w, box_h, proc_last=True, weeks_labels_start=mat_lt + semi_lt + fp_lt + 1)}</div>'
        f'<div style="display:flex;flex-direction:column;gap:3px;width:{dist_band_w}px;">'
        f'{wip_label("WIP A", wip_da, dist_band_w)}'
        f'{wip_label("WIP B", wip_db, dist_band_w)}'
        f'</div></div>'
    )

    # Supplier + stacked stores
    sup_html = supplier_card(state.get('backlog', 0), state.get('supplier_cap', 0), box_h)
    store_a_html = store_card("A", state.get('store_a', 0), state.get('demand_a', 0),
                              state.get('sales_a', 0), state.get('missed_a', 0))
    store_b_html = store_card("B", state.get('store_b', 0), state.get('demand_b', 0),
                              state.get('sales_b', 0), state.get('missed_b', 0))
    stores_html = (
        f'<div style="display:flex;flex-direction:column;gap:6px;justify-content:center;'
        f'align-self:stretch;">{store_a_html}{store_b_html}</div>'
    )

    main = (
        f'<div style="display:flex;align-items:center;gap:10px;">'
        f'<div style="display:flex;align-items:flex-start;gap:10px;">'
        f'{sup_html}{mat_col}{semi_col}{fp_col}{dist_col}</div>'
        f'{stores_html}</div>'
    )

    # Info bar (top) + comment (bottom)
    order_html = (
        f'<b style="color:#2a5a3a;font-size:13px;">ORDER {state["order"]:.0f}</b>'
        if state.get('order', 0) > 0 else f'<span style="color:{C_TXT_L};">No order</span>'
    )
    info_bar = (
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'padding:8px 16px;background:linear-gradient(90deg,#f4f6f9,#eef1f6);'
        f'border:1px solid #dde2ea;border-radius:8px;margin-bottom:10px;'
        f'font-family:Arial,Helvetica,sans-serif;">'
        f'<span style="font-size:12px;color:{C_TXT};">Backlog <b style="color:#8a3030;">{state.get("backlog", 0):.0f}</b></span>'
        f'<span style="font-size:12px;color:{C_TXT};">Pending <b style="color:#8a6a20;">{state.get("pending", 0):.0f}</b></span>'
        f'<span style="font-size:12px;color:{C_TXT};">WIP <b style="color:#2a5a8a;">{state.get("wip_total", 0):.0f}</b></span>'
        f'<span style="font-size:12px;">{order_html}</span>'
        f'<span style="font-size:12px;color:{C_TXT};">Forecast <b style="color:#1a2a40;">{state.get("forecast", 0):.0f}</b>/wk</span>'
        f'<span style="font-size:12px;color:{C_TXT};">A:{params.get("store_a_pct", 60)}% B:{100 - params.get("store_a_pct", 60)}%</span>'
        f'</div>'
    )
    comment = state.get('comment', '')
    comment_html = (
        f'<div style="padding:8px 16px;font-size:11px;color:{C_TXT};line-height:1.5;'
        f'background:#f8f9fb;border:1px solid #e8ecf0;border-radius:6px;margin-top:10px;">{comment}</div>'
        if comment else ''
    )
    physical_flow = (
        f'<div style="text-align:center;padding:8px 0;">'
        f'<span style="font-size:10px;color:{C_TXT_L};letter-spacing:2px;font-weight:700;">'
        f'- - - PHYSICAL FLOW (GOODS) - - -</span></div>'
    )

    # Intrinsic width — used by the JS scaler to compute fit-to-parent ratio
    intrinsic_w = (min(mat_lt, MAX_PER_ROW) + min(semi_lt, MAX_PER_ROW)
                 + min(fp_lt, MAX_PER_ROW)  + min(dist_lt, MAX_PER_ROW)) * (box_w + GAP_PX)
    intrinsic_w += 3 * CARD_W + 60

    container = (
        f'<div style="font-family:Arial,Helvetica,sans-serif;padding:8px;'
        f'background:linear-gradient(90deg,#f6f8fa,#f0f2f6);'
        f'border:1px solid #dde2ea;border-radius:12px;'
        f'width:100%;box-sizing:border-box;overflow:hidden;">'
        f'<div id="sc-scaler" style="transform-origin:top left;width:{intrinsic_w}px;">'
        f'{main}</div>'
        f'</div>'
        f'<script>'
        f'(function(){{'
        f'  var scaler=document.getElementById("sc-scaler");'
        f'  if(!scaler) return;'
        f'  var natural={intrinsic_w};'
        f'  var wrapper=scaler.parentElement;'
        f'  function fit(){{'
        f'    var avail=wrapper.clientWidth - 16;'
        f'    var scale=Math.min(1, avail/natural);'
        f'    scaler.style.transform="scale("+scale+")";'
        f'    wrapper.style.height=(scaler.offsetHeight*scale + 16)+"px";'
        f'  }}'
        f'  fit();'
        f'  window.addEventListener("resize",fit);'
        f'  setTimeout(fit,100);setTimeout(fit,500);'
        f'}})();'
        f'</script>'
    )

    return f'<div style="font-family:Arial,Helvetica,sans-serif;">{info_bar}{container}{physical_flow}{comment_html}</div>'


# ════════════════════════════════════════════════════════════════
# PRESET APPLICATION
# ════════════════════════════════════════════════════════════════
#
# Two preset families (18 buttons total):
#   1. Permanent grid: 3 LT × 3 demand shapes (Flat / Growth / Drop)
#        - Stock auto-sized to coverage × BASE_FORECAST
#        - Distributions: STOCK_DIST_OPERATIONAL
#   2. Seasonal grid: 3 LT × 3 seasonal averages (30 / 100 / 300), Steep curve
#        - Stock = SEASONAL_BASE_STOCK (2600) / sell-through target
#          Agile 100% → 2600, Medium 85% → 3059, Push 60% → 4333
#        - Distributions: STOCK_DIST_SEASONAL
#
# Common to all 18 presets: A=60%, smart distribution ON, kickstart OFF.
# Initial WIP (RM/Semi) only ever moves after the first supplier order.

def apply_preset(lt_name: str, demand_kind: str, *,
                 lr_end: int = 300, lr_wks: int = 5,
                 ld_end: int = 30,  ld_wks: int = 1,
                 seas_avg: int = 100, seas_sub: str = "Steep") -> None:
    """
    Mutates session_state to apply one of the 18 quick scenarios.

    Two preset families with different stock sizing + distribution:

      Permanent (Flat / Growth / Drop)
        - Stock auto-sized to coverage × BASE_FORECAST
        - Distributions: STOCK_DIST_OPERATIONAL (closer to v1)

      Seasonal (avg 30 / 100 / 300, Steep curve)
        - Stock sized by sell-through target: SEASONAL_BASE_STOCK (2600)
          divided by the LT profile's sell-through %. Agile=2600, Medium≈3059,
          Push≈4333 — longer LT hedges with more over-ordering.
        - Distributions: STOCK_DIST_SEASONAL (more RM/Semi pre-positioned)

    Kickstart is OFF for both families: initial WIP stays put until the
    planner places the first order. Over-positioned RM/Semi that never
    gets used is a deliberate teaching outcome, not a bug.

    Parameters
    ----------
    lt_name : "Agile" | "Medium" | "Push"
    demand_kind : "Flat" | "Growth" | "Drop" | "Seasonal"
    """
    lt = LT_PROFILES[lt_name]
    st.session_state["mat_lt"]       = lt["mat_lt"]
    st.session_state["semi_lt"]      = lt["semi_lt"]
    st.session_state["fp_lt"]        = lt["fp_lt"]
    st.session_state["dist_lt"]      = lt["dist_lt"]
    st.session_state["order_freq"]   = lt["order_freq"]

    is_seasonal = (demand_kind == "Seasonal")

    if is_seasonal:
        dist = STOCK_DIST_SEASONAL[lt_name]
        sell_through = SELL_THROUGH_SEASONAL[lt_name]
        st.session_state["total_stock"] = int(round(SEASONAL_BASE_STOCK / sell_through))
    else:
        dist = STOCK_DIST_OPERATIONAL[lt_name]
        coverage = lt["mat_lt"] + lt["semi_lt"] + lt["fp_lt"] + lt["dist_lt"] + lt["order_freq"]
        st.session_state["total_stock"] = min(BASE_FORECAST * coverage, 10000)

    # Kickstart OFF for all presets: initial WIP only moves when the planner
    # places an order. If pre-positioned RM/Semi never gets used because the
    # planner never orders, that's a real teaching point about over-investing
    # upstream — not something to paper over with a force-process.
    st.session_state["kickstart"]    = False

    st.session_state["store_pct"]    = dist["store_pct"]
    st.session_state["wh_pct"]       = dist["wh_pct"]
    st.session_state["semi_pct"]     = dist["semi_pct"]
    st.session_state["store_a_pct"]  = 60
    st.session_state["smart_distrib"] = True

    if demand_kind == "Flat":
        st.session_state["demand_shape"] = DEMAND_SHAPES[0]
    elif demand_kind == "Growth":
        st.session_state["demand_shape"] = DEMAND_SHAPES[1]
        st.session_state["lr_end"] = lr_end
        st.session_state["lr_wks"] = lr_wks
    elif demand_kind == "Drop":
        st.session_state["demand_shape"] = DEMAND_SHAPES[2]
        st.session_state["ld_end"] = ld_end
        st.session_state["ld_wks"] = ld_wks
    elif demand_kind == "Seasonal":
        st.session_state["demand_shape"] = DEMAND_SHAPES[3]
        st.session_state["seas_sub"] = seas_sub
        st.session_state["seas_avg"] = seas_avg

    # Reset navigation so user sees W0 of the new scenario
    st.session_state["week_num"] = 0


# ════════════════════════════════════════════════════════════════
# SIDEBAR UI
# ════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## ⚙️ Supply Chain Setup")
    weeks = st.select_slider("Simulation Length (weeks)", options=[13, 26, 39, 52], value=26)

    # --- Lead times ---
    st.markdown("### \U0001f517 Lead Times (weeks)")
    c1, c2 = st.columns(2)
    with c1:
        mat_lt  = st.number_input("Material",  min_value=1, max_value=24, step=1, key="mat_lt")
        semi_lt = st.number_input("Semi-Fin",  min_value=1, max_value=12, step=1, key="semi_lt")
    with c2:
        fp_lt   = st.number_input("Finishing", min_value=1, max_value=12, step=1, key="fp_lt")
        dist_lt = st.number_input("Distribution", min_value=1, max_value=12, step=1, key="dist_lt")
    phys_lt = mat_lt + semi_lt + fp_lt + dist_lt
    st.caption(f"Physical LT: **{phys_lt}** weeks")

    # --- Planning ---
    st.markdown("### \U0001f4cb Planning")
    order_freq = st.slider("Order / Replenishment Frequency (weeks)",
                           min_value=1, max_value=4, step=1, key="order_freq")
    coverage = phys_lt + order_freq
    st.caption(f"Base forecast: **{BASE_FORECAST}** pcs/wk (fixed)")
    st.caption(f"Coverage target: **{coverage}** weeks (LT {phys_lt} + freq {order_freq})")

    # --- Initial stock with smart recommendation ---
    st.markdown("### \U0001f4e6 Initial Stock")
    _ds = st.session_state.get("demand_shape", DEMAND_SHAPES[0])
    rec_units, rec_detail = recommend_initial_stock(
        _ds, weeks, coverage,
        base=BASE_FORECAST,
        lr_end=st.session_state.get("lr_end", 300),
        lr_wks=st.session_state.get("lr_wks", 5),
        ld_end=st.session_state.get("ld_end", 30),
        ld_wks=st.session_state.get("ld_wks", 1),
        seas_sub=st.session_state.get("seas_sub", "Steep"),
        seas_avg=st.session_state.get("seas_avg", 100),
    )
    st.markdown(
        f'<div style="color:#8a96a6;font-size:12px;font-style:italic;margin-bottom:8px;">'
        f'[Recommended: <b>{rec_units:,.0f}</b> pcs = {rec_detail}] '
        f'<span style="color:#a8b4c4;">— based on demand profile "{_ds}"</span></div>',
        unsafe_allow_html=True,
    )
    total_stock = st.slider("Total Initial Stock (pcs)", min_value=0, max_value=10000, step=50, key="total_stock")

    st.caption("Distribution (% of total):")
    # Guard against stale percentages summing > 100 after a preset switch
    _sp  = st.session_state.get("store_pct", 40)
    _wp  = st.session_state.get("wh_pct", 20)
    _sep = st.session_state.get("semi_pct", 10)
    if _wp > 100 - _sp:
        st.session_state["wh_pct"] = max(0, 100 - _sp)
    if _sep > 100 - _sp - st.session_state.get("wh_pct", 0):
        st.session_state["semi_pct"] = max(0, 100 - _sp - st.session_state.get("wh_pct", 0))

    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        store_pct = st.number_input("Store %", min_value=0, max_value=100, step=5, key="store_pct")
    with sc2:
        wh_max = max(0, 100 - store_pct)
        warehouse_pct = st.number_input("Warehouse %", min_value=0, max_value=wh_max, step=5, key="wh_pct")
    with sc3:
        semi_max = max(0, 100 - store_pct - warehouse_pct)
        semi_pct = st.number_input("Semi-Fin %", min_value=0, max_value=semi_max, step=5, key="semi_pct")
    rawmat_pct = 100 - store_pct - warehouse_pct - semi_pct

    init_store  = int(round(total_stock * store_pct / 100))
    init_cw     = int(round(total_stock * warehouse_pct / 100))
    init_semi   = int(round(total_stock * semi_pct / 100))
    init_rawmat = total_stock - init_store - init_cw - init_semi

    st.markdown(
        f'<div style="background:#f0f2f5;border-radius:8px;padding:8px 12px;font-size:13px;line-height:1.8;">'
        f'<b>Store:</b> {init_store} ({store_pct}%) @ 100% <i>(always 50/50 initial)</i> | '
        f'<b>WH:</b> {init_cw} ({warehouse_pct}%) @ 100% | '
        f'<b>Semi:</b> {init_semi} ({semi_pct}%) @ 75% | '
        f'<b>RM:</b> {init_rawmat} ({rawmat_pct}%) @ 50%</div>',
        unsafe_allow_html=True,
    )

    # --- Store demand split ---
    st.markdown("### \U0001f3ea Store Demand Split")
    store_a_pct = st.slider("Store A demand (%)", 0, 100, step=5, key="store_a_pct")
    smart_distrib = st.toggle("Smart Distribution (need-based)", key="smart_distrib")
    if smart_distrib:
        st.caption(f"A: **{store_a_pct}%** B: **{100-store_a_pct}%** — Stores start 50/50, CW rebalances at first planning review")
    else:
        st.caption(f"A: **{store_a_pct}%** B: **{100-store_a_pct}%** — Push 50/50 always")

    # --- Demand profile ---
    st.markdown("### \U0001f4c8 Demand Profile")
    preset_shape = st.selectbox("Demand shape", DEMAND_SHAPES, key="demand_shape")
    bf = BASE_FORECAST
    demand_description = ""

    if "Flat" in preset_shape:
        init_demand = build_demand_curve(preset_shape, weeks, base=bf)
        demand_description = f"Flat {bf}/wk for {weeks} wks"

    elif "ramp" in preset_shape.lower():
        end_dem = st.slider("Target demand (pcs/wk)", min_value=bf, max_value=1000, step=10, key="lr_end")
        ramp_wks = st.slider("Ramp duration (weeks)", min_value=1, max_value=weeks, step=1, key="lr_wks")
        init_demand = build_demand_curve(preset_shape, weeks, base=bf, lr_end=end_dem, lr_wks=ramp_wks)
        demand_description = f"Ramp {bf}→{end_dem} in {ramp_wks}wk"

    elif "drop" in preset_shape.lower():
        drop_dem = st.slider("Floor demand (pcs/wk)", min_value=0, max_value=bf, step=10, key="ld_end")
        drop_wks = st.slider("Drop duration (weeks)", min_value=1, max_value=weeks, step=1, key="ld_wks")
        init_demand = build_demand_curve(preset_shape, weeks, base=bf, ld_end=drop_dem, ld_wks=drop_wks)
        demand_description = f"Drop {bf}→{drop_dem} in {drop_wks}wk"

    else:  # Seasonal
        seas_sub = st.radio("Profile shape", ["Very Steep", "Steep", "~Flat"],
                            key="seas_sub", horizontal=True)
        seas_avg = st.slider("Average weekly demand", min_value=0, max_value=1000, step=10, key="seas_avg")
        init_demand = build_demand_curve(preset_shape, weeks, base=bf,
                                         seas_sub=seas_sub, seas_avg=seas_avg)
        demand_description = f"Seasonal {seas_sub} (avg {seas_avg}/wk, total {seas_avg * weeks})"

    st.caption(f"**{demand_description}**")

    # Editable demand table
    st.caption("✏️ Edit demand per week:")
    demand_df = pd.DataFrame({
        "Week": list(range(1, weeks + 1)),
        "Demand (pcs)": init_demand[1:weeks + 1],
    })
    edited = st.data_editor(
        demand_df,
        column_config={
            "Week": st.column_config.NumberColumn(disabled=True, width="small"),
            "Demand (pcs)": st.column_config.NumberColumn(min_value=0, max_value=9999, step=10, width="medium"),
        },
        hide_index=True, use_container_width=True, height=min(300, weeks * 35 + 40),
        key="demand_editor",
    )
    custom_demand = [0] + [int(row["Demand (pcs)"]) for _, row in edited.iterrows()]

    # --- Capacity ---
    st.markdown("### \U0001f3ed Capacity")
    cap_start = st.number_input("Starting Capacity (pcs/wk)", 10, 1000, 100)
    cap_ramp = st.slider("Ramp-up (% vs starting capacity, linear every week)", 0, 50, 20, 5) / 100

    # --- Economics ---
    st.markdown("### \U0001f4b0 Economics")
    price = st.number_input("Selling Price (€)", 100, 10000, 1000, 100)
    var_cost = st.number_input("Variable Cost / Finished Product (€)", 10, 5000, 200, 10)
    st.markdown(
        f'<div style="color:#8a96a6;font-size:13px;font-style:italic;">'
        f'RM: €{var_cost * VALOR_RAW_MAT:.0f} (50%) | '
        f'Semi: €{var_cost * VALOR_SEMI:.0f} (75%) | '
        f'Finished: €{var_cost:.0f} (100%)</div>',
        unsafe_allow_html=True,
    )
    fixed_pct = st.slider("Fixed Cost (% of sim period fcst rev)", 0, 100, 45) / 100

    # --- Engine options ---
    st.markdown("### ⚙️ Engine Options")
    kickstart = st.toggle(
        "Kickstart factory at W1 (force-process initial RM/Semi)",
        key="kickstart",
        help="If ON and initial RM/Semi > 0, factory becomes active at W1 even before "
             "the first order is placed. Fixes the 'seasonal ~Flat' edge case where "
             "initial stock is well-sized and the planner never orders. Drop scenarios "
             "(no initial RM/Semi) are unaffected.",
    )
    debug_mode = st.toggle(
        "Debug mode (runtime asserts + Debug expander)",
        key="debug_mode",
        help="Enables conservation assertions inside the engine and shows a Debug "
             "expander on the main page with reconciliation diagnostics.",
    )

    # --- Quick scenarios: permanent grid (3 LT × 3 demand) ---
    st.markdown("---")
    st.markdown("### \U0001f3af Quick Scenarios — Permanent")
    st.caption("3 LT × 3 Demand · stock auto-sized to coverage·100 · "
               "Agile 60/20/10/10, Medium 80/20/0/0, Push 100/0/0/0 · A=60%, smart ON")

    h1, h2, h3, h4 = st.columns([1.2, 1, 1, 1])
    with h2: st.markdown("**Flat 100**")
    with h3: st.markdown("**Growth →300**")
    with h4: st.markdown("**Drop →30**")

    a1, a2, a3, a4 = st.columns([1.2, 1, 1, 1])
    with a1: st.markdown("\U0001f7e2 **Agile**\n\n*LT=8, f=1*")
    with a2: st.button("⚡", key="p_af", use_container_width=True, on_click=apply_preset, args=("Agile", "Flat"))
    with a3: st.button("⚡", key="p_ag", use_container_width=True, on_click=apply_preset, args=("Agile", "Growth"))
    with a4: st.button("⚡", key="p_ad", use_container_width=True, on_click=apply_preset, args=("Agile", "Drop"))

    m1, m2, m3, m4 = st.columns([1.2, 1, 1, 1])
    with m1: st.markdown("\U0001f7e1 **Medium**\n\n*LT=16, f=2*")
    with m2: st.button("\U0001f536", key="p_mf", use_container_width=True, on_click=apply_preset, args=("Medium", "Flat"))
    with m3: st.button("\U0001f536", key="p_mg", use_container_width=True, on_click=apply_preset, args=("Medium", "Growth"))
    with m4: st.button("\U0001f536", key="p_md", use_container_width=True, on_click=apply_preset, args=("Medium", "Drop"))

    p1, p2, p3, p4 = st.columns([1.2, 1, 1, 1])
    with p1: st.markdown("\U0001f534 **Push**\n\n*LT=24, f=4*")
    with p2: st.button("\U0001f9f1", key="p_pf", use_container_width=True, on_click=apply_preset, args=("Push", "Flat"))
    with p3: st.button("\U0001f9f1", key="p_pg", use_container_width=True, on_click=apply_preset, args=("Push", "Growth"))
    with p4: st.button("\U0001f9f1", key="p_pd", use_container_width=True, on_click=apply_preset, args=("Push", "Drop"))

    # --- Quick scenarios: seasonal grid (3 LT × 3 seasonal averages, all Steep) ---
    st.markdown("### \U0001f30a Quick Scenarios — Seasonal (Steep)")
    st.caption("3 LT × 3 averages (30 / 100 / 300) · Steep gamma curve · "
               "stock sized by sell-through target: Agile 100% → 2600 pcs, "
               "Medium 85% → 3059 pcs, Push 60% → 4333 pcs · "
               "Distributions: Agile 40/20/10/30, Medium 70/20/10/0, Push 100/0/0/0 · "
               "A=60%, smart ON")

    sh1, sh2, sh3, sh4 = st.columns([1.2, 1, 1, 1])
    with sh2: st.markdown("**Avg 30**")
    with sh3: st.markdown("**Avg 100**")
    with sh4: st.markdown("**Avg 300**")

    sa1, sa2, sa3, sa4 = st.columns([1.2, 1, 1, 1])
    with sa1: st.markdown("\U0001f7e2 **Agile**")
    with sa2: st.button("\U0001f30a", key="ps_a30",  use_container_width=True, on_click=apply_preset,
                        args=("Agile", "Seasonal"), kwargs={"seas_avg": 30,  "seas_sub": "Steep"})
    with sa3: st.button("\U0001f30a", key="ps_a100", use_container_width=True, on_click=apply_preset,
                        args=("Agile", "Seasonal"), kwargs={"seas_avg": 100, "seas_sub": "Steep"})
    with sa4: st.button("\U0001f30a", key="ps_a300", use_container_width=True, on_click=apply_preset,
                        args=("Agile", "Seasonal"), kwargs={"seas_avg": 300, "seas_sub": "Steep"})

    sm1, sm2, sm3, sm4 = st.columns([1.2, 1, 1, 1])
    with sm1: st.markdown("\U0001f7e1 **Medium**")
    with sm2: st.button("\U0001f30a", key="ps_m30",  use_container_width=True, on_click=apply_preset,
                        args=("Medium", "Seasonal"), kwargs={"seas_avg": 30,  "seas_sub": "Steep"})
    with sm3: st.button("\U0001f30a", key="ps_m100", use_container_width=True, on_click=apply_preset,
                        args=("Medium", "Seasonal"), kwargs={"seas_avg": 100, "seas_sub": "Steep"})
    with sm4: st.button("\U0001f30a", key="ps_m300", use_container_width=True, on_click=apply_preset,
                        args=("Medium", "Seasonal"), kwargs={"seas_avg": 300, "seas_sub": "Steep"})

    sp1, sp2, sp3, sp4 = st.columns([1.2, 1, 1, 1])
    with sp1: st.markdown("\U0001f534 **Push**")
    with sp2: st.button("\U0001f30a", key="ps_p30",  use_container_width=True, on_click=apply_preset,
                        args=("Push", "Seasonal"), kwargs={"seas_avg": 30,  "seas_sub": "Steep"})
    with sp3: st.button("\U0001f30a", key="ps_p100", use_container_width=True, on_click=apply_preset,
                        args=("Push", "Seasonal"), kwargs={"seas_avg": 100, "seas_sub": "Steep"})
    with sp4: st.button("\U0001f30a", key="ps_p300", use_container_width=True, on_click=apply_preset,
                        args=("Push", "Seasonal"), kwargs={"seas_avg": 300, "seas_sub": "Steep"})

    st.caption(
        "All presets gate initial WIP behind the first supplier order. "
        "If the planner never orders (well-sized stock), pre-positioned "
        "RM/Semi stays unused — a deliberate teaching point about "
        "over-investing upstream."
    )


# ════════════════════════════════════════════════════════════════
# MAIN PAGE UI
# ════════════════════════════════════════════════════════════════

import altair as alt

# --- Build params dict & run simulation ---
params = {
    'weeks': weeks,
    'init_store': init_store, 'init_cw': init_cw,
    'init_semi': init_semi, 'init_rawmat': init_rawmat,
    'order_freq': order_freq,
    'mat_lt': mat_lt, 'semi_lt': semi_lt, 'fp_lt': fp_lt, 'dist_lt': dist_lt,
    'cap_start': cap_start, 'cap_ramp': cap_ramp,
    'base_forecast': BASE_FORECAST,
    'price': price, 'var_cost': var_cost, 'fixed_pct': fixed_pct,
    'store_a_pct': store_a_pct, 'smart_distrib': smart_distrib,
    'kickstart': kickstart, 'debug': debug_mode,
    'custom_demand': tuple(custom_demand),
}

states = run_simulation(**params)
final_kpis = compute_kpis(states[1:], price, var_cost, fixed_pct, BASE_FORECAST, weeks,
                          init_store, init_cw, init_semi, init_rawmat, debug=debug_mode)

# --- Global styles ---
st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    section[data-testid="stSidebar"] { background: linear-gradient(180deg, #eaeff5, #f0f3f8); }
    h1 { color: #1a2a40 !important; }
    h2, h3, h4 { color: #2c3e56 !important; }
    .kpi-card {
        background: linear-gradient(135deg, #ffffff, #f7f9fc);
        border-radius: 10px; padding: 14px 8px;
        border: 1px solid #dde3ed; text-align: center;
        box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    }
    .kpi-value { font-size: 21px; font-weight: 800; margin-top: 2px; }
    .kpi-label { font-size: 8px; color: #7a8a9e; text-transform: uppercase;
        letter-spacing: 1.2px; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

# --- Header ---
st.markdown("# \U0001f3ed Supply Chain Agility Simulator")
distrib_mode = "Smart" if smart_distrib else "Push 50/50"
kick_tag = " | Kickstart ON" if kickstart else ""
st.markdown(
    f"*LT = **{phys_lt}**wk | Coverage = **{coverage}**wk | "
    f"Demand: **{demand_description}** | A: **{store_a_pct}%** / B: **{100-store_a_pct}%** | "
    f"{distrib_mode}{kick_tag}*"
)

# --- Week navigation ---
# Guard against stale week_num after a preset or sim-length change
if st.session_state.week_num > weeks:
    st.session_state.week_num = weeks
if st.session_state.week_num < 0:
    st.session_state.week_num = 0


def _nav_w0():    st.session_state.week_num = 0
def _nav_minus(): st.session_state.week_num = max(0, st.session_state.week_num - 1)
def _nav_plus():  st.session_state.week_num = min(weeks, st.session_state.week_num + 1)
def _nav_end():   st.session_state.week_num = weeks


b1, b2, b3, b4, info = st.columns([1, 1, 1, 1, 2])
with b1: st.button("⏮ W0",      use_container_width=True, disabled=st.session_state.week_num == 0,      on_click=_nav_w0)
with b2: st.button("◀ −1",      use_container_width=True, disabled=st.session_state.week_num <= 0,      on_click=_nav_minus)
with b3: st.button("+1 ▶",      use_container_width=True, disabled=st.session_state.week_num >= weeks,  on_click=_nav_plus)
with b4: st.button(f"W{weeks} ⏭", use_container_width=True, disabled=st.session_state.week_num >= weeks, on_click=_nav_end)
with info:
    pct = st.session_state.week_num / max(weeks, 1)
    bar_w = int(pct * 100)
    st.markdown(
        f"<div style='padding:8px 0;'>"
        f"<div style='font-size:24px;font-weight:800;color:#1a2a40;text-align:center;'>"
        f"Week {st.session_state.week_num} <span style='font-size:13px;color:#7a8a9e;'>/ {weeks}</span></div>"
        f"<div style='background:#e0e4ea;border-radius:4px;height:6px;margin-top:4px;'>"
        f"<div style='background:#4a90d9;height:6px;border-radius:4px;width:{bar_w}%;'></div></div></div>",
        unsafe_allow_html=True,
    )

week = st.session_state.week_num
state = states[week]
cum = cumulative_kpis(states[1:], week, price, var_cost, fixed_pct, BASE_FORECAST, weeks,
                      init_store, init_cw, init_semi, init_rawmat)


# --- 7 KPI cards (operational, driven by cumulative-up-to-current-week) ---
def _kpi_card(label, value, color="#1a2a40"):
    return f'<div class="kpi-card"><div class="kpi-label">{label}</div><div class="kpi-value" style="color:{color};">{value}</div></div>'


k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
with k1:
    svc = cum['svc_level']
    c = "#c0392b" if svc < 0.6 else ("#d4850a" if svc < 0.85 else "#1a8a4a")
    st.markdown(_kpi_card("Service Level", f"{svc*100:.1f}%", c), unsafe_allow_html=True)
with k2:
    st.markdown(_kpi_card("Cumul. Sales",   f"{round(cum['sales'], -1):,.0f}", "#2c5f8a"), unsafe_allow_html=True)
with k3:
    st.markdown(_kpi_card("Missed Total",   f"{round(cum['missed'], -1):,.0f}", "#c0392b"), unsafe_allow_html=True)
with k4:
    st.markdown(_kpi_card("Missed A",       f"{round(cum['missed_a'], -1):,.0f}", "#c0392b"), unsafe_allow_html=True)
with k5:
    st.markdown(_kpi_card("Missed B",       f"{round(cum['missed_b'], -1):,.0f}", "#7b2d8e"), unsafe_allow_html=True)
with k6:
    sc_clr = "#c0392b" if cum['stockout_wks'] > 0 else "#1a8a4a"
    st.markdown(_kpi_card("Stockout Wks",   f"{cum['stockout_wks']}/{week}", sc_clr), unsafe_allow_html=True)
with k7:
    uf = cum['useful_pct']
    uc = "#1a8a4a" if uf > 80 else ("#d4850a" if uf > 50 else "#c0392b")
    st.markdown(_kpi_card("Useful Prod.",   f"{uf:.0f}%", uc), unsafe_allow_html=True)


# --- SC flow diagram (exactly one st.components.v1.html call) ---
st.markdown("")
_max_stage = max(params['mat_lt'], params['semi_lt'], params['fp_lt'], params['dist_lt'])
_rows_needed = math.ceil(_max_stage / MAX_PER_ROW)
_stage_h  = _rows_needed * 145
_stores_h = 2 * 118 + 10 + 28   # 2 store cards + gap + header; sized so LOST badge doesn't clip
_content_h = max(_stage_h, _stores_h)
_viz_h = 48 + 16 + _content_h + 28 + 32   # info bar + pad + content + phys flow + comment
st.components.v1.html(make_sc_html(state, params), height=_viz_h, scrolling=False)


# --- Charts (collapsed by default) ---
with st.expander("\U0001f4c8 Charts: Demand, Fulfillment, Stocks", expanded=False):
    dem_chart_data = pd.DataFrame({
        "Week": list(range(1, weeks + 1)),
        "Demand": [states[i]["demand"] for i in range(1, weeks + 1)],
        "Sales":  [states[i]["sales"]  for i in range(1, weeks + 1)],
        "Missed": [states[i]["missed"] for i in range(1, weeks + 1)],
    })
    y_max = max(dem_chart_data["Demand"].max(), 1) * 1.15
    y_scale = alt.Scale(domain=[0, y_max])

    bar_data = dem_chart_data.melt("Week", ["Sales", "Missed"], var_name="Type", value_name="Units")
    stacked_bars = alt.Chart(bar_data).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("Week:O", title="Week"),
        y=alt.Y("Units:Q", title="Units/week", scale=y_scale, stack=True),
        color=alt.Color("Type:N",
            scale=alt.Scale(domain=["Sales", "Missed"], range=["#1a8a4a", "#c0392b"]),
            legend=alt.Legend(orient="top", title=None)),
        order=alt.Order("Type:N", sort="descending"),
    )
    demand_line = alt.Chart(dem_chart_data).mark_line(color="#4a90d9", strokeWidth=3, strokeDash=[6, 3]).encode(
        x=alt.X("Week:O"), y=alt.Y("Demand:Q", scale=y_scale))
    demand_dots = alt.Chart(dem_chart_data).mark_circle(color="#4a90d9", size=40).encode(
        x="Week:O", y=alt.Y("Demand:Q", scale=y_scale))
    rule_dc = alt.Chart(pd.DataFrame({"Week": [week]})).mark_rule(
        color="#d4850a", strokeWidth=2, strokeDash=[4, 2]).encode(x="Week:O")

    st.markdown("#### Demand vs Sales vs Missed")
    st.altair_chart((stacked_bars + demand_line + demand_dots + rule_dc).properties(height=300),
                    use_container_width=True)

    ch1, ch2 = st.columns(2)
    with ch1:
        st.markdown("#### Demand vs Fulfillment (per store)")
        rows = []
        for s in states[1:]:
            rows.append({'Week': s['week'], 'Group': 'Fill A', 'Component': 'Sales A', 'Value': s['sales_a']})
            rows.append({'Week': s['week'], 'Group': 'Fill A', 'Component': 'Lost A',  'Value': s['missed_a']})
            rows.append({'Week': s['week'], 'Group': 'Fill B', 'Component': 'Sales B', 'Value': s['sales_b']})
            rows.append({'Week': s['week'], 'Group': 'Fill B', 'Component': 'Lost B',  'Value': s['missed_b']})
        df_bars = pd.DataFrame(rows)
        bars = alt.Chart(df_bars).mark_bar(cornerRadiusTopLeft=2, cornerRadiusTopRight=2).encode(
            x=alt.X('Week:O'), y=alt.Y('Value:Q', title='Units', stack=True),
            color=alt.Color('Component:N',
                scale=alt.Scale(domain=['Sales A', 'Lost A', 'Sales B', 'Lost B'],
                                range=['#2c5f8a', '#c0392b', '#6a3d9a', '#e74c8c']),
                legend=alt.Legend(orient='top', title=None, columns=2)),
            xOffset='Group:N',
        ).properties(height=260)
        rule = alt.Chart(pd.DataFrame({'Week': [week]})).mark_rule(
            color='#d4850a', strokeWidth=2, strokeDash=[4, 2]).encode(x='Week:O')
        st.altair_chart(bars + rule, use_container_width=True)

    with ch2:
        st.markdown("#### Store Stocks & Orders")
        stock_data = pd.DataFrame({
            'Week': [s['week'] for s in states],
            'Store A': [s['store_a'] for s in states],
            'Store B': [s['store_b'] for s in states],
            'Order':   [s['order']   for s in states],
        })
        melted = stock_data.melt('Week', ['Store A', 'Store B'], var_name='Store', value_name='Stock')
        lines = alt.Chart(melted).mark_area(opacity=0.25).encode(
            x=alt.X('Week:O'), y=alt.Y('Stock:Q', title='Units', stack=False),
            color=alt.Color('Store:N',
                scale=alt.Scale(domain=['Store A', 'Store B'], range=['#2c5f8a', '#6a3d9a']),
                legend=alt.Legend(orient='top', title=None)),
        ).properties(height=260)
        order_bars = alt.Chart(stock_data[stock_data['Order'] > 0]).mark_bar(
            color='#1a8a4a', opacity=0.4, cornerRadiusTopLeft=2, cornerRadiusTopRight=2
        ).encode(x='Week:O', y='Order:Q')
        rule2 = alt.Chart(pd.DataFrame({'Week': [week]})).mark_rule(
            color='#d4850a', strokeWidth=2, strokeDash=[4, 2]).encode(x='Week:O')
        st.altair_chart(lines + order_bars + rule2, use_container_width=True)


# --- P&L Summary (collapsed by default) ---
with st.expander("\U0001f4cb P&L Summary (end of simulation)", expanded=False):
    fk = final_kpis
    st.markdown("#### Profit & Loss Statement")
    # Detail text uses ACTUAL units costed (= cost_total / unit_increment)
    units_rm   = fk['cost_mat_total']  / (var_cost * VALOR_RAW_MAT) if var_cost > 0 else 0
    units_semi = fk['cost_semi_total'] / (var_cost * (VALOR_SEMI - VALOR_RAW_MAT)) if var_cost > 0 else 0
    units_fp   = fk['cost_fp_total']   / (var_cost * (VALOR_FINISHED - VALOR_SEMI)) if var_cost > 0 else 0
    pl_data = {
        "Line": [
            "\U0001f4b0 Revenue",
            "",
            "− Initial Stock (pre-invested)",
            "− Purchasing (RM @50%)",
            "− Semi Processing (+25%)",
            "− Finishing (+25%)",
            "= Total Variable Cost",
            "",
            "= Gross Margin",
            "− Fixed Costs",
            "",
            "= **Net Margin**",
            "",
            "\U0001f4e6 Leftover Stock + WIP (asset)",
        ],
        "Amount (€)": [
            f"{fk['revenue']:,.0f}",
            "",
            f"-{fk['init_stock_value']:,.0f}",
            f"-{fk['cost_mat_total']:,.0f}",
            f"-{fk['cost_semi_total']:,.0f}",
            f"-{fk['cost_fp_total']:,.0f}",
            f"-{fk['var_cost']:,.0f}",
            "",
            f"{fk['gm']:,.0f}",
            f"-{fk['fixed']:,.0f}",
            "",
            f"{fk['margin']:,.0f}",
            "",
            f"€{fk['leftover_value']:,.0f}",
        ],
        "Detail": [
            f"{fk['total_sales']:,.0f} pcs × €{price}",
            "",
            "Store/WH @100% + Semi @75% + RM @50% (pre-positioned)",
            f"{units_rm:.0f} pcs × €{var_cost * VALOR_RAW_MAT:.0f} (booked on entry to Material)",
            f"{units_semi:.0f} pcs × €{var_cost * (VALOR_SEMI - VALOR_RAW_MAT):.0f} (booked on entry to Semi)",
            f"{units_fp:.0f} pcs × €{var_cost * (VALOR_FINISHED - VALOR_SEMI):.0f} (booked on entry to FP)",
            "Init stock + production costs",
            "",
            "Revenue − Variable Costs",
            f"{fixed_pct*100:.0f}% of simulation forecast revenue",
            "",
            f"{fk['margin_pct']*100:.1f}% of revenue",
            "",
            f"{fk['end_stock_units'] + fk['end_pipe_units']:.0f} pcs (store + WIP + pipe), valorized by stage",
        ],
    }
    st.table(pd.DataFrame(pl_data).set_index("Line"))

    st.markdown("---")
    st.markdown("#### Production Efficiency")
    u1, u2, u3 = st.columns(3)
    with u1: st.metric("Service Level",            f"{fk['svc_level']*100:.1f}%")
    with u2: st.metric("✅ Sold (Useful)",          f"{fk['useful_units']:,.0f} pcs ({fk['useful_pct']:.0f}%)")
    with u3: st.metric("❌ Remaining WIP + stock",  f"{fk['useless_units']:,.0f} pcs ({fk['useless_pct']:.0f}%)")


# --- Debug expander (only when Debug mode is ON) ---
if debug_mode:
    with st.expander("🧪 Debug — reconciliation diagnostics", expanded=True):
        checks = reconciliation_report(states, final_kpis, params)
        rows = []
        for label, ok, detail in checks:
            rows.append({
                "Check": label,
                "Status": "✓ pass" if ok else "✗ FAIL",
                "Detail": detail,
            })
        df_checks = pd.DataFrame(rows)
        st.dataframe(df_checks, use_container_width=True, hide_index=True)
        if any(not ok for _, ok, _ in checks):
            st.error("One or more reconciliation checks failed — see details above.")
        else:
            st.success("All reconciliation checks passed.")


# --- Week-by-week data table (collapsed by default) ---
with st.expander("\U0001f4ca Detailed Week-by-Week Data", expanded=False):
    table_data = []
    for s in states:
        wk_rev    = s['sales'] * price
        wk_vc     = s.get('cost_mat', 0) + s.get('cost_semi', 0) + s.get('cost_fp', 0)
        wk_margin = wk_rev - wk_vc
        table_data.append({
            'Week':     s['week'],
            'Demand':   s['demand'],   'Dem A':  s['demand_a'], 'Dem B':  s['demand_b'],
            'Sales':    s['sales'],    'Sales A': s['sales_a'], 'Sales B': s['sales_b'],
            'Missed':   s['missed'],   'Miss A': s['missed_a'], 'Miss B': s['missed_b'],
            'Stk A':    s['store_a'],  'Stk B':  s['store_b'],
            'Alloc A':  s['alloc_a'],  'Alloc B': s['alloc_b'],
            'CW Wait':  s.get('cw_stock', 0),   'CW Pipe':  s.get('cw_shipped', 0),
            'FP Pipe':  round(sum(s.get('fp_pipe', [])), 1),
            'Semi Wait': s.get('semi_stock', 0), 'Semi Pipe': round(sum(s.get('semi_pipe', [])), 1),
            'RM Wait':  s.get('raw_mat_stock', 0), 'Mat Pipe': round(sum(s.get('mat_pipe', [])), 1),
            'WIP':      s.get('wip_total', 0),
            'Order':    s['order'],    'Pending': s['pending'],
            'Sup Cap':  s.get('supplier_cap', 0),
            'Revenue':  round(wk_rev),
            'Cost RM':   round(s.get('cost_mat', 0)),
            'Cost Semi': round(s.get('cost_semi', 0)),
            'Cost FP':   round(s.get('cost_fp', 0)),
            'Tot VC':   round(wk_vc),
            'Margin':   round(wk_margin),
        })
    st.dataframe(pd.DataFrame(table_data), use_container_width=True, height=500)
    st.caption("**CW Wait** = stock sitting in CW buffer. **CW Pipe** = units shipped from CW toward stores this week. "
               "**Costs** are booked when units ENTER each stage (RM 50%, Semi +25%, FP +25%).")


# --- Save scenario + comparison ---
with st.expander("\U0001f4be Save Scenario for Comparison", expanded=False):
    now_str = datetime.now().strftime("%H:%M:%S")
    default_name = f"LT{phys_lt}_f{order_freq}_{demand_description}_{now_str}"
    scenario_name = st.text_input("Scenario Name", default_name)
    if st.button("Save Current Scenario"):
        st.session_state.setdefault('saved_scenarios', {})
        st.session_state.saved_scenarios[scenario_name] = {
            'params':      params.copy(),
            'kpis':        final_kpis.copy(),
            'demand_desc': demand_description,
        }
        st.success(f"Saved '{scenario_name}'!")

    if st.session_state.get('saved_scenarios'):
        st.markdown("### Comparison")
        saved_list = list(st.session_state.saved_scenarios.items())
        first_margin = saved_list[0][1]['kpis']['margin']
        comp = []
        for i, (n, d) in enumerate(saved_list):
            k = d['kpis']; p = d['params']
            delta = k['margin'] - first_margin
            delta_str = f"€{delta:+,.0f}" if i > 0 else "Baseline"
            comp.append({
                'Scenario': n,
                'Demand':   d.get('demand_desc', ''),
                'Svc%':     f"{k['svc_level']*100:.1f}%",
                'Sales':    f"{round(k['total_sales'], -1):,.0f}",
                'Missed':   f"{round(k['total_missed'], -1):,.0f}",
                'Revenue':  f"€{k['revenue']:,.0f}",
                'Margin':   f"€{k['margin']:,.0f}",
                'Δ vs Base': delta_str,
                'Useful%':  f"{k['useful_pct']:.0f}%",
                'Stock':    p['init_store'] + p['init_cw'] + p['init_semi'] + p['init_rawmat'],
                'Freq':     f"{p['order_freq']}wk",
                'Tot LT':   p['mat_lt'] + p['semi_lt'] + p['fp_lt'] + p['dist_lt'],
            })
        st.dataframe(pd.DataFrame(comp), use_container_width=True)
        if st.button("Clear All"):
            st.session_state.saved_scenarios = {}
            st.rerun()
