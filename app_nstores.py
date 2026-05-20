"""
Supply Chain Agility Simulator — N-stores version.

Differences from v3 (app.py):
  • N stores (2, 10, 50, 200) instead of fixed 2
  • Per-store demand is stochastic (Poisson with curve / N as mean per store)
  • Smart distribution generalised to N stores (water-filling on cover)
  • Aggregate "store cluster" card + per-store distribution histograms
  • Same 4-stage chain, per-stage push policy, execution lag, cost accounting

Engine reuses the v3 invariants and reconciliation tests, generalised to N stores.

Single-file constraint preserved. Reuses the same dependencies as app.py
(streamlit, pandas, numpy, altair).
"""

import io
import streamlit as st
import math
import numpy as np
import pandas as pd
import altair as alt
from datetime import datetime
from math import gamma as gamma_fn, exp as math_exp

st.set_page_config(layout="wide", page_title="SC Simulator — N stores", page_icon="🏭")


# ════════════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════════════

VALOR_RAW_MAT  = 0.50
VALOR_SEMI     = 0.75
VALOR_FINISHED = 1.00
BASE_FORECAST  = 100   # AGGREGATE forecast per week

DEMAND_SHAPES = ["📊 Linear (start 100 → final value)", "🌊 Seasonal (curve profile)"]

LT_PROFILES = {
    "Agile":  {"mat_lt": 4,  "semi_lt": 2, "fp_lt": 1, "dist_lt": 1, "order_freq": 1},
    "Medium": {"mat_lt": 8,  "semi_lt": 4, "fp_lt": 2, "dist_lt": 2, "order_freq": 2},
    "Push":   {"mat_lt": 12, "semi_lt": 6, "fp_lt": 3, "dist_lt": 3, "order_freq": 4},
}
STOCK_DIST = {
    "Agile":  {"store_pct": 60, "wh_pct": 20, "semi_pct": 10},
    "Medium": {"store_pct": 80, "wh_pct": 20, "semi_pct": 0},
    "Push":   {"store_pct": 100, "wh_pct": 0, "semi_pct": 0},
}

SEASONAL_PARAMS = {
    "Very Steep": (3.0 / 26.0, 2.5),
    "Steep":      (6.0 / 26.0, 3.0),
    "~Flat":      (6.0 / 26.0, 1.8),
}

_DEFAULTS = {
    "n_stores": 50, "rng_seed": 42,
    "mat_lt": 4, "semi_lt": 2, "fp_lt": 1, "dist_lt": 1, "order_freq": 1,
    "total_stock": 900, "store_pct": 60, "wh_pct": 20, "semi_pct": 10,
    "smart_distrib": True,
    "demand_shape": DEMAND_SHAPES[0],
    "lin_end": 100, "lin_wks": 1,
    "seas_sub": "Steep", "seas_avg": 100,
}
for _k, _v in _DEFAULTS.items():
    st.session_state.setdefault(_k, _v)


# ════════════════════════════════════════════════════════════════
# MATH HELPERS
# ════════════════════════════════════════════════════════════════

def gamma_pdf(x, k, theta):
    if x <= 0: return 0.0
    return (x ** (k - 1)) * math_exp(-x / theta) / ((theta ** k) * gamma_fn(k))


def seasonal_curve_float(weeks, sub_shape, avg):
    ratio, k = SEASONAL_PARAMS.get(sub_shape, SEASONAL_PARAMS["Steep"])
    theta = (ratio * weeks) / max(k - 1, 0.1)
    pdf_vals = [gamma_pdf(w, k, theta) for w in range(1, weeks + 1)]
    pdf_sum = sum(pdf_vals) or 1.0
    total = avg * weeks
    return [max(0.0, v * total / pdf_sum) for v in pdf_vals]


def build_demand_curve(shape, weeks, *, base=BASE_FORECAST, lin_end=100, lin_wks=1,
                       seas_sub="Steep", seas_avg=100):
    """Return per-week AGGREGATE demand (length weeks, deterministic mean)."""
    out = []
    if "Seasonal" in shape:
        out = [int(round(v)) for v in seasonal_curve_float(weeks, seas_sub, seas_avg)]
    else:
        for w in range(1, weeks + 1):
            if lin_wks > 0 and w <= lin_wks:
                val = base + (lin_end - base) * w / lin_wks
            else:
                val = lin_end
            out.append(max(0, int(round(val))))
    return out


def split_demand_poisson(aggregate_demand, n_stores, rng):
    """
    Split a single week's aggregate demand into N per-store realisations.
    Uses Poisson(λ = aggregate / N) for each store independently, so the
    aggregate is APPROXIMATELY (but not exactly) the requested total —
    individual stores see lumpy 0/1/2 demand even when N is large.
    """
    if n_stores <= 0 or aggregate_demand <= 0:
        return np.zeros(n_stores, dtype=int)
    lam = aggregate_demand / n_stores
    return rng.poisson(lam, size=n_stores)


# ════════════════════════════════════════════════════════════════
# SMART DISTRIBUTION TO N STORES (water-filling)
# ════════════════════════════════════════════════════════════════

def smart_alloc_n(ship_out, stores, dist_pipes, forecast_per_store):
    """
    Allocate `ship_out` units across N stores to equalise weeks-of-cover.
    Water-fill algorithm: bring the lowest-cover stores up to the next-
    lowest level, repeat until ship_out exhausted or all covers equal,
    then distribute residue proportionally to per-store forecast.
    """
    n = len(stores)
    allocs = np.zeros(n, dtype=float)
    if ship_out <= 0 or n == 0:
        return allocs.astype(int)

    eps = 1e-6
    forecasts = np.array([max(f, eps) for f in forecast_per_store])
    ip = np.array([stores[i] + sum(dist_pipes[i]) for i in range(n)], dtype=float)
    cov = ip / forecasts                                # weeks of cover each store has now
    order = np.argsort(cov)
    cov_sorted = cov[order]
    fcst_sorted = forecasts[order]

    remaining = float(ship_out)
    for k in range(n - 1):
        cov_next = cov_sorted[k + 1]
        # Cost in units to raise the first (k+1) stores from cov_sorted[k]
        # up to cov_next:
        delta = cov_next - cov_sorted[k]
        if delta <= 0:
            continue
        cost = delta * fcst_sorted[:k + 1].sum()
        if cost <= remaining + eps:
            # Easily affordable — raise all to cov_next and continue
            for j in range(k + 1):
                allocs[order[j]] += delta * fcst_sorted[j]
            remaining -= cost
            cov_sorted[:k + 1] = cov_next
        else:
            # Distribute remaining proportionally and stop
            for j in range(k + 1):
                allocs[order[j]] += remaining * fcst_sorted[j] / fcst_sorted[:k + 1].sum()
            remaining = 0
            break

    # If we exhausted the loop with remaining still > 0, all stores are at
    # the highest current cover — distribute the residue by demand share.
    if remaining > eps:
        for j in range(n):
            allocs[order[j]] += remaining * fcst_sorted[j] / forecasts.sum()

    # Round to integers and force exact sum
    int_allocs = np.round(allocs).astype(int)
    diff = int(ship_out) - int(int_allocs.sum())
    if diff != 0:
        # Give the residue to the largest-forecast store
        biggest = int(np.argmax(forecasts))
        int_allocs[biggest] += diff
    return int_allocs


# ════════════════════════════════════════════════════════════════
# SIMULATION ENGINE — N stores
# ════════════════════════════════════════════════════════════════

@st.cache_data
def run_simulation_n(weeks, n_stores, init_per_store_total, init_cw, init_semi, init_rawmat,
                     order_freq, mat_lt, semi_lt, fp_lt, dist_lt,
                     cap_start, cap_ramp, base_forecast,
                     price, var_cost, fixed_pct, smart_distrib,
                     safety_z=1.65,
                     rng_seed=42,
                     aggregate_demand_mean=None,    # tuple, length weeks (per-week MEAN aggregate)
                     planner_curve=None):
    """
    Run the weekly simulation with N stores and Poisson per-store demand.
    Returns (states, store_history, kpis_dict) where store_history[w] is the
    per-store stock array at end of week w.
    """
    rng = np.random.default_rng(rng_seed)
    phys_lt   = mat_lt + semi_lt + fp_lt + dist_lt
    coverage  = phys_lt + order_freq
    cov_sup   = coverage
    cov_semi  = semi_lt + fp_lt + dist_lt + order_freq
    cov_fp    = fp_lt + dist_lt + order_freq
    cov_ship  = dist_lt + order_freq

    # Initial per-store stock: split init_per_store_total equally
    init_each = int(round(init_per_store_total / max(n_stores, 1)))
    stores = np.full(n_stores, init_each, dtype=float)
    # Distribute rounding remainder to first few stores
    delta = init_per_store_total - int(stores.sum())
    if delta != 0:
        adj = np.zeros(n_stores, dtype=int)
        adj[:abs(delta)] = 1 if delta > 0 else -1
        stores += adj

    # Per-store distribution pipes
    dist_pipes = [[0.0] * max(1, dist_lt) for _ in range(n_stores)]
    mat_pipe   = [0.0] * max(1, mat_lt)
    semi_pipe  = [0.0] * max(1, semi_lt)
    fp_pipe    = [0.0] * max(1, fp_lt)

    raw_mat = float(init_rawmat); semi = float(init_semi); cw = float(init_cw)

    pb = 0.0; semi_backlog = 0.0; fp_backlog = 0.0; ship_backlog = 0.0
    pn = sn = fn = 0
    supplier_active_from = semi_active_from = fp_active_from = None
    co = 0.0; cas = 0.0

    order_weeks = (list(range(order_freq, weeks + 1, order_freq))
                   if order_freq > 1 else list(range(1, weeks + 1)))
    ff = float(base_forecast)

    # Seasonal planner state
    seasonal_mode = planner_curve is not None
    planner_factor = None
    if seasonal_mode:
        pc = [0.0] * (weeks + coverage + 2)
        for i in range(min(len(planner_curve), weeks + 1)):
            pc[i] = float(planner_curve[i])
        planner_curve_internal = pc
    else:
        planner_curve_internal = None

    def _lookahead_sum(curve, w_from, w_to):
        s = 0.0
        for i in range(w_from, w_to + 1):
            if 0 <= i < len(curve):
                s += curve[i]
        return s * (planner_factor or 1.0)

    # W0 state
    s0 = {
        'week': 0, 'demand': 0, 'sales': 0, 'missed': 0,
        'stores_sum': float(stores.sum()), 'stores_min': float(stores.min()),
        'stores_max': float(stores.max()), 'stores_mean': float(stores.mean()),
        'stores_std': float(stores.std()),
        'raw_mat_stock': raw_mat, 'semi_stock': semi, 'cw_stock': cw,
        'mat_pipe': list(mat_pipe), 'semi_pipe': list(semi_pipe),
        'fp_pipe': list(fp_pipe),
        'order': 0, 'order_semi': 0, 'order_fp': 0, 'order_ship': 0,
        'pending': 0, 'backlog': 0,
        'semi_backlog': 0, 'fp_backlog': 0, 'ship_backlog': 0,
        'supplier_shipped': 0, 'supplier_cap': cap_start,
        'semi_input': 0, 'semi_cap': cap_start,
        'fp_input': 0, 'fp_cap': cap_start,
        'cw_shipped': 0,
        'cost_mat': 0.0, 'cost_semi': 0.0, 'cost_fp': 0.0,
        'planner_factor': None, 'wip_total': 0,
    }
    states = [s0]
    store_history = [stores.copy()]
    sales_per_store_cum = np.zeros(n_stores, dtype=float)
    missed_per_store_cum = np.zeros(n_stores, dtype=float)

    for w in range(1, weeks + 1):
        s = {'week': w}

        # Aggregate mean demand for this week
        agg_mean = (aggregate_demand_mean[w - 1]
                    if aggregate_demand_mean and w - 1 < len(aggregate_demand_mean)
                    else base_forecast)

        # 1. Per-store stochastic demand draw
        per_store_dem = split_demand_poisson(agg_mean, n_stores, rng)
        dem_total = int(per_store_dem.sum())
        s['demand'] = dem_total

        # 2. Arrivals — clear pipe fronts
        m_arr  = mat_pipe[0];  mat_pipe[0]  = 0.0
        sm_arr = semi_pipe[0]; semi_pipe[0] = 0.0
        fp_arr = fp_pipe[0];   fp_pipe[0]   = 0.0
        store_arrivals = np.zeros(n_stores, dtype=float)
        for i in range(n_stores):
            store_arrivals[i] = dist_pipes[i][0]
            dist_pipes[i][0] = 0.0

        # 3. Sales per store
        available = stores + store_arrivals
        sales_per_store = np.minimum(per_store_dem, available)
        missed_per_store = per_store_dem - sales_per_store
        stores = available - sales_per_store
        sales = int(sales_per_store.sum())
        missed = int(missed_per_store.sum())
        sales_per_store_cum += sales_per_store
        missed_per_store_cum += missed_per_store
        cas += float(store_arrivals.sum())

        # 4. Buffer updates from arrivals
        raw_mat += m_arr; semi += sm_arr; cw += fp_arr

        # SNAPSHOT for execution-lag
        pb_avail = pb
        semi_bl_avail = semi_backlog
        fp_bl_avail = fp_backlog
        ship_bl_avail = ship_backlog

        # 5. Planner review (review weeks only)
        od_sup = od_semi = od_fp = od_ship = 0
        if w in order_weeks:
            # Update forecast
            if seasonal_mode and planner_factor is None:
                actual_cum = sum((aggregate_demand_mean[i - 1] if aggregate_demand_mean else base_forecast)
                                 for i in range(1, w + 1))
                expected_cum = sum(planner_curve_internal[i] for i in range(1, w + 1))
                if expected_cum > 0.01:
                    raw_f = actual_cum / expected_cum
                    snapped_int = round(raw_f)
                    if abs(raw_f - snapped_int) < 0.05 and snapped_int > 0:
                        planner_factor = float(snapped_int)
                    else:
                        snapped_1dec = round(raw_f * 10) / 10
                        if abs(raw_f - snapped_1dec) < 0.02:
                            planner_factor = snapped_1dec
                        else:
                            planner_factor = raw_f
                else:
                    planner_factor = 1.0
            ff = float(agg_mean)

            store_sum = float(stores.sum())
            dist_sum = sum(sum(dp) for dp in dist_pipes)
            existing_sup = (store_sum + sum(mat_pipe) + raw_mat + sum(semi_pipe) + semi
                            + sum(fp_pipe) + cw + dist_sum + pb)
            existing_semi = (store_sum + sum(semi_pipe) + semi + sum(fp_pipe) + cw
                             + dist_sum + semi_backlog)
            existing_fp = (store_sum + sum(fp_pipe) + cw + dist_sum + fp_backlog)
            existing_ship = (store_sum + dist_sum + ship_backlog)

            if seasonal_mode:
                tgt_sup  = _lookahead_sum(planner_curve_internal, w + 1, w + cov_sup)
                tgt_semi = _lookahead_sum(planner_curve_internal, w + 1, w + cov_semi)
                tgt_fp   = _lookahead_sum(planner_curve_internal, w + 1, w + cov_fp)
                tgt_ship = _lookahead_sum(planner_curve_internal, w + 1, w + cov_ship)
            else:
                tgt_sup  = ff * cov_sup
                tgt_semi = ff * cov_semi
                tgt_fp   = ff * cov_fp
                tgt_ship = ff * cov_ship

            # Per-store safety stock: the ship-stage target picks up an
            # extra `z × sqrt(N × forecast × cov_ship)` buffer that accounts
            # for store-level Poisson variance. With N=200 and λ=0.5/wk, this
            # raises the ship target by ~330 units for z=1.65 (95% per-store
            # service). The supplier/semi/fp targets pick up the same buffer
            # so it propagates up the chain.
            safety = safety_z * math.sqrt(max(n_stores * ff * cov_ship, 0.0)) if safety_z > 0 else 0.0
            tgt_sup  += safety
            tgt_semi += safety
            tgt_fp   += safety
            tgt_ship += safety

            od_sup  = math.ceil(max(0, tgt_sup  - existing_sup))
            od_semi = math.ceil(max(0, tgt_semi - existing_semi))
            od_fp   = math.ceil(max(0, tgt_fp   - existing_fp))
            od_ship = math.ceil(max(0, tgt_ship - existing_ship))

            co += od_sup
            pb            += od_sup
            semi_backlog  += od_semi
            fp_backlog    += od_fp
            ship_backlog  += od_ship

        s['order']      = round(od_sup, 0)
        s['order_semi'] = round(od_semi, 0)
        s['order_fp']   = round(od_fp, 0)
        s['order_ship'] = round(od_ship, 0)
        s['planner_factor'] = planner_factor

        # 6. Supplier ship (uses snapshot)
        pc_cap = min(cap_start * (1 + pn * cap_ramp), cap_start * 10)
        if pb_avail > 0.01:
            shipped = math.ceil(min(pb_avail, pc_cap))
            pb -= shipped
        else:
            shipped = 0.0
        if shipped > 0 and supplier_active_from is None:
            supplier_active_from = w
        s['supplier_shipped'] = round(shipped, 1)
        s['supplier_cap']     = round(pc_cap, 0)

        # 7. Semi processing
        sc_cap = min(cap_start * (1 + sn * cap_ramp), cap_start * 10)
        if raw_mat > 0.01 and semi_bl_avail > 0.01:
            si = math.ceil(min(raw_mat, sc_cap, semi_bl_avail))
            raw_mat -= si
            semi_backlog -= si
        else:
            si = 0.0
        if si > 0 and semi_active_from is None:
            semi_active_from = w
        s['semi_input'] = round(si, 1)
        s['semi_cap']   = round(sc_cap, 0)
        s['raw_mat_stock'] = round(raw_mat, 1)

        # 8. FP processing
        fp_cap = min(cap_start * (1 + fn * cap_ramp), cap_start * 10)
        if semi > 0.01 and fp_bl_avail > 0.01:
            fi = math.ceil(min(semi, fp_cap, fp_bl_avail))
            semi -= fi
            fp_backlog -= fi
        else:
            fi = 0.0
        if fi > 0 and fp_active_from is None:
            fp_active_from = w
        s['fp_input']   = round(fi, 1)
        s['fp_cap']     = round(fp_cap, 0)
        s['semi_stock'] = round(semi, 1)

        # 9. CW push — water-fill to N stores
        if cw > 0.01 and ship_bl_avail > 0.01:
            ship_out = math.ceil(min(cw, ship_bl_avail))
            cw -= ship_out
            ship_backlog -= ship_out
        else:
            ship_out = 0.0
        s['cw_shipped'] = round(ship_out, 1)
        s['cw_stock']   = round(cw, 1)

        # Allocate ship_out among stores
        if ship_out > 0:
            if smart_distrib:
                # per-store forecast = agg_mean / N (uniform weights for v1)
                forecast_per_store = np.full(n_stores, max(agg_mean / n_stores, 0.01))
                allocs = smart_alloc_n(ship_out, stores, dist_pipes, forecast_per_store)
            else:
                # Equal split
                each = int(ship_out // n_stores)
                allocs = np.full(n_stores, each, dtype=int)
                allocs[0] += int(ship_out - each * n_stores)  # remainder
        else:
            allocs = np.zeros(n_stores, dtype=int)

        # 10. Pipe update
        mat_pipe  = mat_pipe[1:]  + [shipped]
        semi_pipe = semi_pipe[1:] + [si]
        fp_pipe   = fp_pipe[1:]   + [fi]
        for i in range(n_stores):
            dist_pipes[i] = dist_pipes[i][1:] + [float(allocs[i])]

        # Costs (entering-stage convention)
        s['cost_mat']  = round(shipped * var_cost * VALOR_RAW_MAT, 1)
        s['cost_semi'] = round(si      * var_cost * (VALOR_SEMI - VALOR_RAW_MAT), 1)
        s['cost_fp']   = round(fi      * var_cost * (VALOR_FINISHED - VALOR_SEMI), 1)

        # Ramp counters advance at end of first-push week
        if supplier_active_from is not None and w >= supplier_active_from: pn += 1
        if semi_active_from     is not None and w >= semi_active_from:     sn += 1
        if fp_active_from       is not None and w >= fp_active_from:       fn += 1

        # State summary
        dist_total = sum(sum(dp) for dp in dist_pipes)
        total_wip = (sum(mat_pipe) + sum(semi_pipe) + sum(fp_pipe) + dist_total
                     + raw_mat + semi + cw + pb)
        s['sales']  = sales
        s['missed'] = missed
        s['stores_sum']  = float(stores.sum())
        s['stores_min']  = float(stores.min())
        s['stores_max']  = float(stores.max())
        s['stores_mean'] = float(stores.mean())
        s['stores_std']  = float(stores.std())
        s['mat_pipe']    = [round(x, 1) for x in mat_pipe]
        s['semi_pipe']   = [round(x, 1) for x in semi_pipe]
        s['fp_pipe']     = [round(x, 1) for x in fp_pipe]
        s['wip_total']   = round(total_wip, 1)
        s['pending']     = round(co - cas, 0)
        s['backlog']     = round(pb, 0)
        s['semi_backlog'] = round(semi_backlog, 1)
        s['fp_backlog']  = round(fp_backlog, 1)
        s['ship_backlog'] = round(ship_backlog, 1)

        states.append(s)
        store_history.append(stores.copy())

    return states, store_history, sales_per_store_cum, missed_per_store_cum


# ════════════════════════════════════════════════════════════════
# KPIs
# ════════════════════════════════════════════════════════════════

def compute_kpis_n(states, price, var_cost, fixed_pct, base_forecast, weeks,
                   init_store_total, init_cw, init_semi, init_rawmat):
    ts = sum(s['sales'] for s in states)
    tm = sum(s['missed'] for s in states)
    td = sum(s['demand'] for s in states)

    init_stock_value = (init_store_total * var_cost * VALOR_FINISHED
                       + init_cw         * var_cost * VALOR_FINISHED
                       + init_semi       * var_cost * VALOR_SEMI
                       + init_rawmat     * var_cost * VALOR_RAW_MAT)
    cost_mat_total  = sum(s.get('cost_mat', 0)  for s in states)
    cost_semi_total = sum(s.get('cost_semi', 0) for s in states)
    cost_fp_total   = sum(s.get('cost_fp', 0)   for s in states)
    prod_cost = cost_mat_total + cost_semi_total + cost_fp_total

    vc = init_stock_value + prod_cost
    rev = ts * price
    gm = rev - vc
    fx = base_forecast * weeks * price * fixed_pct
    mg = gm - fx

    last = states[-1] if states else {}
    end_stock = (last.get('stores_sum', 0) + last.get('cw_stock', 0)
                + last.get('semi_stock', 0) + last.get('raw_mat_stock', 0))
    end_pipe = (sum(last.get('mat_pipe', [0])) + sum(last.get('semi_pipe', [0]))
               + sum(last.get('fp_pipe', [0])))

    return {
        'total_demand': td, 'total_sales': ts, 'total_missed': tm,
        'svc_level': ts / td if td > 0 else 0,
        'stockout_weeks': sum(1 for s in states if s['missed'] > 0.5),
        'revenue': rev, 'var_cost': vc, 'gm': gm, 'fixed': fx,
        'margin': mg, 'margin_pct': mg / rev if rev > 0 else 0,
        'init_stock_value': init_stock_value,
        'cost_mat_total': cost_mat_total,
        'cost_semi_total': cost_semi_total,
        'cost_fp_total': cost_fp_total,
        'end_stock_units': end_stock,
        'end_pipe_units':  end_pipe,
        'leftover_units': end_stock + end_pipe,
    }


# ════════════════════════════════════════════════════════════════
# BATCH COMPARISON
# ════════════════════════════════════════════════════════════════

BATCH_INPUT_COLS = [
    "Scenario\nLabel",
    "Sim\nWeeks",
    "N\nStores",
    "Safety\nz",
    "Seed",
    "Material\nLT (wk)",
    "Semi\nLT (wk)",
    "Finishing\nLT (wk)",
    "Distribution\nLT (wk)",
    "Order\nFreq (wk)",
    "Total Init\nStock (pcs)",
    "Init Store\n%",
    "Init WH\n%",
    "Init Semi\n%",
    "Smart\nDistrib",
    "Demand\nShape",
    "Linear End\n(pcs/wk)",
    "Linear\nTransition (wk)",
    "Seasonal\nSub-shape",
    "Seasonal Avg\n(pcs/wk)",
    "Capacity\nStart (pcs/wk)",
    "Capacity Ramp\n(%/wk)",
    "Price\n(€/pc)",
    "Var Cost\n(€/pc)",
    "Fixed Cost\n% of fcst rev",
]

BATCH_OUTPUT_COLS = [
    "Revenue\n(€)",
    "Cumul Sales\n(pcs)",
    "Service\nLevel %",
    "Missed Total\n(pcs)",
    "Stockout\nWeeks",
    "Stores w/\nStockouts",
    "Avg Sales\n/ Store",
    "Avg Missed\n/ Store",
    "End Stock\nStd (pcs)",
    "Init Stock\nValue (€)",
    "Total VC\n(€)",
    "Fixed Cost\n(€)",
    "Net Margin\n(€)",
    "Margin\n% of rev",
    "Leftover\nUnits (pcs)",
    "End Stock\nTotal (pcs)",
]


def _batch_default_rows():
    """9 presets: 3 profiles × 3 N values, flat 100 pcs/wk aggregate, z=1.65."""
    presets = []
    for prof in ["Agile", "Medium", "Push"]:
        for n in [10, 50, 200]:
            presets.append((f"{prof:6s} · N={n:>3d}", prof, n))
    rows = []
    for label, lt_name, n in presets:
        lt = LT_PROFILES[lt_name]
        dist = STOCK_DIST[lt_name]
        cov = lt["mat_lt"] + lt["semi_lt"] + lt["fp_lt"] + lt["dist_lt"] + lt["order_freq"]
        stock = min(100 * cov, 10000)
        rows.append({
            BATCH_INPUT_COLS[0]:  label,
            BATCH_INPUT_COLS[1]:  26,
            BATCH_INPUT_COLS[2]:  n,
            BATCH_INPUT_COLS[3]:  1.65,
            BATCH_INPUT_COLS[4]:  42,
            BATCH_INPUT_COLS[5]:  lt["mat_lt"],
            BATCH_INPUT_COLS[6]:  lt["semi_lt"],
            BATCH_INPUT_COLS[7]:  lt["fp_lt"],
            BATCH_INPUT_COLS[8]:  lt["dist_lt"],
            BATCH_INPUT_COLS[9]:  lt["order_freq"],
            BATCH_INPUT_COLS[10]: stock,
            BATCH_INPUT_COLS[11]: dist["store_pct"],
            BATCH_INPUT_COLS[12]: dist["wh_pct"],
            BATCH_INPUT_COLS[13]: dist["semi_pct"],
            BATCH_INPUT_COLS[14]: True,
            BATCH_INPUT_COLS[15]: "Linear",
            BATCH_INPUT_COLS[16]: 100,
            BATCH_INPUT_COLS[17]: 1,
            BATCH_INPUT_COLS[18]: "Steep",
            BATCH_INPUT_COLS[19]: 100,
            BATCH_INPUT_COLS[20]: 200,
            BATCH_INPUT_COLS[21]: 10,
            BATCH_INPUT_COLS[22]: 10,
            BATCH_INPUT_COLS[23]: 3,
            BATCH_INPUT_COLS[24]: 20,
        })
    return pd.DataFrame(rows)


def _run_one_scenario_n(row):
    """Run one batch row through the N-stores engine and return KPI dict."""
    w           = int(row[BATCH_INPUT_COLS[1]])
    n_stores    = int(row[BATCH_INPUT_COLS[2]])
    safety_z    = float(row[BATCH_INPUT_COLS[3]])
    seed        = int(row[BATCH_INPUT_COLS[4]])
    mat_lt      = int(row[BATCH_INPUT_COLS[5]])
    semi_lt     = int(row[BATCH_INPUT_COLS[6]])
    fp_lt       = int(row[BATCH_INPUT_COLS[7]])
    dist_lt     = int(row[BATCH_INPUT_COLS[8]])
    freq        = int(row[BATCH_INPUT_COLS[9]])
    total_stock = int(row[BATCH_INPUT_COLS[10]])
    sp          = int(row[BATCH_INPUT_COLS[11]])
    wp          = int(row[BATCH_INPUT_COLS[12]])
    sep         = int(row[BATCH_INPUT_COLS[13]])
    smart       = bool(row[BATCH_INPUT_COLS[14]])
    shape       = str(row[BATCH_INPUT_COLS[15]])
    lin_end     = int(row[BATCH_INPUT_COLS[16]])
    lin_wks     = int(row[BATCH_INPUT_COLS[17]])
    seas_sub    = str(row[BATCH_INPUT_COLS[18]])
    seas_avg    = int(row[BATCH_INPUT_COLS[19]])
    cap_start   = float(row[BATCH_INPUT_COLS[20]])
    cap_ramp    = float(row[BATCH_INPUT_COLS[21]]) / 100.0
    price       = float(row[BATCH_INPUT_COLS[22]])
    var_cost    = float(row[BATCH_INPUT_COLS[23]])
    fixed_pct   = float(row[BATCH_INPUT_COLS[24]]) / 100.0

    init_store  = int(round(total_stock * sp / 100))
    init_cw     = int(round(total_stock * wp / 100))
    init_semi   = int(round(total_stock * sep / 100))
    init_raw    = total_stock - init_store - init_cw - init_semi

    if "Seasonal" in shape:
        shape_full = DEMAND_SHAPES[1]
        cd = build_demand_curve(shape_full, w, base=BASE_FORECAST,
                                seas_sub=seas_sub, seas_avg=seas_avg)
        pc = [0.0] + list(seasonal_curve_float(w, seas_sub, BASE_FORECAST))
    else:
        shape_full = DEMAND_SHAPES[0]
        cd = build_demand_curve(shape_full, w, base=BASE_FORECAST,
                                lin_end=lin_end, lin_wks=lin_wks)
        pc = None

    states, store_history, sales_pc, missed_pc = run_simulation_n(
        weeks=w, n_stores=n_stores,
        init_per_store_total=init_store, init_cw=init_cw,
        init_semi=init_semi, init_rawmat=init_raw,
        order_freq=freq, mat_lt=mat_lt, semi_lt=semi_lt,
        fp_lt=fp_lt, dist_lt=dist_lt,
        cap_start=cap_start, cap_ramp=cap_ramp, base_forecast=BASE_FORECAST,
        price=price, var_cost=var_cost, fixed_pct=fixed_pct,
        smart_distrib=smart, safety_z=safety_z, rng_seed=seed,
        aggregate_demand_mean=tuple(cd),
        planner_curve=tuple(pc) if pc is not None else None,
    )
    k = compute_kpis_n(states, price, var_cost, fixed_pct, BASE_FORECAST, w,
                       init_store, init_cw, init_semi, init_raw)

    end_stock_per_store = store_history[-1]
    return {
        BATCH_OUTPUT_COLS[0]:  round(k["revenue"]),
        BATCH_OUTPUT_COLS[1]:  round(k["total_sales"]),
        BATCH_OUTPUT_COLS[2]:  round(k["svc_level"] * 100, 1),
        BATCH_OUTPUT_COLS[3]:  round(k["total_missed"]),
        BATCH_OUTPUT_COLS[4]:  k["stockout_weeks"],
        BATCH_OUTPUT_COLS[5]:  int((missed_pc > 0).sum()),
        BATCH_OUTPUT_COLS[6]:  round(float(sales_pc.mean()), 1),
        BATCH_OUTPUT_COLS[7]:  round(float(missed_pc.mean()), 2),
        BATCH_OUTPUT_COLS[8]:  round(float(end_stock_per_store.std()), 2),
        BATCH_OUTPUT_COLS[9]:  round(k["init_stock_value"]),
        BATCH_OUTPUT_COLS[10]: round(k["var_cost"]),
        BATCH_OUTPUT_COLS[11]: round(k["fixed"]),
        BATCH_OUTPUT_COLS[12]: round(k["margin"]),
        BATCH_OUTPUT_COLS[13]: round(k["margin_pct"] * 100, 1),
        BATCH_OUTPUT_COLS[14]: round(k["leftover_units"]),
        BATCH_OUTPUT_COLS[15]: round(k["end_stock_units"]),
    }


def render_batch_ui_n():
    st.markdown("# 📊 Batch Scenario Comparison — N stores")
    st.caption("Edit one scenario per row, then click **Run all scenarios**. Each row is "
               "fully reproducible from its **Seed** (so two rows with the same seed and N "
               "see the same demand draws — fair apples-to-apples).")

    if "batch_df" not in st.session_state:
        st.session_state.batch_df = _batch_default_rows()

    NumberCol = st.column_config.NumberColumn
    SelectCol = st.column_config.SelectboxColumn
    CheckCol  = st.column_config.CheckboxColumn
    TextCol   = st.column_config.TextColumn
    col_config = {
        BATCH_INPUT_COLS[0]:  TextCol(width="medium", pinned=True),
        BATCH_INPUT_COLS[1]:  NumberCol(min_value=13, max_value=52, step=1),
        BATCH_INPUT_COLS[2]:  NumberCol(min_value=2, max_value=500, step=1),
        BATCH_INPUT_COLS[3]:  NumberCol(min_value=0.0, max_value=3.0, step=0.05, format="%.2f"),
        BATCH_INPUT_COLS[4]:  NumberCol(min_value=0, max_value=10000, step=1),
        BATCH_INPUT_COLS[5]:  NumberCol(min_value=1, max_value=24, step=1),
        BATCH_INPUT_COLS[6]:  NumberCol(min_value=1, max_value=12, step=1),
        BATCH_INPUT_COLS[7]:  NumberCol(min_value=1, max_value=12, step=1),
        BATCH_INPUT_COLS[8]:  NumberCol(min_value=1, max_value=12, step=1),
        BATCH_INPUT_COLS[9]:  NumberCol(min_value=1, max_value=4, step=1),
        BATCH_INPUT_COLS[10]: NumberCol(min_value=0, max_value=50000, step=50),
        BATCH_INPUT_COLS[11]: NumberCol(min_value=0, max_value=100, step=5),
        BATCH_INPUT_COLS[12]: NumberCol(min_value=0, max_value=100, step=5),
        BATCH_INPUT_COLS[13]: NumberCol(min_value=0, max_value=100, step=5),
        BATCH_INPUT_COLS[14]: CheckCol(),
        BATCH_INPUT_COLS[15]: SelectCol(options=["Linear", "Seasonal"]),
        BATCH_INPUT_COLS[16]: NumberCol(min_value=0, max_value=1000, step=10),
        BATCH_INPUT_COLS[17]: NumberCol(min_value=1, max_value=52, step=1),
        BATCH_INPUT_COLS[18]: SelectCol(options=["Very Steep", "Steep", "~Flat"]),
        BATCH_INPUT_COLS[19]: NumberCol(min_value=0, max_value=1000, step=10),
        BATCH_INPUT_COLS[20]: NumberCol(min_value=10, max_value=1000, step=10),
        BATCH_INPUT_COLS[21]: NumberCol(min_value=0, max_value=50, step=5),
        BATCH_INPUT_COLS[22]: NumberCol(min_value=1, max_value=10000, step=1),
        BATCH_INPUT_COLS[23]: NumberCol(min_value=1, max_value=5000, step=1),
        BATCH_INPUT_COLS[24]: NumberCol(min_value=0, max_value=100, step=5),
    }
    for c in BATCH_OUTPUT_COLS:
        col_config[c] = NumberCol(disabled=True)

    def _apply_editor_state(source_df):
        df = source_df.copy().reset_index(drop=True)
        edits = st.session_state.get("batch_editor", {}) or {}
        for idx_key, changes in (edits.get("edited_rows") or {}).items():
            idx = int(idx_key)
            for col, val in changes.items():
                if 0 <= idx < len(df):
                    df.at[idx, col] = val
        for row in (edits.get("added_rows") or []):
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        for idx in sorted([int(i) for i in (edits.get("deleted_rows") or [])], reverse=True):
            if 0 <= idx < len(df):
                df = df.drop(idx).reset_index(drop=True)
        return df

    def _reset_batch():
        st.session_state.batch_df = _batch_default_rows()
        st.session_state.pop("batch_editor", None)
        st.session_state.pop("batch_results", None)

    def _duplicate_last_row():
        current = _apply_editor_state(st.session_state.batch_df[BATCH_INPUT_COLS])
        if len(current) == 0:
            return
        new_row = current.iloc[-1].copy()
        new_row[BATCH_INPUT_COLS[0]] = str(new_row[BATCH_INPUT_COLS[0]]) + " (copy)"
        st.session_state.batch_df = pd.concat(
            [current, pd.DataFrame([new_row])], ignore_index=True)
        st.session_state.pop("batch_editor", None)

    b1, b2, b3 = st.columns([1, 1, 1])
    b1.button("🔄 Reset to 9-preset baseline", use_container_width=True, on_click=_reset_batch)
    b2.button("➕ Duplicate last row", use_container_width=True, on_click=_duplicate_last_row)
    run_clicked = b3.button("▶️ Run all scenarios", use_container_width=True, type="primary")

    input_only_df = st.session_state.batch_df[BATCH_INPUT_COLS].copy()
    edited = st.data_editor(
        input_only_df,
        column_config={k: v for k, v in col_config.items() if k in BATCH_INPUT_COLS},
        num_rows="dynamic",
        use_container_width=True,
        height=min(600, 40 + len(input_only_df) * 35),
        key="batch_editor",
    )

    if run_clicked:
        results = []
        progress = st.progress(0.0, text="Running scenarios…")
        total = len(edited)
        for i, (_, row) in enumerate(edited.iterrows()):
            try:
                out = _run_one_scenario_n(row)
            except Exception as e:
                out = {c: None for c in BATCH_OUTPUT_COLS}
                out[BATCH_OUTPUT_COLS[0]] = None
                st.warning(f"Row {i+1} failed: {e}")
            results.append(out)
            progress.progress((i + 1) / max(total, 1), text=f"Running scenario {i+1}/{total}…")
        progress.empty()
        results_df = pd.DataFrame(results)
        st.session_state.batch_results = pd.concat(
            [edited.reset_index(drop=True), results_df.reset_index(drop=True)], axis=1)

    if "batch_results" in st.session_state:
        st.markdown("### Results")
        st.dataframe(
            st.session_state.batch_results,
            use_container_width=True,
            height=min(600, 40 + len(st.session_state.batch_results) * 35),
            column_config=col_config,
        )

        xlsx_buf = io.BytesIO()
        with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as xw:
            st.session_state.batch_results.to_excel(xw, index=False, sheet_name="Batch")
        st.download_button(
            "📥 Download results as Excel (inputs + outputs)",
            data=xlsx_buf.getvalue(),
            file_name=f"sc_batch_nstores_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.markdown("---")
    with st.expander("📤 Upload a previously-saved batch Excel file to restore the table"):
        st.caption("Upload the .xlsx file you downloaded earlier. Input columns are "
                   "picked up; output columns are ignored and recomputed when you click Run.")
        uploaded = st.file_uploader("Choose an .xlsx file", type=["xlsx"], key="batch_upload")
        if uploaded is not None and st.button("Load from uploaded file"):
            try:
                loaded = pd.read_excel(uploaded, sheet_name=0)
                defaults = _batch_default_rows().iloc[0]
                for c in BATCH_INPUT_COLS:
                    if c not in loaded.columns:
                        loaded[c] = defaults[c]
                st.session_state.batch_df = loaded[BATCH_INPUT_COLS].copy()
                st.session_state.pop("batch_editor", None)
                st.session_state.pop("batch_results", None)
                st.success(f"Loaded {len(loaded)} scenarios. Click 'Run all scenarios'.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to parse Excel file: {e}")


# ════════════════════════════════════════════════════════════════
# UI — mode switch
# ════════════════════════════════════════════════════════════════

with st.sidebar:
    app_mode = st.radio("App mode", ["Single Scenario", "Batch Comparison"],
                        horizontal=True, key="app_mode",
                        help="Single = full week-by-week simulator with charts. "
                             "Batch = table of scenarios run in parallel, exportable.")

if app_mode == "Batch Comparison":
    render_batch_ui_n()
    st.stop()


st.title("🏭 Supply Chain Agility Simulator — N stores")
st.caption("Parametric version with N stores and stochastic per-store demand (Poisson). "
           "Use this to see how risk pooling and store-level variance change the agility story.")

# ── Sidebar ─────────────────────────────────────────────────────
with st.sidebar:
    st.header("🏪 Network size")
    n_stores = st.select_slider("Number of stores", options=[2, 10, 50, 100, 200, 500],
                                value=st.session_state.get('n_stores', 50), key='n_stores')
    rng_seed = st.number_input("Random seed", min_value=0, max_value=10_000, step=1,
                               value=st.session_state.get('rng_seed', 42), key='rng_seed')

    st.header("⚙️ Supply-chain profile")
    profile = st.radio("Profile", options=["Agile", "Medium", "Push", "Custom"],
                       index=0, horizontal=True, key='profile')
    if profile != "Custom":
        lt_p = LT_PROFILES[profile]
        sd_p = STOCK_DIST[profile]
        st.session_state['mat_lt']     = lt_p['mat_lt']
        st.session_state['semi_lt']    = lt_p['semi_lt']
        st.session_state['fp_lt']      = lt_p['fp_lt']
        st.session_state['dist_lt']    = lt_p['dist_lt']
        st.session_state['order_freq'] = lt_p['order_freq']
        st.session_state['store_pct']  = sd_p['store_pct']
        st.session_state['wh_pct']     = sd_p['wh_pct']
        st.session_state['semi_pct']   = sd_p['semi_pct']

    with st.expander("Lead times & frequency", expanded=(profile == "Custom")):
        c1, c2 = st.columns(2)
        with c1:
            mat_lt  = st.number_input("Material LT",   1, 26, key='mat_lt')
            fp_lt   = st.number_input("Finishing LT",  1, 26, key='fp_lt')
        with c2:
            semi_lt = st.number_input("Semi-Fin LT",   1, 26, key='semi_lt')
            dist_lt = st.number_input("Distribution LT", 1, 26, key='dist_lt')
        order_freq = st.number_input("Order frequency (weeks)", 1, 26, key='order_freq')

    with st.expander("Initial stock distribution", expanded=(profile == "Custom")):
        total_stock = st.number_input("Total initial stock (units)", 0, 100_000,
                                      value=st.session_state.get('total_stock', 900),
                                      step=50, key='total_stock')
        store_pct = st.slider("In stores (%)",     0, 100, key='store_pct')
        wh_pct    = st.slider("In Finishing (%)",  0, 100 - store_pct, key='wh_pct')
        semi_pct  = st.slider("In Semi-Fin (%)",   0, 100 - store_pct - wh_pct, key='semi_pct')
        mat_pct   = 100 - store_pct - wh_pct - semi_pct
        st.caption(f"Material: **{mat_pct}%** (remainder)")

    smart_distrib = st.checkbox("Smart distribution (cover-equalise)", value=True, key='smart_distrib')

    st.header("📈 Demand")
    demand_shape = st.selectbox("Shape", DEMAND_SHAPES, key='demand_shape')
    if "Seasonal" in demand_shape:
        seas_sub = st.selectbox("Curve sharpness", list(SEASONAL_PARAMS.keys()),
                                index=1, key='seas_sub')
        seas_avg = st.number_input("Aggregate average per week", 1, 100_000,
                                   value=st.session_state.get('seas_avg', 100), key='seas_avg')
        weeks = 26
        lin_end = lin_wks = 0
    else:
        weeks = 26
        c1, c2 = st.columns(2)
        with c1:
            lin_end = st.number_input("Final aggregate (per week)", 0, 100_000,
                                      value=st.session_state.get('lin_end', 100), key='lin_end')
        with c2:
            lin_wks = st.number_input("Ramp weeks", 0, weeks,
                                      value=st.session_state.get('lin_wks', 1), key='lin_wks')
        seas_sub = "Steep"; seas_avg = 100

    st.header("🛡️ Safety stock")
    safety_z = st.slider("Service factor z (per-store)", 0.0, 3.0, 1.65, 0.05,
                         help="0 = no safety stock, 1.0 ≈ 84%, 1.65 ≈ 95%, 2.33 ≈ 99% per-store service. "
                              "Adds `z·√(N·λ·cov_ship)` units to all stage targets.")

    st.header("🏭 Capacity & economics")
    cap_start = st.number_input("Capacity start (units/wk)", 1, 100_000, value=200, step=10)
    cap_ramp  = st.slider("Ramp factor (per active wk)", 0.0, 1.0, 0.1, 0.05)
    c1, c2 = st.columns(2)
    with c1:
        price    = st.number_input("Price",    0.0, 1e6, 10.0, 0.5)
        var_cost = st.number_input("Var cost", 0.0, 1e6, 3.0,  0.5)
    with c2:
        fixed_pct = st.slider("Fixed cost %",  0.0, 1.0, 0.2, 0.01)

# ── Build demand curves ────────────────────────────────────────
agg_demand = build_demand_curve(demand_shape, weeks, base=BASE_FORECAST,
                                lin_end=lin_end, lin_wks=lin_wks,
                                seas_sub=seas_sub, seas_avg=seas_avg)
planner_curve = None
if "Seasonal" in demand_shape:
    pc = [0.0] + list(seasonal_curve_float(weeks, seas_sub, seas_avg))
    planner_curve = pc

# Allocate initial stock to stages
init_store_total = int(round(total_stock * store_pct / 100))
init_cw          = int(round(total_stock * wh_pct  / 100))
init_semi        = int(round(total_stock * semi_pct / 100))
init_rawmat      = total_stock - init_store_total - init_cw - init_semi

# ── Run simulation ──────────────────────────────────────────────
with st.spinner(f"Running {n_stores}-store simulation…"):
    states, store_history, sales_pc, missed_pc = run_simulation_n(
        weeks=weeks, n_stores=int(n_stores),
        init_per_store_total=init_store_total, init_cw=init_cw,
        init_semi=init_semi, init_rawmat=init_rawmat,
        order_freq=int(order_freq),
        mat_lt=int(mat_lt), semi_lt=int(semi_lt),
        fp_lt=int(fp_lt), dist_lt=int(dist_lt),
        cap_start=float(cap_start), cap_ramp=float(cap_ramp),
        base_forecast=BASE_FORECAST,
        price=float(price), var_cost=float(var_cost),
        fixed_pct=float(fixed_pct), smart_distrib=bool(smart_distrib),
        safety_z=float(safety_z),
        rng_seed=int(rng_seed),
        aggregate_demand_mean=tuple(agg_demand),
        planner_curve=tuple(planner_curve) if planner_curve else None,
    )
    kpis = compute_kpis_n(states, price, var_cost, fixed_pct, BASE_FORECAST, weeks,
                          init_store_total, init_cw, init_semi, init_rawmat)

# ── Aggregate KPIs ──────────────────────────────────────────────
st.header("📊 Aggregate KPIs")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Service level", f"{kpis['svc_level']:.1%}")
c2.metric("Total missed", f"{kpis['total_missed']:,}")
c3.metric("Leftover stock", f"{kpis['leftover_units']:,.0f}")
c4.metric("Margin", f"{kpis['margin']:,.0f}")
c5.metric("Margin %", f"{kpis['margin_pct']:+.1%}")

# ── Store cluster card ──────────────────────────────────────────
st.header(f"🏪 Store cluster — {n_stores} stores")
final = states[-1]
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("End stock (total)", f"{final['stores_sum']:,.0f}")
c2.metric("End stock (avg)",   f"{final['stores_mean']:.1f}")
c3.metric("End stock (min)",   f"{final['stores_min']:.0f}")
c4.metric("End stock (max)",   f"{final['stores_max']:.0f}")
c5.metric("End stock (std)",   f"{final['stores_std']:.1f}")

# Per-store sales / missed
c1, c2, c3 = st.columns(3)
c1.metric("Sales per store (avg)",  f"{sales_pc.mean():.1f}")
c2.metric("Missed per store (avg)", f"{missed_pc.mean():.1f}")
c3.metric("Stores with stockouts",  f"{int((missed_pc > 0).sum())}/{n_stores}")

# ── Distribution histograms ─────────────────────────────────────
st.subheader("📈 Distribution across stores")
end_stock_per_store = store_history[-1]
dist_df = pd.DataFrame({
    "end_stock":  end_stock_per_store,
    "total_sales": sales_pc,
    "total_missed": missed_pc,
})

c1, c2, c3 = st.columns(3)
with c1:
    st.caption("End-of-horizon stock per store")
    ch = (alt.Chart(dist_df).mark_bar()
          .encode(x=alt.X("end_stock:Q", bin=alt.Bin(maxbins=20), title="Units"),
                  y=alt.Y("count():Q", title="Stores"))
          .properties(height=200))
    st.altair_chart(ch, use_container_width=True)
with c2:
    st.caption("Total sales per store (horizon)")
    ch = (alt.Chart(dist_df).mark_bar(color="#2ca02c")
          .encode(x=alt.X("total_sales:Q", bin=alt.Bin(maxbins=20), title="Units"),
                  y=alt.Y("count():Q", title="Stores"))
          .properties(height=200))
    st.altair_chart(ch, use_container_width=True)
with c3:
    st.caption("Total missed per store (horizon)")
    ch = (alt.Chart(dist_df).mark_bar(color="#d62728")
          .encode(x=alt.X("total_missed:Q", bin=alt.Bin(maxbins=20), title="Units"),
                  y=alt.Y("count():Q", title="Stores"))
          .properties(height=200))
    st.altair_chart(ch, use_container_width=True)

# ── Aggregate timeline ─────────────────────────────────────────
st.subheader("📅 Aggregate timeline")
tl = pd.DataFrame([{
    "week": s['week'],
    "demand": s['demand'],
    "sales": s['sales'],
    "missed": s['missed'],
    "stores_sum": s.get('stores_sum', 0),
    "cw_stock":   s.get('cw_stock', 0),
    "semi_stock": s.get('semi_stock', 0),
    "raw_mat_stock": s.get('raw_mat_stock', 0),
    "wip_total":  s.get('wip_total', 0),
    "backlog":    s.get('backlog', 0),
} for s in states])

tab1, tab2, tab3 = st.tabs(["Demand / Sales / Missed", "Stock by stage", "Backlog & WIP"])
with tab1:
    long = tl.melt(id_vars="week", value_vars=["demand", "sales", "missed"],
                   var_name="metric", value_name="units")
    ch = (alt.Chart(long).mark_line(point=True)
          .encode(x="week:Q", y="units:Q",
                  color=alt.Color("metric:N",
                                  scale=alt.Scale(domain=["demand", "sales", "missed"],
                                                  range=["#1f77b4", "#2ca02c", "#d62728"])))
          .properties(height=320))
    st.altair_chart(ch, use_container_width=True)
with tab2:
    long = tl.melt(id_vars="week",
                   value_vars=["stores_sum", "cw_stock", "semi_stock", "raw_mat_stock"],
                   var_name="stage", value_name="units")
    ch = (alt.Chart(long).mark_area(opacity=0.7)
          .encode(x="week:Q", y=alt.Y("units:Q", stack="zero"),
                  color=alt.Color("stage:N",
                                  scale=alt.Scale(domain=["raw_mat_stock", "semi_stock",
                                                          "cw_stock", "stores_sum"],
                                                  range=["#8c564b", "#ff7f0e",
                                                         "#9467bd", "#2ca02c"])))
          .properties(height=320))
    st.altair_chart(ch, use_container_width=True)
with tab3:
    long = tl.melt(id_vars="week", value_vars=["wip_total", "backlog"],
                   var_name="metric", value_name="units")
    ch = (alt.Chart(long).mark_line(point=True)
          .encode(x="week:Q", y="units:Q", color="metric:N")
          .properties(height=320))
    st.altair_chart(ch, use_container_width=True)

# ── Per-store stock heatmap ────────────────────────────────────
with st.expander("🗺️ Per-store stock heatmap (week × store)"):
    if n_stores <= 200:
        hm = np.stack(store_history[1:], axis=0)  # shape (weeks, n_stores)
        hm_df = pd.DataFrame(hm).reset_index().melt(id_vars="index",
                                                    var_name="store", value_name="stock")
        hm_df.columns = ["week", "store", "stock"]
        hm_df["week"]  = hm_df["week"] + 1
        ch = (alt.Chart(hm_df).mark_rect()
              .encode(x=alt.X("week:O", title="Week"),
                      y=alt.Y("store:O", title="Store #",
                              axis=alt.Axis(labels=(n_stores <= 50))),
                      color=alt.Color("stock:Q", scale=alt.Scale(scheme="viridis")))
              .properties(height=min(400, 6 * n_stores + 50)))
        st.altair_chart(ch, use_container_width=True)
    else:
        st.info("Heatmap suppressed for N > 200 stores (too dense). "
                "See the histograms above for distribution.")

# ── Economics breakdown ────────────────────────────────────────
with st.expander("💰 Economics breakdown"):
    df = pd.DataFrame([
        {"item": "Revenue",                "amount":  kpis['revenue']},
        {"item": "Initial stock value",    "amount": -kpis['init_stock_value']},
        {"item": "Material costs",         "amount": -kpis['cost_mat_total']},
        {"item": "Semi-Fin processing",    "amount": -kpis['cost_semi_total']},
        {"item": "Finishing processing",   "amount": -kpis['cost_fp_total']},
        {"item": "Gross margin",           "amount":  kpis['gm']},
        {"item": "Fixed costs",            "amount": -kpis['fixed']},
        {"item": "Net margin",             "amount":  kpis['margin']},
    ])
    st.dataframe(df.style.format({"amount": "{:,.0f}"}), use_container_width=True)

# ── Footer / teaching summary ──────────────────────────────────
with st.expander("📚 Teaching notes — what to look for"):
    st.markdown("""
**Centralisation vs distribution.** At N=2 stores, the difference between a centralised
warehouse and distributed store stock is small because total variance is low. At
N=200 stores with 0–1 pc/wk per store, individual demand is *very* lumpy — half the
stores can want zero in a week while a few want 2 or 3. Pooled inventory (in the
CW) covers this variance with √N stock; distributed store inventory needs N times
more to hit the same per-store service level.

**Why Agile dominates Push at large N.** The Push profile holds 100% of stock in
stores up-front with a 4-week order cycle: by week 4 the lucky stores are full
and the unlucky stores are empty, with no mechanism to redistribute. Agile holds
40% upstream and pushes weekly to whichever stores are running low, equalising
cover across the network.

**Safety stock z.** With Poisson per-store demand, the ship-stage target needs
to be `μ + z·√(N·λ·cov_ship)` to hit a target per-store service. At z=0 the
engine targets only the mean; at z=1.65 it adds enough buffer for ~95% service.
For luxury-scale networks (0–1 pc/wk per store), z=1.65–2.33 is typical.
""")

