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

# Demand shape labels (visible in the selector).
# Linear unifies the old "flat / ramp up / ramp down" cases via a single
# (final-value, transition-weeks) pair, with start always = BASE_FORECAST.
DEMAND_SHAPES = [
    "📊 Linear (start 100 → final value)",
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
    "n_stores": 50, "smart_distrib": True,
    "demand_shape": DEMAND_SHAPES[0],
    "app_mode": "Single Scenario",
    # "kickstart": False,  # removed in v3
    "debug_mode": False,
    "week_num": 0,
    "lin_end": 100, "lin_wks": 1,
    "seas_sub": "Steep",
    "seas_avg": 100,
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
    users change sim length. Returns integers (display/sim-input form).
    """
    ratio, k = SEASONAL_PARAMS.get(sub_shape, SEASONAL_PARAMS["Steep"])
    theta = (ratio * weeks) / max(k - 1, 0.1)
    pdf_vals = [gamma_pdf(w, k, theta) for w in range(1, weeks + 1)]
    pdf_sum = sum(pdf_vals) or 1.0
    total = avg * weeks
    return [max(0, int(round(v * total / pdf_sum))) for v in pdf_vals]


def seasonal_curve_float(weeks: int, sub_shape: str, avg: float) -> list[float]:
    """
    Same gamma shape as seasonal_curve, but UNROUNDED. Used as the planner's
    internal belief curve so the discovered amplitude factor is a clean ratio
    (e.g., exactly 3.0 for avg=300 vs. avg=100, not 2.97 from integer drift).
    """
    ratio, k = SEASONAL_PARAMS.get(sub_shape, SEASONAL_PARAMS["Steep"])
    theta = (ratio * weeks) / max(k - 1, 0.1)
    pdf_vals = [gamma_pdf(w, k, theta) for w in range(1, weeks + 1)]
    pdf_sum = sum(pdf_vals) or 1.0
    total = avg * weeks
    return [max(0.0, v * total / pdf_sum) for v in pdf_vals]


def build_demand_curve(shape: str, weeks: int, *,
                       base: int = BASE_FORECAST,
                       lin_end: int = 100, lin_wks: int = 1,
                       seas_sub: str = "Steep", seas_avg: int = 100) -> list[int]:
    """
    Return a length-(weeks+1) demand list where index 0 = 0 (W0) and indices
    1..weeks = demand at each week.

    Linear   : start at `base`, linearly reach `lin_end` over `lin_wks` weeks,
               then hold flat at `lin_end`. If lin_end == base, the curve is
               flat for the whole sim.
    Seasonal : gamma curve scaled so total = seas_avg × weeks.
    """
    out = [0]
    if "Seasonal" in shape:
        out.extend(seasonal_curve(weeks, seas_sub, seas_avg))
    else:  # Linear (covers flat/up/down via the one (lin_end, lin_wks) pair)
        for w in range(1, weeks + 1):
            if lin_wks > 0 and w <= lin_wks:
                val = base + (lin_end - base) * w / lin_wks
            else:
                val = lin_end
            out.append(max(0, int(round(val))))
    return out


def recommend_initial_stock(shape: str, weeks: int, coverage: int, *,
                            base: int = BASE_FORECAST,
                            lin_end: int = 100, lin_wks: int = 1,
                            seas_sub: str = "Steep", seas_avg: int = 100) -> tuple[int, str]:
    """
    Smart initial-stock recommendation: sum of actual demand over the first
    `coverage` weeks of the chosen shape. Returns (units, human_description).
    """
    n = min(coverage, weeks)
    curve = build_demand_curve(shape, weeks,
                               base=base, lin_end=lin_end, lin_wks=lin_wks,
                               seas_sub=seas_sub, seas_avg=seas_avg)
    total = sum(curve[1:n + 1])
    if "Seasonal" in shape:
        detail = f"sum of first {n} wks of {seas_sub} curve (avg {seas_avg}/wk)"
    elif lin_end == base:
        detail = f"{base}/wk × {n} wks (flat)"
    elif lin_end > base:
        detail = f"first {n} wks of ramp {base}→{lin_end} over {lin_wks}wk"
    else:
        detail = f"first {n} wks of drop {base}→{lin_end} over {lin_wks}wk"
    return int(total), detail


# ════════════════════════════════════════════════════════════════
# SIMULATION ENGINE — pure function, cached by Streamlit
# ════════════════════════════════════════════════════════════════

def _smart_alloc_n(ship_out, stores_arr, dist_pipes, forecast_per_store):
    """
    Water-fill `ship_out` units across N stores to equalise weeks-of-cover.
    `stores_arr` : np.array of length N (current store stock)
    `dist_pipes` : list of N lists (each store's distribution pipe)
    `forecast_per_store` : np.array of length N (weekly demand expected per store)
    Returns an integer np.array of length N summing exactly to int(ship_out).
    """
    n = len(stores_arr)
    allocs = np.zeros(n, dtype=float)
    if ship_out <= 0 or n == 0:
        return allocs.astype(int)

    eps = 1e-6
    fcst = np.maximum(forecast_per_store, eps)
    ip = stores_arr + np.array([sum(dp) for dp in dist_pipes], dtype=float)
    cov = ip / fcst
    order = np.argsort(cov)
    cov_sorted = cov[order].copy()
    fcst_sorted = fcst[order]

    remaining = float(ship_out)
    for k in range(n - 1):
        delta = cov_sorted[k + 1] - cov_sorted[k]
        if delta <= 0:
            continue
        cost = delta * fcst_sorted[:k + 1].sum()
        if cost <= remaining + eps:
            for j in range(k + 1):
                allocs[order[j]] += delta * fcst_sorted[j]
            remaining -= cost
            cov_sorted[:k + 1] = cov_sorted[k + 1]
        else:
            for j in range(k + 1):
                allocs[order[j]] += remaining * fcst_sorted[j] / fcst_sorted[:k + 1].sum()
            remaining = 0
            break

    if remaining > eps:
        for j in range(n):
            allocs[order[j]] += remaining * fcst_sorted[j] / fcst.sum()

    int_allocs = np.round(allocs).astype(int)
    diff = int(ship_out) - int(int_allocs.sum())
    if diff != 0:
        biggest = int(np.argmax(fcst))
        int_allocs[biggest] += diff
    return int_allocs


# Store-bucket mix:
#   high-selling  10% × 4.0× rate  → carries 40% of total sales
#   medium        30% × 1.0× rate  → carries 30%
#   small         60% × 0.5× rate  → carries 30%
# Mean weight = 0.10·4 + 0.30·1 + 0.60·0.5 = 1.0 (no scaling drift; the
# multinomial preserves aggregate demand exactly).
TIER_WEIGHTS = {"small": 0.5, "medium": 1.0, "high": 4.0}
TIER_SHARE   = {"small": 0.60, "medium": 0.30, "high": 0.10}

def _store_tier_probs(n_stores, rng_seed=None):
    """
    Persistent per-store demand share.

    Slow stores rarely sell (most weeks 0, occasionally 1), medium sell
    ~1/wk, high sell several per week. The same store keeps its tier all
    simulation long — assignment is a deterministic shuffle seeded by
    rng_seed, so re-running with the same N yields the same tier map.

    Returns
    -------
    probs   : np.array length N, sum = 1 (feed to rng.multinomial)
    weights : np.array length N, raw tier weights (mean ≈ 1.1)
    tiers   : list[str] length N, "small" / "medium" / "high"
    """
    n = int(max(1, n_stores))
    n_high   = max(1, int(round(n * TIER_SHARE["high"])))
    n_medium = max(1, int(round(n * TIER_SHARE["medium"])))
    n_small  = n - n_high - n_medium
    if n_small < 0:                                 # very small N: clamp
        n_small = 0
        n_medium = max(0, n - n_high)
    # Deterministic ordering: store 1..n_high are high-selling stores, next n_medium
    # are medium, the rest are small. Makes the per-store matrix readable
    # without sorting and keeps store ids stable across runs.
    weights = np.concatenate([
        np.full(n_high,   TIER_WEIGHTS["high"]),
        np.full(n_medium, TIER_WEIGHTS["medium"]),
        np.full(n_small,  TIER_WEIGHTS["small"]),
    ])
    tiers = np.where(weights == TIER_WEIGHTS["high"], "high",
             np.where(weights == TIER_WEIGHTS["medium"], "medium", "small")).tolist()
    probs = weights / weights.sum()
    return probs, weights, tiers


# Per-store demand is DETERMINISTIC (no RNG). Each bucket follows a fixed
# rate ratio high-selling : medium : small = 15 : 6 : 1, scaled so that the
# total demand each week equals the user's `dem_total` exactly.
#
# Example (user's spec, 6 stores, D=10/wk):
#   1 high-selling  rate 5/wk   ⇒ sells 5/wk every week
#   2 medium    rate 2/wk   ⇒ each sells 2/wk every week
#   3 small     rate 1/3/wk ⇒ one sells 1 each week, cycling s1→s2→s3→…
#
# Rounding rule: when bucket totals don't sum to dem_total (fractional
# rates round down to integers), the residual goes to the HIGH-SELLING
# bucket starting from the first store. Within medium/small the integer +1
# from rotation cycles across stores so each store hits its long-run
# average target.
def _bucket_counts(n_stores):
    """How many stores per bucket given the share constants."""
    n = int(max(1, n_stores))
    n_high   = max(1, int(round(n * TIER_SHARE["high"])))
    n_medium = max(1, int(round(n * TIER_SHARE["medium"])))
    n_small  = n - n_high - n_medium
    if n_small < 0:
        n_small = 0
        n_medium = max(0, n - n_high)
    return n_high, n_medium, n_small


def _deterministic_per_store_demand(week, dem_total, n_high, n_medium, n_small):
    """
    Returns integer per-store demand for `week` (1-indexed), summing to
    exactly `dem_total`. Store order: high-selling[0..n_high-1],
    medium[0..n_medium-1], small[0..n_small-1].
    """
    N = n_high + n_medium + n_small
    dem_total = int(max(0, dem_total))
    if N == 0 or dem_total == 0:
        return np.zeros(max(1, N), dtype=int)

    denom = 15.0 * n_high + 6.0 * n_medium + 1.0 * n_small
    if denom <= 0:
        return np.zeros(N, dtype=int)

    bkt_f_int = int(15.0 * n_high   * dem_total / denom)
    bkt_m_int = int( 6.0 * n_medium * dem_total / denom)
    bkt_s_int = int( 1.0 * n_small  * dem_total / denom)
    # Rounding residual → high-selling bucket first (then medium, then small).
    residual = dem_total - (bkt_f_int + bkt_m_int + bkt_s_int)
    if residual > 0:
        give_to_flag   = min(residual, max(0, 15 * n_high))
        bkt_f_int     += give_to_flag
        residual      -= give_to_flag
        if residual > 0:
            give_to_med = min(residual, max(0, 6 * n_medium))
            bkt_m_int  += give_to_med
            residual   -= give_to_med
            bkt_s_int  += residual

    demand = np.zeros(N, dtype=int)

    # High-Selling: even base + the first `rem` high-selling stores always get +1
    # (deterministic — rule from spec, lands at target in long run).
    if n_high > 0:
        base = bkt_f_int // n_high
        rem  = bkt_f_int -  base * n_high
        demand[:n_high] = base
        if rem > 0:
            demand[:rem] += 1

    # Medium / Small: even base + cyclic rotation of the integer remainder
    # so every store in the bucket hits its long-run average.
    def _fill(start, n, total):
        if n == 0 or total == 0:
            return
        base = total // n
        rem  = total - base * n
        demand[start : start + n] = base
        if rem > 0:
            offset = ((week - 1) * rem) % n
            for k in range(rem):
                demand[start + ((offset + k) % n)] += 1
    _fill(n_high,            n_medium, bkt_m_int)
    _fill(n_high + n_medium, n_small,  bkt_s_int)

    return demand


# Fixed seed for per-store demand draws (kept out of the UI so the simulator
# is always reproducible — same inputs, same draws every time).
FIXED_RNG_SEED = 42

# Learning rate for the planner's per-store demand EMA (chase-the-curve at
# store level). α = 0.3 → effective horizon ~3.3 weeks: fast enough to
# distinguish tiers, slow enough not to over-react to single-week noise.
PS_RATE_ALPHA = 0.3


@st.cache_data
def run_simulation(weeks, init_store, init_cw, init_semi, init_rawmat,
                   order_freq, mat_lt, semi_lt, fp_lt, dist_lt,
                   cap_start, cap_ramp, base_forecast,
                   price, var_cost, fixed_pct, n_stores, rng_seed, smart_distrib,
                   debug=False,
                   custom_demand=None,
                   planner_curve=None,
                   prod_cap=None):
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

    Per-stage push policy (v3 "advanced stage ordering")
    ----------------------------------------------------
    Each push lever has its own (R, S) decision applied at review weeks
    (every `order_freq` weeks), parallel to the supplier order:

        Lever            Downstream span (cov.)          Backlog variable
        ---------------- ------------------------------- ------------------
        Supplier order   mat+semi+fp+dist+freq           pb
        si  (RM→Semi)    semi+fp+dist+freq               semi_backlog
        fi  (Semi→FP)    fp+dist+freq                    fp_backlog
        CW push          dist+freq                       ship_backlog

    At review week w:  order_x = max(0, target_x − existing_x); backlog_x += order_x
    Between reviews:   each lever executes from its backlog at its own
                       per-week capacity, capped by the upstream buffer.

    Per-stage ramp counters (pn/sn/fn/dn) advance starting the week AFTER
    that stage's first push (independent activation flags). Same forecast
    `ff` is used for all stages and is updated on review weeks only.

    Seasonal forecasting (v3.1)
    ---------------------------
    If `planner_curve` is provided (length weeks+1, index 0 ignored), the
    planner uses a SHAPE-aware lookahead in place of the constant-forecast
    `ff × coverage`:

      • The planner believes the curve is `planner_curve` (scaled to an
        avg of base_forecast/wk — i.e., the planner knows the shape but
        guesses an amplitude).
      • At the FIRST review week, the planner computes one adjustment
        factor `f = sum(actual_demand[1..w]) / sum(planner_curve[1..w])`
        and locks it for the rest of the simulation.
      • For each subsequent review and each stage, the target is
        `Σ planner_curve[w+1..w+cov_x] × f`, capped at sim end (no
        hypothetical future demand beyond `weeks`).

    Non-seasonal modes (`planner_curve is None`) keep v3 `ff × coverage`.

    Parameters
    ----------
    debug : bool
        If True, runtime sanity assertions are active (conservation checks).
    custom_demand : sequence or None
        Per-week demand override indexed 0..weeks (index 0 ignored).
    planner_curve : sequence or None
        Planner's belief about the demand shape, scaled to avg base_forecast.
        Length weeks+1 (index 0 ignored). Triggers seasonal lookahead mode.

    Returns
    -------
    list[dict]
        Per-week state dicts. See the W0 state constructor below for the
        full schema of keys.
    """
    phys_lt = mat_lt + semi_lt + fp_lt + dist_lt
    coverage = phys_lt + order_freq
    n_stores = int(max(1, n_stores))
    rng = np.random.default_rng(int(rng_seed))
    # Store mix: 10% high-selling, 30% medium, 60% small (deterministic order —
    # stores 1..n_high are high-selling stores, etc.). Per-store demand is
    # DETERMINISTIC; rates follow high-selling:medium:small = 15:6:1, scaled so
    # the weekly total equals the user-set demand exactly.
    n_high, n_medium, n_small = _bucket_counts(n_stores)
    tier_probs, tier_weights, tier_labels = _store_tier_probs(n_stores, int(rng_seed))

    # Per-stage downstream coverages (how many weeks of demand each stage
    # must keep covered DOWNSTREAM of its own push point, including freq).
    cov_sup  = coverage
    cov_semi = semi_lt + fp_lt + dist_lt + order_freq
    cov_fp   = fp_lt + dist_lt + order_freq
    cov_ship = dist_lt + order_freq

    # --- Demand curve ---
    demand = {0: 0}
    if custom_demand is not None:
        for w in range(1, weeks + 1):
            demand[w] = int(custom_demand[w]) if w < len(custom_demand) else int(custom_demand[-1])
    else:
        for w in range(1, weeks + 1):
            demand[w] = base_forecast

    # --- Pipes (index 0 = front, about to arrive; index -1 = back, just entered) ---
    mat_pipe   = [0.0] * max(1, mat_lt)
    semi_pipe  = [0.0] * max(1, semi_lt)
    fp_pipe    = [0.0] * max(1, fp_lt)
    dist_pipes = [[0.0] * max(1, dist_lt) for _ in range(n_stores)]

    # --- Buffers ---
    # Stores: split init_store equally across N stores (any remainder goes
    # to the first few stores so the integer total is preserved).
    base_each = int(init_store) // n_stores
    rem       = int(init_store) - base_each * n_stores
    stores = np.full(n_stores, float(base_each))
    if rem > 0:
        stores[:rem] += 1.0
    raw_mat = float(init_rawmat)
    semi    = float(init_semi)
    cw      = float(init_cw)

    # --- Per-stage state ---
    # Backlogs: units the planner has decided to push at each stage but
    # which have not yet been physically processed.
    pb            = 0.0   # supplier backlog (orders placed, not yet shipped)
    semi_backlog  = 0.0   # RM→Semi push orders not yet executed
    fp_backlog    = 0.0   # Semi→FP push orders not yet executed
    ship_backlog  = 0.0   # CW→stores push orders not yet executed

    # Ramp counters: each advances from the week AFTER that stage's first push.
    # No counter for distribution: CW→stores is logistics, not production, and
    # has no per-week capacity limit.
    pn = sn = fn = 0
    supplier_active_from = None   # week of first supplier ship > 0
    semi_active_from     = None   # week of first si > 0
    fp_active_from       = None   # week of first fi > 0

    co  = 0.0                  # cumulative supplier orders placed
    cas = 0.0                  # cumulative units arrived at stores
    smart_discovered = False   # planner discovers A/B imbalance at first review

    order_weeks = list(range(order_freq, weeks + 1, order_freq)) if order_freq > 1 else list(range(1, weeks + 1))
    ff = float(base_forecast)  # aggregate forecast — updates on review weeks
    # Per-store demand EMA — what the planner has LEARNED about each
    # store's sell rate from observation. Starts uniform (planner has no
    # prior knowledge of tier mix), updates on review weeks toward the
    # observed per_store_dem. After a few weeks, high stores' rates rise
    # well above the mean and slow stores' rates collapse toward zero.
    ps_rate = np.full(n_stores, float(base_forecast) / max(1, n_stores))

    # --- Seasonal planner state ---
    seasonal_mode = planner_curve is not None
    planner_factor = None      # locked once at first review
    planner_factor_week = None # for one-time UI message
    if seasonal_mode:
        # Pad curve to weeks + max coverage so lookahead always has values
        # (past sim end → 0, per spec).
        max_cov = phys_lt + order_freq
        pc = [0.0] * (weeks + max_cov + 2)
        for i in range(min(len(planner_curve), weeks + 1)):
            pc[i] = float(planner_curve[i])
        planner_curve_internal = pc
    else:
        planner_curve_internal = None

    def _lookahead_sum(curve, w_from, w_to):
        """Sum of curve[w_from .. w_to] inclusive, scaled by planner_factor."""
        s = 0.0
        for i in range(w_from, w_to + 1):
            if 0 <= i < len(curve):
                s += curve[i]
        return s * (planner_factor or 1.0)

    # --- W0 initial state ---
    s0 = {
        'week': 0,
        'demand': 0,
        'forecast': base_forecast,
        'mat_arr': 0, 'semi_arr': 0, 'fp_arr': 0,
        'dist_arr': 0,
        'sales': 0, 'missed': 0,
        'stores': stores.tolist(), 'store_stock': float(stores.sum()),
        'stores_min': float(stores.min()), 'stores_max': float(stores.max()),
        'stores_mean': float(stores.mean()), 'stores_std': float(stores.std()),
        'stores_w_stockout': 0,
        'per_store_dem': [0] * n_stores,
        'per_store_sales': [0] * n_stores,
        'per_store_missed': [0] * n_stores,
        'tier_labels': list(tier_labels),
        'tier_weights': [float(x) for x in tier_weights],
        'supplier_shipped': 0, 'supplier_cap': cap_start,
        'raw_mat_before_prod': raw_mat, 'raw_mat_stock': raw_mat,
        'semi_input': 0, 'semi_cap': cap_start, 'semi_stock': semi,
        'fp_input': 0, 'fp_cap': cap_start,
        'cw_shipped': 0, 'cw_stock': cw,
        'allocs': [0] * n_stores,
        'mat_pipe':   list(mat_pipe),
        'semi_pipe':  list(semi_pipe),
        'fp_pipe':    list(fp_pipe),
        'dist_pipes': [list(dp) for dp in dist_pipes],
        'order': 0, 'order_semi': 0, 'order_fp': 0, 'order_ship': 0,
        'pending': 0, 'backlog': 0,
        'semi_backlog': 0, 'fp_backlog': 0, 'ship_backlog': 0,
        'target_sup': 0, 'planner_factor': None, 'planner_factor_locked': False,
        'planner_calc': None,
        'wip_total': 0,
        # W0 has no production costs — initial stock is valued separately
        'cost_mat': 0.0, 'cost_semi': 0.0, 'cost_fp': 0.0,
        'coverage': coverage,
        'comment': "Week 0 — initial state (pre-positioned stock).",
    }
    states = [s0]

    # Cumulative-production cap. The user's "max products in chain" is a
    # LIFETIME budget: initial stock at W0 + all units the supplier ships
    # over the simulation can never exceed prod_cap. Once exhausted, no
    # more supplier orders are placed. Initial stock counts as production
    # already done at W0.
    cum_produced = float(init_store + init_cw + init_semi + init_rawmat)

    for w in range(1, weeks + 1):
        s = {'week': w}
        dem_total = demand[w]
        # Deterministic per-store demand (no RNG). high-selling:medium:small
        # rates = 15:6:1, scaled so Σ per_store_dem == dem_total exactly
        # this week. Rounding remainder → high-selling bucket first; medium
        # and small bucket remainders rotate across stores by week so
        # every store within a bucket hits its long-run average.
        per_store_dem = _deterministic_per_store_demand(
            w, int(dem_total), n_high, n_medium, n_small,
        )

        # Forecast updates only at review weeks (periodic-review blind between).
        # Aggregate forecast is reactive (last week's demand); per-store rates
        # are an EMA, so the planner learns each store's tier from the data.
        if w in order_weeks:
            ff = float(dem_total)
            ps_rate = ((1.0 - PS_RATE_ALPHA) * ps_rate
                       + PS_RATE_ALPHA * per_store_dem.astype(float))
            if smart_distrib and not smart_discovered:
                smart_discovered = True
        s['demand'] = dem_total
        s['ps_rate'] = [round(float(x), 3) for x in ps_rate.tolist()]
        s['forecast'] = round(ff, 1)

        # 1. Arrivals — clear pipe fronts
        m_arr  = mat_pipe[0]
        sm_arr = semi_pipe[0]
        fp_arr = fp_pipe[0]
        store_arrivals = np.array([dp[0] for dp in dist_pipes], dtype=float)
        d_arr_total = float(store_arrivals.sum())
        mat_pipe[0] = 0.0; semi_pipe[0] = 0.0; fp_pipe[0] = 0.0
        for dp in dist_pipes:
            dp[0] = 0.0
        s['mat_arr']  = round(m_arr, 1)
        s['semi_arr'] = round(sm_arr, 1)
        s['fp_arr']   = round(fp_arr, 1)
        s['dist_arr'] = round(d_arr_total, 1)
        cas += d_arr_total

        # 2. Store sales (per store, vectorised)
        available = stores + store_arrivals
        per_store_sales = np.minimum(per_store_dem.astype(float), available)
        per_store_missed = per_store_dem.astype(float) - per_store_sales
        stores = available - per_store_sales

        sales = float(per_store_sales.sum())
        missed = float(per_store_missed.sum())
        s.update({
            'stores': [round(x, 1) for x in stores.tolist()],
            'store_stock': round(float(stores.sum()), 1),
            'stores_min':  round(float(stores.min()), 1),
            'stores_max':  round(float(stores.max()), 1),
            'stores_mean': round(float(stores.mean()), 2),
            'stores_std':  round(float(stores.std()), 2),
            'stores_w_stockout': int((per_store_missed > 0.5).sum()),
            'per_store_dem':    per_store_dem.astype(int).tolist(),
            'per_store_sales':  [round(x, 1) for x in per_store_sales.tolist()],
            'per_store_missed': [round(x, 1) for x in per_store_missed.tolist()],
            'sales': round(sales, 1), 'missed': round(missed, 1),
        })

        # 3. Arrivals update buffers
        raw_mat += m_arr
        semi    += sm_arr
        cw      += fp_arr
        s['raw_mat_before_prod'] = round(raw_mat, 1)

        # --- Execution-lag snapshot: each stage's actual ship/process this
        # --- week uses the backlog AS IT STOOD AT THE END OF LAST WEEK, not
        # --- the post-this-week's-planning value. This implements the
        # --- "decision at week N → execution starts week N+1" semantic the
        # --- user specified. Equivalent to: planner orders at end of week,
        # --- supplier/factory acts on it starting next week.
        pb_avail      = pb
        semi_bl_avail = semi_backlog
        fp_bl_avail   = fp_backlog
        ship_bl_avail = ship_backlog

        # 4. Planner review (only on review weeks). Computes four push
        #    orders in parallel — one per stage — each comparing a target
        #    against a downstream existing position that includes that
        #    stage's own outstanding backlog (so consecutive reviews don't
        #    double-count).
        #
        #    Flat / Ramp / Drop:  target = ff × cov_x          (constant)
        #    Seasonal:            target = Σ planner_curve[w+1..w+cov_x] × f
        #                         where f is locked at the first review.
        od_sup = od_semi = od_fp = od_ship = 0
        if w in order_weeks:
            # Lock the planner's adjustment factor on the first review.
            if seasonal_mode and planner_factor is None:
                actual_cum   = sum(demand[i] for i in range(1, w + 1))
                expected_cum = sum(planner_curve_internal[i] for i in range(1, w + 1))
                if expected_cum > 0.01:
                    raw_factor = actual_cum / expected_cum
                    # Snap to clean integer (or 0.1 increment) when the
                    # observed ratio is within rounding-drift distance of
                    # one. The rounded integer demand curves the user picks
                    # via the presets always produce factors that are nearly
                    # but not exactly clean (avg=300 vs avg=100 gives ~2.99
                    # because each weekly demand was independently rounded).
                    # Snapping keeps the math clean and the display readable
                    # without affecting any other scenario.
                    snapped_int = round(raw_factor)
                    if abs(raw_factor - snapped_int) < 0.05 and snapped_int > 0:
                        planner_factor = float(snapped_int)
                    else:
                        snapped_1dec = round(raw_factor * 10) / 10
                        # 0.02 tolerance: at low ratios (e.g. avg=30 → ~0.288)
                        # the per-week integer rounding causes relatively
                        # larger drift than at high ratios.
                        if abs(raw_factor - snapped_1dec) < 0.02:
                            planner_factor = snapped_1dec
                        else:
                            planner_factor = raw_factor
                else:
                    planner_factor = 1.0
                planner_factor_week = w

            store_sum  = float(stores.sum())
            dist_total = sum(sum(dp) for dp in dist_pipes)
            existing_sup  = (store_sum
                           + sum(mat_pipe) + raw_mat
                           + sum(semi_pipe) + semi
                           + sum(fp_pipe) + cw
                           + dist_total + pb)
            existing_semi = (store_sum
                           + sum(semi_pipe) + semi
                           + sum(fp_pipe) + cw
                           + dist_total + semi_backlog)
            existing_fp   = (store_sum
                           + sum(fp_pipe) + cw
                           + dist_total + fp_backlog)
            existing_ship = (store_sum
                           + dist_total + ship_backlog)

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

            od_sup  = math.ceil(max(0, tgt_sup  - existing_sup))
            od_semi = math.ceil(max(0, tgt_semi - existing_semi))
            od_fp   = math.ceil(max(0, tgt_fp   - existing_fp))
            od_ship = math.ceil(max(0, tgt_ship - existing_ship))

            # Snapshot of pooled-only orders (for debug display)
            pooled_od = {'sup': od_sup, 'semi': od_semi, 'fp': od_fp, 'ship': od_ship}
            ps_gaps = {'sup': 0.0, 'semi': 0.0, 'fp': 0.0, 'ship': 0.0}

            # Per-store gap adjustment: pooled targets can miss the case where
            # some stores starve while others have surplus. With smart
            # distribution, the planner's expected allocation of the common
            # upstream pool is the cover-equalising allocation that the CW
            # push step will actually do later. Generalised to N stores via
            # water-filling: each store's expected share of the pool is what
            # raises it to the equal-cover line, clipped to [0, common_pool].
            if smart_distrib and n_stores > 1:
                own = stores + np.array([sum(dp) for dp in dist_pipes], dtype=float)

                def _ps_gap(cov_x, common_pool):
                    if seasonal_mode:
                        dx = _lookahead_sum(planner_curve_internal, w + 1, w + cov_x)
                    else:
                        dx = ff * cov_x
                    # Tier-aware water-fill using the planner's LEARNED
                    # per-store rates (ps_rate, EMA of observed demand).
                    # Equalises weeks-of-cover at each store's own rate:
                    #     own[i] + share[i] = target_cov × ps_rate[i]
                    fcst = np.maximum(ps_rate, 1e-6)
                    fcst_total = float(fcst.sum())
                    target_cov = (float(own.sum()) + float(common_pool)) / max(fcst_total, 1e-6)
                    raw_shares = np.maximum(0.0, target_cov * fcst - own)
                    s_sum = float(raw_shares.sum())
                    if s_sum > common_pool + 1e-9 and s_sum > 0:
                        raw_shares = raw_shares * (common_pool / s_sum)
                    supply = own + raw_shares
                    # Per-store horizon demand: dx × (ps_rate[i] / Σps_rate)
                    # — the planner's belief about each store's share.
                    dx_per = dx * (fcst / fcst_total)
                    gaps = np.maximum(0.0, dx_per - supply)
                    return float(gaps.sum())

                pool_ship = 0
                pool_fp   = sum(fp_pipe) + cw
                pool_semi = sum(semi_pipe) + semi + sum(fp_pipe) + cw
                pool_sup  = (sum(mat_pipe) + raw_mat + sum(semi_pipe) + semi
                           + sum(fp_pipe) + cw + pb)

                od_ship_smart = math.ceil(max(0, _ps_gap(cov_ship, pool_ship) - ship_backlog))
                od_fp_smart   = math.ceil(max(0, _ps_gap(cov_fp,   pool_fp)   - fp_backlog))
                od_semi_smart = math.ceil(max(0, _ps_gap(cov_semi, pool_semi) - semi_backlog))
                od_sup_smart  = math.ceil(max(0, _ps_gap(cov_sup,  pool_sup)  - pb))
                ps_gaps = {'sup': od_sup_smart, 'semi': od_semi_smart,
                           'fp': od_fp_smart, 'ship': od_ship_smart}
                od_ship = max(od_ship, od_ship_smart)
                od_fp   = max(od_fp,   od_fp_smart)
                od_semi = max(od_semi, od_semi_smart)
                od_sup  = max(od_sup,  od_sup_smart)

            # Cumulative production cap. Initial stock + every supplier
            # order placed over the run must never exceed prod_cap.
            # Once the lifetime budget is spent, od_sup is forced to 0.
            if prod_cap is not None:
                headroom = max(0.0, float(prod_cap) - cum_produced)
                od_sup = min(od_sup, headroom)
            cum_produced += od_sup

            co            += od_sup
            pb            += od_sup
            semi_backlog  += od_semi
            fp_backlog    += od_fp
            ship_backlog  += od_ship

            # Snapshot for the Debug calculations expander
            s['planner_calc'] = {
                'sup':  {'target': tgt_sup,  'existing': existing_sup,
                         'pooled_order': pooled_od['sup'],  'ps_gap': ps_gaps['sup'],
                         'final_order': od_sup},
                'semi': {'target': tgt_semi, 'existing': existing_semi,
                         'pooled_order': pooled_od['semi'], 'ps_gap': ps_gaps['semi'],
                         'final_order': od_semi},
                'fp':   {'target': tgt_fp,   'existing': existing_fp,
                         'pooled_order': pooled_od['fp'],   'ps_gap': ps_gaps['fp'],
                         'final_order': od_fp},
                'ship': {'target': tgt_ship, 'existing': existing_ship,
                         'pooled_order': pooled_od['ship'], 'ps_gap': ps_gaps['ship'],
                         'final_order': od_ship},
            }
        else:
            s['planner_calc'] = None
        s['order']      = round(od_sup, 0)   # 'order' = supplier order (legacy field name)
        s['order_semi'] = round(od_semi, 0)
        s['order_fp']   = round(od_fp, 0)
        s['order_ship'] = round(od_ship, 0)
        s['planner_factor']        = planner_factor
        s['planner_factor_locked'] = (planner_factor_week == w)  # True only on the week of lock

        # Supplier-side lookahead target stored every week (not just review weeks)
        # so the info bar can always show "demand to cover in next LT+freq wks".
        if seasonal_mode:
            s['target_sup'] = round(_lookahead_sum(planner_curve_internal, w + 1, w + cov_sup), 0)
        else:
            s['target_sup'] = round(ff * cov_sup, 0)

        # 5. Supplier ships — against pb_avail (end-of-last-week backlog), capped
        # by supplier capacity. New orders placed this week (added to pb at
        # step 4) only become available for shipping next week.
        pc = min(cap_start * (1 + pn * cap_ramp), cap_start * 10)
        if pb_avail > 0.01:
            shipped = math.ceil(min(pb_avail, pc))
            pb -= shipped
        else:
            shipped = 0.0
        if shipped > 0 and supplier_active_from is None:
            supplier_active_from = w
        s['supplier_shipped'] = round(shipped, 1)
        s['supplier_cap']     = round(pc, 0)

        # 6. Semi processing — RM → semi_pipe. Against semi_bl_avail (end-of-
        # last-week backlog), capped by RM available AND semi capacity.
        sc_ = min(cap_start * (1 + sn * cap_ramp), cap_start * 10)
        if raw_mat > 0.01 and semi_bl_avail > 0.01:
            si = math.ceil(min(raw_mat, sc_, semi_bl_avail))
            raw_mat      -= si
            semi_backlog -= si
        else:
            si = 0.0
        if si > 0 and semi_active_from is None:
            semi_active_from = w
        s['semi_input']    = round(si, 1)
        s['semi_cap']      = round(sc_, 0)
        s['raw_mat_stock'] = round(raw_mat, 1)
        s['semi_backlog']  = round(semi_backlog, 1)

        # 7. FP processing — Semi → fp_pipe. Against fp_bl_avail (end-of-last-
        # week backlog), capped by Semi available AND FP capacity.
        fpc = min(cap_start * (1 + fn * cap_ramp), cap_start * 10)
        if semi > 0.01 and fp_bl_avail > 0.01:
            fi = math.ceil(min(semi, fpc, fp_bl_avail))
            semi       -= fi
            fp_backlog -= fi
        else:
            fi = 0.0
        if fi > 0 and fp_active_from is None:
            fp_active_from = w
        s['fp_input']   = round(fi, 1)
        s['fp_cap']     = round(fpc, 0)
        s['semi_stock'] = round(semi, 1)
        s['fp_backlog'] = round(fp_backlog, 1)

        # 8. CW push — cw → dist_pipes. Distribution is pure logistics (trucks,
        #    picking, transport): no per-week throughput limit. Constrained
        #    only by available cw and the end-of-last-week ship_backlog.
        if cw > 0.01 and ship_bl_avail > 0.01:
            ship_out = math.ceil(min(cw, ship_bl_avail))
            cw           -= ship_out
            ship_backlog -= ship_out
        else:
            ship_out = 0.0
        s['cw_shipped']   = round(ship_out, 1)
        s['cw_stock']     = round(cw, 1)
        s['ship_backlog'] = round(ship_backlog, 1)

        if ship_out > 0:
            # Both modes use the planner's LEARNED per-store rate (ps_rate)
            # as the per-store forecast. The difference is the allocation
            # policy: smart is closed-loop (water-fills to equalise current
            # cover); push is open-loop (ships proportional to the learned
            # rate, ignoring current per-store stock).
            fcst_per_store = np.maximum(ps_rate, 0.01)
            if smart_distrib and smart_discovered:
                # Smart: water-fill to equalise weeks-of-cover.
                allocs = _smart_alloc_n(ship_out, stores, dist_pipes, fcst_per_store)
            else:
                # Push: tier-proportional shares (largest-remainder rounding so
                # the integer total equals ship_out exactly).
                props = fcst_per_store / fcst_per_store.sum()
                raw   = float(ship_out) * props
                floors = np.floor(raw).astype(int)
                deficit = int(ship_out) - int(floors.sum())
                if deficit > 0:
                    fracs = raw - floors
                    order = np.argsort(-fracs)              # largest fractional remainder first
                    floors[order[:deficit]] += 1
                allocs = floors
        else:
            allocs = np.zeros(n_stores, dtype=int)
        s['allocs'] = [int(x) for x in allocs.tolist()]

        # 9. Update pipes (shift, append new entrants)
        mat_pipe  = mat_pipe[1:]  + [shipped]
        semi_pipe = semi_pipe[1:] + [si]
        fp_pipe   = fp_pipe[1:]   + [fi]
        for i in range(n_stores):
            dist_pipes[i] = dist_pipes[i][1:] + [float(allocs[i])]
        s['mat_pipe']   = [round(x, 1) for x in mat_pipe]
        s['semi_pipe']  = [round(x, 1) for x in semi_pipe]
        s['fp_pipe']    = [round(x, 1) for x in fp_pipe]
        s['dist_pipes'] = [[round(x, 1) for x in dp] for dp in dist_pipes]

        # --- COST BOOKING — entering-stage convention ---
        # shipped units enter Material stage this week → book RM cost (50% VC)
        # si      units enter Semi stage this week      → book +25% VC
        # fi      units enter FP stage this week        → book +25% VC
        s['cost_mat']  = round(shipped * var_cost * VALOR_RAW_MAT, 1)
        s['cost_semi'] = round(si      * var_cost * (VALOR_SEMI - VALOR_RAW_MAT), 1)
        s['cost_fp']   = round(fi      * var_cost * (VALOR_FINISHED - VALOR_SEMI), 1)

        # 10. Per-stage ramp counters: each stage's first push runs at base
        #     capacity (pn/sn/fn = 0). Increment fires AT THE END of the
        #     first-push week so the NEXT week starts at the first ramped
        #     level. Each stage warms up independently.
        if supplier_active_from is not None and w >= supplier_active_from: pn += 1
        if semi_active_from     is not None and w >= semi_active_from:     sn += 1
        if fp_active_from       is not None and w >= fp_active_from:       fn += 1

        # 11. Post-processing WIP (for display)
        dist_total = sum(sum(dp) for dp in dist_pipes)
        total_wip = (sum(mat_pipe) + sum(semi_pipe) + sum(fp_pipe)
                     + dist_total + raw_mat + semi + cw + pb)
        s['wip_total'] = round(total_wip, 1)
        s['pending']   = round(co - cas, 0)
        s['backlog']   = round(pb, 0)
        s['coverage']  = coverage

        # Commentary (aggregate; per-store detail is in the zoom expander)
        stockout_n = int((per_store_missed > 0.5).sum())
        parts = []
        if missed > 0.5:
            parts.append(f"Sales {sales:.0f}/{dem_total} ({stockout_n}/{n_stores} stores w/ stockout).")
        else:
            parts.append(f"Sold {sales:.0f}/{dem_total}, stores hold {float(stores.sum()):.0f}.")
        if od_sup > 0:
            parts.append(f"ORDER {od_sup:.0f}.")
        if od_semi > 0 or od_fp > 0 or od_ship > 0:
            parts.append(f"Push targets — Semi {od_semi:.0f}, FP {od_fp:.0f}, Ship {od_ship:.0f}.")
        if shipped > 0.5:
            parts.append(f"Supplier {shipped:.0f}.")
        if si > 0.5: parts.append(f"Semi proc {si:.0f}.")
        if fi > 0.5: parts.append(f"FP proc {fi:.0f}.")
        if ship_out > 0.5:
            mode = "smart water-fill" if (smart_distrib and smart_discovered) else "equal split"
            parts.append(f"CW→stores {ship_out:.0f} ({mode}).")
        s['comment'] = " ".join(parts)

        # Debug assertions — conservation within single-week state
        if debug:
            assert (stores >= -0.5).all(), f"W{w}: negative store stock"
            assert raw_mat >= -0.5 and semi >= -0.5 and cw >= -0.5, f"W{w}: negative buffer"
            assert pb >= -0.5, f"W{w}: negative backlog"
            assert semi_backlog >= -0.5, f"W{w}: negative semi_backlog"
            assert fp_backlog >= -0.5, f"W{w}: negative fp_backlog"
            assert ship_backlog >= -0.5, f"W{w}: negative ship_backlog"

        states.append(s)

    # Debug: end-of-run unit conservation
    # Backlog (pb) is phantom — outstanding orders the supplier hasn't yet shipped,
    # so those units don't physically exist in our system. Don't count them here.
    if debug:
        init_units = init_store + init_cw + init_semi + init_rawmat
        total_shipped = sum(s['supplier_shipped'] for s in states)
        total_sales = sum(s['sales'] for s in states)
        end_stock_u = float(stores.sum()) + cw + semi + raw_mat
        end_pipe_u = (sum(mat_pipe) + sum(semi_pipe) + sum(fp_pipe)
                      + sum(sum(dp) for dp in dist_pipes))
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
    dist_units = sum(sum(dp) for dp in state.get('dist_pipes', []))
    return (sum(state.get('mat_pipe', [0]))  * var_cost * VALOR_RAW_MAT
          + sum(state.get('semi_pipe', [0])) * var_cost * VALOR_SEMI
          + sum(state.get('fp_pipe', [0]))   * var_cost * VALOR_FINISHED
          + dist_units                       * var_cost * VALOR_FINISHED)


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
    end_store    = last.get('store_stock', 0)
    end_cw       = last.get('cw_stock', 0)
    end_semi     = last.get('semi_stock', 0)
    end_rawmat   = last.get('raw_mat_stock', 0)
    end_stock_units = end_store + end_cw + end_semi + end_rawmat
    end_stock_value = _value_stock(end_store, end_cw, end_semi, end_rawmat, var_cost)

    end_dist_units = sum(sum(dp) for dp in last.get('dist_pipes', []))
    end_pipe_units = (sum(last.get('mat_pipe', [0]))
                    + sum(last.get('semi_pipe', [0]))
                    + sum(last.get('fp_pipe', [0]))
                    + end_dist_units)
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
        'stores_w_stockout_total': sum(s.get('stores_w_stockout', 0) for s in states),
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
        'stores_w_stockout': sum(s.get('stores_w_stockout', 0) for s in sub),
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
    end_stock_u = (last.get('store_stock', 0)
                 + last.get('cw_stock', 0) + last.get('semi_stock', 0)
                 + last.get('raw_mat_stock', 0))
    end_dist_u = sum(sum(dp) for dp in last.get('dist_pipes', []))
    end_pipe_u = (sum(last.get('mat_pipe', [])) + sum(last.get('semi_pipe', []))
                + sum(last.get('fp_pipe', []))  + end_dist_u)

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
    # Tolerance is relative because per-week costs are stored rounded to
    # one decimal — over hundreds of weeks × 3 stages, rounding drift can
    # reach a few €. The P&L identity (Check 3) is the strict guarantee.
    value_in = kpis['init_stock_value'] + kpis['prod_cost']
    value_remaining = kpis['leftover_value']
    value_sold = kpis['cost_of_sold']
    diff = abs(value_in - value_sold - value_remaining)
    tol = max(10.0, value_in * 1e-4)
    checks.append((
        "Stage value conserved",
        diff < tol,
        f"VC (€{value_in:,.0f}) ≈ sold (€{value_sold:,.0f}) + leftover (€{value_remaining:,.0f}), Δ=€{diff:.1f}",
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
    any_neg = any(any(x < -0.5 for x in s.get('stores', []))
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

    # Sum across all N stores' distribution pipes
    dist_pipes_state = state.get('dist_pipes', [])
    if dist_pipes_state:
        pipe_len = len(dist_pipes_state[0])
        dist_combined = [sum(dp[i] for dp in dist_pipes_state) for i in range(pipe_len)]
        dist_combined = list(reversed(dist_combined))
    else:
        dist_combined = []

    # WIP per band
    wip_mat  = sum(state.get('mat_pipe', []))  + raw_mat
    wip_semi = sum(state.get('semi_pipe', [])) + semi
    wip_fp   = sum(state.get('fp_pipe', []))   + cw
    wip_dist = sum(sum(dp) for dp in dist_pipes_state)

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
        f'{wip_label("WIP", wip_dist, dist_band_w)}'
        f'</div></div>'
    )

    # Supplier + single aggregated stores card
    n_stores_state = params.get('n_stores', len(state.get('stores', [1])))
    sup_html = supplier_card(state.get('backlog', 0), state.get('supplier_cap', 0), box_h)
    store_label = f"Stores (×{n_stores_state})"
    stores_html_card = store_card(store_label,
                                  state.get('store_stock', 0),
                                  state.get('demand', 0),
                                  state.get('sales', 0),
                                  state.get('missed', 0))
    stores_html = (
        f'<div style="display:flex;flex-direction:column;gap:6px;justify-content:center;'
        f'align-self:stretch;">{stores_html_card}</div>'
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
        f'<span style="font-size:12px;color:{C_TXT};" title="Demand the planner aims to cover in the next LT+freq weeks. The supplier order = max(0, this − stores − all WIP − backlog).">Cover Tgt <b style="color:#1a2a40;">{state.get("target_sup", 0):.0f}</b></span>'
        f'<span style="font-size:12px;color:{C_TXT};">Forecast <b style="color:#1a2a40;">{state.get("forecast", 0):.0f}</b>/wk</span>'
        f'<span style="font-size:12px;color:{C_TXT};">Stores <b style="color:#1a2a40;">{params.get("n_stores", 2)}</b></span>'
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
        # Stock is sized to the recommended cover at avg = BASE_FORECAST/wk
        # (NOT at the preset's seas_avg). This keeps the initial stock
        # constant across the 9 seasonal scenarios for a given LT profile,
        # so the comparison "30 vs 100 vs 300" actually shows the impact
        # of demand level, not stock level.
        cov = (lt["mat_lt"] + lt["semi_lt"] + lt["fp_lt"]
               + lt["dist_lt"] + lt["order_freq"])
        n = min(cov, PRESET_WEEKS)
        seas_baseline = seasonal_curve(PRESET_WEEKS, seas_sub, BASE_FORECAST)
        base_rec = sum(seas_baseline[:n])
        raw = base_rec / sell_through
        st.session_state["total_stock"] = int(math.ceil(raw / 50.0) * 50)
    else:
        dist = STOCK_DIST_OPERATIONAL[lt_name]
        coverage = lt["mat_lt"] + lt["semi_lt"] + lt["fp_lt"] + lt["dist_lt"] + lt["order_freq"]
        st.session_state["total_stock"] = min(BASE_FORECAST * coverage, 10000)

    # Kickstart OFF for all presets: initial WIP only moves when the planner
    # places an order. If pre-positioned RM/Semi never gets used because the
    # planner never orders, that's a real teaching point about over-investing
    # upstream — not something to paper over with a force-process.
    # kickstart removed in v3 — per-stage push policy supersedes it

    st.session_state["store_pct"]    = dist["store_pct"]
    st.session_state["wh_pct"]       = dist["wh_pct"]
    st.session_state["semi_pct"]     = dist["semi_pct"]
    st.session_state["smart_distrib"] = True

    if demand_kind == "Flat":
        st.session_state["demand_shape"] = DEMAND_SHAPES[0]
        st.session_state["lin_end"] = 100
        st.session_state["lin_wks"] = 1
    elif demand_kind == "Growth":
        st.session_state["demand_shape"] = DEMAND_SHAPES[0]
        st.session_state["lin_end"] = lr_end
        st.session_state["lin_wks"] = lr_wks
    elif demand_kind == "Drop":
        st.session_state["demand_shape"] = DEMAND_SHAPES[0]
        st.session_state["lin_end"] = ld_end
        st.session_state["lin_wks"] = ld_wks
    elif demand_kind == "Seasonal":
        st.session_state["demand_shape"] = DEMAND_SHAPES[1]
        st.session_state["seas_sub"] = seas_sub
        st.session_state["seas_avg"] = seas_avg

    # Reset navigation so user sees W0 of the new scenario
    st.session_state["week_num"] = 0


# ════════════════════════════════════════════════════════════════
# BATCH COMPARISON UI
# ════════════════════════════════════════════════════════════════
#
# A second page-mode that lets the user define many scenarios as rows
# of a table and run them in parallel, then export results as CSV (which
# can also be pasted back later to restore a saved batch).

import io

# Per-row input column names (≤ 22, each on max 2 lines via \n).
BATCH_INPUT_COLS = [
    "Scenario\nLabel",
    "Sim\nWeeks",
    "N\nStores",
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

# Output column names (appended after Run).
BATCH_OUTPUT_COLS = [
    "Revenue\n(€)",
    "Cumul Sales\n(pcs)",
    "Service\nLevel %",
    "Missed Total\n(pcs)",
    "Store-Stockout\nEvents",
    "Stockout\nWeeks",
    "Init Stock\nValue (€)",
    "Total VC\n(€)",
    "Fixed Cost\n(€)",
    "Net Margin\n(€)",
    "Margin\n% of rev",
    "Leftover Units\n(pcs)",
    "Leftover\nValue (€)",
    "Useful\nProd %",
    "Reconciled\n✓/✗",
]


def _batch_default_rows():
    """9 Permanent presets (Agile/Medium/Push × Drop/Flat/Growth) as starter rows."""
    presets = [
        ("Agile  · Drop",   "Agile",  30,  1),
        ("Agile  · Flat",   "Agile",  100, 1),
        ("Agile  · Growth", "Agile",  300, 5),
        ("Medium · Drop",   "Medium", 30,  1),
        ("Medium · Flat",   "Medium", 100, 1),
        ("Medium · Growth", "Medium", 300, 5),
        ("Push   · Drop",   "Push",   30,  1),
        ("Push   · Flat",   "Push",   100, 1),
        ("Push   · Growth", "Push",   300, 5),
    ]
    rows = []
    for label, lt_name, lin_end, lin_wks in presets:
        lt = LT_PROFILES[lt_name]
        dist = STOCK_DIST_OPERATIONAL[lt_name]
        cov = lt["mat_lt"] + lt["semi_lt"] + lt["fp_lt"] + lt["dist_lt"] + lt["order_freq"]
        stock = min(100 * cov, 10000)
        rows.append({
            BATCH_INPUT_COLS[0]:  label,
            BATCH_INPUT_COLS[1]:  26,
            BATCH_INPUT_COLS[2]:  50,        # N stores (default mid-range)
            BATCH_INPUT_COLS[3]:  lt["mat_lt"],
            BATCH_INPUT_COLS[4]:  lt["semi_lt"],
            BATCH_INPUT_COLS[5]:  lt["fp_lt"],
            BATCH_INPUT_COLS[6]:  lt["dist_lt"],
            BATCH_INPUT_COLS[7]:  lt["order_freq"],
            BATCH_INPUT_COLS[8]:  stock,
            BATCH_INPUT_COLS[9]:  dist["store_pct"],
            BATCH_INPUT_COLS[10]: dist["wh_pct"],
            BATCH_INPUT_COLS[11]: dist["semi_pct"],
            BATCH_INPUT_COLS[12]: True,
            BATCH_INPUT_COLS[13]: "Linear",
            BATCH_INPUT_COLS[14]: lin_end,
            BATCH_INPUT_COLS[15]: lin_wks,
            BATCH_INPUT_COLS[16]: "Steep",
            BATCH_INPUT_COLS[17]: 100,
            BATCH_INPUT_COLS[18]: 100,
            BATCH_INPUT_COLS[19]: 20,
            BATCH_INPUT_COLS[20]: 1000,
            BATCH_INPUT_COLS[21]: 200,
            BATCH_INPUT_COLS[22]: 45,
        })
    return pd.DataFrame(rows)


def _run_one_scenario(row):
    """Run one scenario row through the engine and return a dict of outputs."""
    w           = int(row[BATCH_INPUT_COLS[1]])
    n_stores    = int(row[BATCH_INPUT_COLS[2]])
    mat_lt      = int(row[BATCH_INPUT_COLS[3]])
    semi_lt     = int(row[BATCH_INPUT_COLS[4]])
    fp_lt       = int(row[BATCH_INPUT_COLS[5]])
    dist_lt     = int(row[BATCH_INPUT_COLS[6]])
    freq        = int(row[BATCH_INPUT_COLS[7]])
    total_stock = int(row[BATCH_INPUT_COLS[8]])
    sp          = int(row[BATCH_INPUT_COLS[9]])
    wp          = int(row[BATCH_INPUT_COLS[10]])
    sep         = int(row[BATCH_INPUT_COLS[11]])
    smart       = bool(row[BATCH_INPUT_COLS[12]])
    shape       = str(row[BATCH_INPUT_COLS[13]])
    lin_end     = int(row[BATCH_INPUT_COLS[14]])
    lin_wks     = int(row[BATCH_INPUT_COLS[15]])
    seas_sub    = str(row[BATCH_INPUT_COLS[16]])
    seas_avg    = int(row[BATCH_INPUT_COLS[17]])
    cap_start   = int(row[BATCH_INPUT_COLS[18]])
    cap_ramp    = float(row[BATCH_INPUT_COLS[19]]) / 100.0
    price       = int(row[BATCH_INPUT_COLS[20]])
    var_cost    = int(row[BATCH_INPUT_COLS[21]])
    fixed_pct   = float(row[BATCH_INPUT_COLS[22]]) / 100.0

    init_store  = int(round(total_stock * sp / 100))
    init_cw     = int(round(total_stock * wp / 100))
    init_semi   = int(round(total_stock * sep / 100))
    init_raw    = total_stock - init_store - init_cw - init_semi

    if "Seasonal" in shape:
        cd = seasonal_curve(w, seas_sub, seas_avg)
        pc = [0.0] + list(seasonal_curve_float(w, seas_sub, BASE_FORECAST))
    else:
        cd = build_demand_curve(DEMAND_SHAPES[0], w, base=BASE_FORECAST,
                                lin_end=lin_end, lin_wks=lin_wks)[1:]
        pc = None

    states = run_simulation(
        weeks=w, init_store=init_store, init_cw=init_cw,
        init_semi=init_semi, init_rawmat=init_raw,
        order_freq=freq, mat_lt=mat_lt, semi_lt=semi_lt,
        fp_lt=fp_lt, dist_lt=dist_lt,
        cap_start=cap_start, cap_ramp=cap_ramp, base_forecast=BASE_FORECAST,
        price=price, var_cost=var_cost, fixed_pct=fixed_pct,
        n_stores=n_stores, rng_seed=FIXED_RNG_SEED, smart_distrib=smart,
        debug=False,
        custom_demand=tuple([0] + cd),
        planner_curve=tuple(pc) if pc is not None else None,
    )
    k = compute_kpis(states[1:], price, var_cost, fixed_pct, BASE_FORECAST, w,
                     init_store, init_cw, init_semi, init_raw, debug=False)
    reconciled = abs(k["pl_residual"]) < 1.0
    return {
        BATCH_OUTPUT_COLS[0]:  round(k["revenue"]),
        BATCH_OUTPUT_COLS[1]:  round(k["total_sales"]),
        BATCH_OUTPUT_COLS[2]:  round(k["svc_level"] * 100, 1),
        BATCH_OUTPUT_COLS[3]:  round(k["total_missed"]),
        BATCH_OUTPUT_COLS[4]:  k.get("stores_w_stockout_total", 0),
        BATCH_OUTPUT_COLS[5]:  k["stockout_weeks"],
        BATCH_OUTPUT_COLS[6]:  round(k["init_stock_value"]),
        BATCH_OUTPUT_COLS[7]:  round(k["var_cost"]),
        BATCH_OUTPUT_COLS[8]:  round(k["fixed"]),
        BATCH_OUTPUT_COLS[9]:  round(k["margin"]),
        BATCH_OUTPUT_COLS[10]: round(k["margin_pct"] * 100, 1),
        BATCH_OUTPUT_COLS[11]: round(k["end_stock_units"] + k["end_pipe_units"]),
        BATCH_OUTPUT_COLS[12]: round(k["leftover_value"]),
        BATCH_OUTPUT_COLS[13]: round(k["useful_pct"], 1),
        BATCH_OUTPUT_COLS[14]: "✓" if reconciled else "✗",
    }


def render_batch_ui():
    """Main page when app_mode == 'Batch Comparison'."""
    st.markdown("# 📊 Batch Scenario Comparison")
    st.caption("Edit one scenario per row, then click **Run all scenarios**. "
               "Outputs are appended as columns. Export the whole table as CSV; "
               "paste it back into the box at the bottom to restore a saved batch.")

    # --- Initialize the table ---
    if "batch_df" not in st.session_state:
        st.session_state.batch_df = _batch_default_rows()

    # --- Column config for type-aware editing ---
    NumberCol = st.column_config.NumberColumn
    SelectCol = st.column_config.SelectboxColumn
    CheckCol  = st.column_config.CheckboxColumn
    TextCol   = st.column_config.TextColumn
    col_config = {
        BATCH_INPUT_COLS[0]:  TextCol(width="medium", pinned=True),
        BATCH_INPUT_COLS[1]:  NumberCol(min_value=13, max_value=52, step=1),
        BATCH_INPUT_COLS[2]:  NumberCol(min_value=2, max_value=500, step=1),     # N stores
        BATCH_INPUT_COLS[3]:  NumberCol(min_value=0, max_value=10000, step=1),    # Seed
        BATCH_INPUT_COLS[4]:  NumberCol(min_value=1, max_value=24, step=1),       # Material LT
        BATCH_INPUT_COLS[5]:  NumberCol(min_value=1, max_value=12, step=1),
        BATCH_INPUT_COLS[6]:  NumberCol(min_value=1, max_value=12, step=1),
        BATCH_INPUT_COLS[7]:  NumberCol(min_value=1, max_value=12, step=1),
        BATCH_INPUT_COLS[8]:  NumberCol(min_value=1, max_value=4, step=1),
        BATCH_INPUT_COLS[9]:  NumberCol(min_value=0, max_value=50000, step=50),
        BATCH_INPUT_COLS[10]: NumberCol(min_value=0, max_value=100, step=5),
        BATCH_INPUT_COLS[11]: NumberCol(min_value=0, max_value=100, step=5),
        BATCH_INPUT_COLS[12]: NumberCol(min_value=0, max_value=100, step=5),
        BATCH_INPUT_COLS[13]: CheckCol(),
        BATCH_INPUT_COLS[14]: SelectCol(options=["Linear", "Seasonal"]),
        BATCH_INPUT_COLS[15]: NumberCol(min_value=0, max_value=1000, step=10),
        BATCH_INPUT_COLS[16]: NumberCol(min_value=1, max_value=52, step=1),
        BATCH_INPUT_COLS[17]: SelectCol(options=["Very Steep", "Steep", "~Flat"]),
        BATCH_INPUT_COLS[18]: NumberCol(min_value=0, max_value=1000, step=10),
        BATCH_INPUT_COLS[19]: NumberCol(min_value=10, max_value=1000, step=10),
        BATCH_INPUT_COLS[20]: NumberCol(min_value=0, max_value=50, step=5),
        BATCH_INPUT_COLS[21]: NumberCol(min_value=100, max_value=10000, step=100),
        BATCH_INPUT_COLS[22]: NumberCol(min_value=10, max_value=5000, step=10),
        BATCH_INPUT_COLS[23]: NumberCol(min_value=0, max_value=100, step=5),
    }
    # Mark output columns read-only with formatting
    for c in BATCH_OUTPUT_COLS:
        if "✓" in c:
            col_config[c] = TextCol(disabled=True)
        else:
            col_config[c] = NumberCol(disabled=True)

    # --- Helpers: pull the latest edited dataframe out of the data_editor's
    #     internal state without mutating the source. This is what avoids
    #     the "scroll jumps to the left and edit is lost" issue: by NOT
    #     writing `edited` back into st.session_state.batch_df on every
    #     keystroke, the source DataFrame stays stable and the editor's
    #     scroll/cursor position is preserved across reruns.
    def _apply_editor_state(source_df):
        """Return source_df with the editor's pending edits applied (adds,
        edits, deletes), using the diff dict stored at
        st.session_state['batch_editor']."""
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
        label_col = BATCH_INPUT_COLS[0]
        new_row[label_col] = str(new_row[label_col]) + " (copy)"
        st.session_state.batch_df = pd.concat(
            [current, pd.DataFrame([new_row])], ignore_index=True
        )
        st.session_state.pop("batch_editor", None)

    # --- Top action buttons ---
    b1, b2, b3 = st.columns([1, 1, 1])
    b1.button("🔄 Reset to 9-preset baseline", use_container_width=True, on_click=_reset_batch)
    b2.button("➕ Duplicate last row", use_container_width=True, on_click=_duplicate_last_row)
    run_clicked = b3.button("▶️ Run all scenarios", use_container_width=True, type="primary")

    # --- Editable table ---
    # Strip output columns from the editor view (they're filled by Run only).
    # Critical: do NOT write `edited` back to batch_df. The editor's `key`
    # parameter persists edits in st.session_state["batch_editor"] across
    # reruns; we only commit them back to batch_df on explicit user actions
    # (Reset, Duplicate, Run, Paste-back).
    input_only_df = st.session_state.batch_df[BATCH_INPUT_COLS].copy()
    edited = st.data_editor(
        input_only_df,
        column_config={k: v for k, v in col_config.items() if k in BATCH_INPUT_COLS},
        num_rows="dynamic",
        use_container_width=True,
        height=min(600, 40 + len(input_only_df) * 35),
        key="batch_editor",
    )

    # --- Run ---
    if run_clicked:
        results = []
        progress = st.progress(0.0, text="Running scenarios…")
        total = len(edited)
        for i, (_, row) in enumerate(edited.iterrows()):
            try:
                out = _run_one_scenario(row)
            except Exception as e:
                out = {c: ("FAIL" if "✓" in c else None) for c in BATCH_OUTPUT_COLS}
                out[BATCH_OUTPUT_COLS[15]] = f"✗ {str(e)[:30]}"
            results.append(out)
            progress.progress((i + 1) / max(total, 1), text=f"Running scenario {i+1}/{total}…")
        progress.empty()
        results_df = pd.DataFrame(results)
        st.session_state.batch_results = pd.concat([edited.reset_index(drop=True),
                                                     results_df.reset_index(drop=True)], axis=1)

    # --- Results display + export ---
    if "batch_results" in st.session_state:
        st.markdown("### Results")
        st.dataframe(
            st.session_state.batch_results,
            use_container_width=True,
            height=min(600, 40 + len(st.session_state.batch_results) * 35),
            column_config=col_config,
        )

        # XLSX export (inputs + outputs together — fully reproducible)
        xlsx_buf = io.BytesIO()
        with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as xw:
            st.session_state.batch_results.to_excel(xw, index=False, sheet_name="Batch")
        st.download_button(
            "📥 Download results as Excel (inputs + outputs)",
            data=xlsx_buf.getvalue(),
            file_name=f"sc_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # --- Upload-back loader (Excel) ---
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
                st.success(f"Loaded {len(loaded)} scenarios. Click 'Run all scenarios' to compute outputs.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to parse Excel file: {e}")


# ════════════════════════════════════════════════════════════════
# SIDEBAR UI
# ════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## ⚙️ Supply Chain Setup")
    app_mode = st.radio("App mode", ["Single Scenario", "Batch Comparison"],
                        horizontal=True, key="app_mode",
                        help="Single = the full week-by-week simulator. "
                             "Batch = a table of scenarios run in parallel.")
    if app_mode == "Batch Comparison":
        st.caption("📊 Edit per-scenario parameters in the main table. The widgets below are unused in this mode (collapse the sidebar with the « arrow if it bothers you).")
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
        lin_end=st.session_state.get("lin_end", 100),
        lin_wks=st.session_state.get("lin_wks", 1),
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

    # Lifetime production cap: initial stock (already in the chain at W0)
    # + every supplier order placed over the simulation must never exceed
    # this. Cannot be set below `total_stock` — the chain already has that
    # much "produced" at W0.
    prod_cap_min = max(10, int(total_stock))
    _prev_cap = st.session_state.get("prod_cap", prod_cap_min)
    if _prev_cap < prod_cap_min:
        st.session_state["prod_cap"] = prod_cap_min
    prod_cap = st.slider(
        "Max total products (lifetime)",
        min_value=prod_cap_min, max_value=10000, step=50,
        key="prod_cap",
        help="Cumulative production budget over the whole simulation. "
             "Counts initial stock (already in the chain at W0) plus "
             "every unit the supplier ships afterwards. Once exhausted, "
             "supplier orders are forced to 0. Cannot be less than the "
             "initial stock above.",
    )

    st.caption("Distribution (% of total):")
    # Guard against stale percentages summing > 100 after a preset switch
    _sp  = st.session_state.get("store_pct", 40)
    _wp  = st.session_state.get("wh_pct", 20)
    _sep = st.session_state.get("semi_pct", 10)
    if _wp > 100 - _sp:
        st.session_state["wh_pct"] = max(0, 100 - _sp)
    if _sep > 100 - _sp - st.session_state.get("wh_pct", 0):
        st.session_state["semi_pct"] = max(0, 100 - _sp - st.session_state.get("wh_pct", 0))

    # Layout exactly mirrors the Lead Times 2×2 grid so the four labels
    # line up cell-for-cell:
    #     Material  (auto = 100 − others)  |  Finishing  (input)
    #     Semi-Fin  (input)                 |  Store      (input)
    # Material % is rendered into a TOP-of-column-1 placeholder LAST so it
    # reflects the latest values of the other three inputs every render.
    sc1, sc2 = st.columns(2)
    material_slot = sc1.empty()                                            # top-left
    finishing_pct = sc2.number_input(                                       # top-right
        "Finishing %", min_value=0, max_value=100, step=5, key="wh_pct",
        help="% of total initial stock held in the Finishing-stage buffer (CW = central warehouse).",
    )
    semi_pct = sc1.number_input(                                            # bottom-left
        "Semi-Fin %", min_value=0, max_value=max(0, 100 - finishing_pct), step=5,
        key="semi_pct",
    )
    store_pct = sc2.number_input(                                           # bottom-right
        "Store %", min_value=0,
        max_value=max(0, 100 - finishing_pct - semi_pct), step=5, key="store_pct",
        help="% of total initial stock pre-positioned at the two stores (always 50/50 between A and B).",
    )
    material_pct = max(0, 100 - finishing_pct - semi_pct - store_pct)
    # Restyled to closely match the look of a Streamlit number_input:
    # same border, padding, font size, no bold. Just read-only.
    material_slot.markdown(
        f'<div style="font-size:14px;color:rgb(38,39,48);margin-bottom:0.25rem;">'
        f'Material %  <span style="color:#a8b4c4;font-size:11px;">(= 100 − others)</span></div>'
        f'<div style="border:1px solid rgba(49,51,63,0.2);border-radius:0.5rem;'
        f'padding:0.45rem 0.75rem;font-size:14px;color:rgb(38,39,48);background:#fafbfc;">'
        f'{material_pct}</div>',
        unsafe_allow_html=True,
    )

    # Engine-side variables (names unchanged to preserve all internal refs)
    warehouse_pct = finishing_pct          # legacy alias
    rawmat_pct    = material_pct

    init_store  = int(round(total_stock * store_pct / 100))
    init_cw     = int(round(total_stock * finishing_pct / 100))
    init_semi   = int(round(total_stock * semi_pct / 100))
    init_rawmat = total_stock - init_store - init_cw - init_semi

    st.markdown(
        f'<div style="background:#f0f2f5;border-radius:8px;padding:8px 12px;font-size:13px;line-height:1.8;">'
        f'<b>Material:</b> {init_rawmat} ({material_pct}%) @ 50% | '
        f'<b>Semi-Fin:</b> {init_semi} ({semi_pct}%) @ 75% | '
        f'<b>Finishing:</b> {init_cw} ({finishing_pct}%) @ 100% | '
        f'<b>Store:</b> {init_store} ({store_pct}%) @ 100% <i>(always 50/50 initial)</i>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # --- Store network ---
    st.markdown("### \U0001f3ea Store Network")
    n_stores = st.slider("Number of stores",
                         min_value=10, max_value=500, step=10,
                         key="n_stores")
    rng_seed = FIXED_RNG_SEED      # fixed — same draws every run
    smart_distrib = st.toggle("Smart Distribution (need-based)", key="smart_distrib")
    st.caption(
        f"**{n_stores} stores** — mix is "
        f"**10% high-selling** ({TIER_WEIGHTS['high']:.1f}× avg, carries ~40% of sales), "
        f"**30% medium** ({TIER_WEIGHTS['medium']:.1f}× avg, ~30%), "
        f"**60% small** ({TIER_WEIGHTS['small']:.1f}× avg, ~30%, often 0 sales/wk). "
        f"Same store keeps its bucket all simulation."
    )
    st.caption(
        f"The planner *learns* each store's sell rate from observed demand "
        f"(EMA, α={PS_RATE_ALPHA}). Both modes use the learned rates; they "
        f"differ in **how** stock is allocated."
    )
    if smart_distrib:
        st.caption("**Smart**: water-fills each week to equalise weeks-of-cover "
                   "across stores (closed-loop, reacts to current per-store stock).")
    else:
        st.caption("**Push**: ships tier-proportional shares using the learned "
                   "rates (open-loop, blind to current per-store stock).")

    # --- Demand profile ---
    st.markdown("### \U0001f4c8 Demand Profile")
    preset_shape = st.radio("Demand family", DEMAND_SHAPES, key="demand_shape", horizontal=True)
    bf = BASE_FORECAST
    demand_description = ""

    if "Linear" in preset_shape:
        st.caption(f"Start always at {bf}/wk. Adjust the final value (set = {bf} for flat).")
        end_dem = st.slider("Final demand (pcs/wk)", min_value=0, max_value=1000, step=10, key="lin_end")
        trans_wks = st.slider("Transition duration (weeks)", min_value=1, max_value=weeks, step=1, key="lin_wks")
        init_demand = build_demand_curve(preset_shape, weeks, base=bf, lin_end=end_dem, lin_wks=trans_wks)
        if end_dem == bf:
            demand_description = f"Flat {bf}/wk for {weeks} wks"
        elif end_dem > bf:
            demand_description = f"Ramp {bf}→{end_dem} in {trans_wks}wk"
        else:
            demand_description = f"Drop {bf}→{end_dem} in {trans_wks}wk"

    else:  # Seasonal
        seas_sub = st.radio("Curve shape", ["Very Steep", "Steep", "~Flat"],
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

    # Seasonal planner curve: same shape, but UNROUNDED floats scaled to
    # avg=base_forecast. The float form avoids integer rounding drift, so
    # the discovered factor is a clean ratio (e.g., exactly 3.0× for the
    # avg=300 preset vs. the planner's avg=100 belief).
    if "Seasonal" in preset_shape:
        planner_curve = [0.0] + list(seasonal_curve_float(weeks, st.session_state.get("seas_sub", "Steep"), BASE_FORECAST))
    else:
        planner_curve = None

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

    # No engine toggles in v3 — kickstart is superseded by the per-stage
    # push policy, and reconciliation runs silently every simulation.
    debug_mode = False

    # --- Show-your-work toggle: spells out the per-week math under the diagram ---
    st.markdown("---")
    show_calcs = st.toggle("🧪 Show calculations under diagram",
                           key="show_calcs",
                           help="When ON, an expander appears under the supply-chain "
                                "diagram with the explicit math for the current week — "
                                "forecast, per-stage targets, capacity, processing, costs.")

    # --- Quick scenarios: permanent grid (3 LT × 3 demand) ---
    st.markdown("---")
    st.markdown("### \U0001f3af Quick Scenarios — Permanent")
    st.caption("3 LT × 3 Demand · stock auto-sized to coverage·100 · "
               "Agile 60/20/10/10, Medium 80/20/0/0, Push 100/0/0/0 · A=60%, smart ON")

    h1, h2, h3, h4 = st.columns([1.2, 1, 1, 1])
    with h2: st.markdown("**📉 Drop →30**")
    with h3: st.markdown("**⚪ Flat 100**")
    with h4: st.markdown("**📈 Growth →300**")

    a1, a2, a3, a4 = st.columns([1.2, 1, 1, 1])
    with a1: st.markdown("\U0001f7e2 **Agile**\n\n*LT=8, f=1*")
    with a2: st.button("📉", key="p_ad", use_container_width=True, on_click=apply_preset, args=("Agile", "Drop"))
    with a3: st.button("⚪", key="p_af", use_container_width=True, on_click=apply_preset, args=("Agile", "Flat"))
    with a4: st.button("📈", key="p_ag", use_container_width=True, on_click=apply_preset, args=("Agile", "Growth"))

    m1, m2, m3, m4 = st.columns([1.2, 1, 1, 1])
    with m1: st.markdown("\U0001f7e1 **Medium**\n\n*LT=16, f=2*")
    with m2: st.button("📉", key="p_md", use_container_width=True, on_click=apply_preset, args=("Medium", "Drop"))
    with m3: st.button("⚪", key="p_mf", use_container_width=True, on_click=apply_preset, args=("Medium", "Flat"))
    with m4: st.button("📈", key="p_mg", use_container_width=True, on_click=apply_preset, args=("Medium", "Growth"))

    p1, p2, p3, p4 = st.columns([1.2, 1, 1, 1])
    with p1: st.markdown("\U0001f534 **Push**\n\n*LT=24, f=4*")
    with p2: st.button("📉", key="p_pd", use_container_width=True, on_click=apply_preset, args=("Push", "Drop"))
    with p3: st.button("⚪", key="p_pf", use_container_width=True, on_click=apply_preset, args=("Push", "Flat"))
    with p4: st.button("📈", key="p_pg", use_container_width=True, on_click=apply_preset, args=("Push", "Growth"))

    # --- Quick scenarios: seasonal grid (3 LT × 3 seasonal averages, all Steep) ---
    st.markdown("### \U0001f30a Quick Scenarios — Seasonal (Steep)")
    st.caption("3 LT × 3 averages (30 / 100 / 300) · Steep gamma curve · "
               "**stock sized at avg=100/wk recommendation (constant across the 3 averages) "
               "÷ sell-through, rounded up to 50** — keeps the comparison apples-to-apples · "
               "Sell-through: Agile 100% → ~1650 pcs, Medium 85% → ~2950, Push 60% → ~4350 · "
               "Distributions: Agile 40/20/10/30, Medium 70/20/10/0, Push 100/0/0/0 · A=60%, smart ON")

    sh1, sh2, sh3, sh4 = st.columns([1.2, 1, 1, 1])
    with sh2: st.markdown("**📉 Avg 30**")
    with sh3: st.markdown("**⚪ Avg 100**")
    with sh4: st.markdown("**📈 Avg 300**")

    sa1, sa2, sa3, sa4 = st.columns([1.2, 1, 1, 1])
    with sa1: st.markdown("\U0001f7e2 **Agile**")
    with sa2: st.button("📉", key="ps_a30",  use_container_width=True, on_click=apply_preset,
                        args=("Agile", "Seasonal"), kwargs={"seas_avg": 30,  "seas_sub": "Steep"})
    with sa3: st.button("⚪", key="ps_a100", use_container_width=True, on_click=apply_preset,
                        args=("Agile", "Seasonal"), kwargs={"seas_avg": 100, "seas_sub": "Steep"})
    with sa4: st.button("📈", key="ps_a300", use_container_width=True, on_click=apply_preset,
                        args=("Agile", "Seasonal"), kwargs={"seas_avg": 300, "seas_sub": "Steep"})

    sm1, sm2, sm3, sm4 = st.columns([1.2, 1, 1, 1])
    with sm1: st.markdown("\U0001f7e1 **Medium**")
    with sm2: st.button("📉", key="ps_m30",  use_container_width=True, on_click=apply_preset,
                        args=("Medium", "Seasonal"), kwargs={"seas_avg": 30,  "seas_sub": "Steep"})
    with sm3: st.button("⚪", key="ps_m100", use_container_width=True, on_click=apply_preset,
                        args=("Medium", "Seasonal"), kwargs={"seas_avg": 100, "seas_sub": "Steep"})
    with sm4: st.button("📈", key="ps_m300", use_container_width=True, on_click=apply_preset,
                        args=("Medium", "Seasonal"), kwargs={"seas_avg": 300, "seas_sub": "Steep"})

    sp1, sp2, sp3, sp4 = st.columns([1.2, 1, 1, 1])
    with sp1: st.markdown("\U0001f534 **Push**")
    with sp2: st.button("📉", key="ps_p30",  use_container_width=True, on_click=apply_preset,
                        args=("Push", "Seasonal"), kwargs={"seas_avg": 30,  "seas_sub": "Steep"})
    with sp3: st.button("⚪", key="ps_p100", use_container_width=True, on_click=apply_preset,
                        args=("Push", "Seasonal"), kwargs={"seas_avg": 100, "seas_sub": "Steep"})
    with sp4: st.button("📈", key="ps_p300", use_container_width=True, on_click=apply_preset,
                        args=("Push", "Seasonal"), kwargs={"seas_avg": 300, "seas_sub": "Steep"})

    st.caption(
        "**Per-stage push** — each stage (supplier, Semi, FP, CW→stores) has "
        "its own (R, S) decision at every review week. Initial WIP flows whenever "
        "its downstream is below target, with no kickstart override needed. "
        "**Seasonal mode** additionally activates a shape-aware lookahead: the "
        "planner knows the curve shape but assumes avg=100/wk until the first review "
        "reveals the true scale."
    )


# ════════════════════════════════════════════════════════════════
# MAIN PAGE UI
# ════════════════════════════════════════════════════════════════

import altair as alt

# --- Batch Comparison mode short-circuit ---
# If user chose Batch in the sidebar, render the batch UI and stop here.
# Single-scenario rendering (everything below) is bypassed via st.stop().
if app_mode == "Batch Comparison":
    render_batch_ui()  # defined below, just before this block
    st.stop()

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
    'n_stores': n_stores, 'rng_seed': FIXED_RNG_SEED, 'smart_distrib': smart_distrib,
    'debug': debug_mode,
    'custom_demand':  tuple(custom_demand),
    'planner_curve':  tuple(planner_curve) if planner_curve is not None else None,
    'prod_cap':       int(prod_cap),
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
st.markdown("# \U0001f3ed Supply Chain Agility Simulator — Advanced Stage Ordering")
distrib_mode = "Smart" if smart_distrib else "Push 50/50"
st.markdown(
    f"*LT = **{phys_lt}**wk | Coverage = **{coverage}**wk | "
    f"Demand: **{demand_description}** | **{n_stores}** stores | "
    f"{distrib_mode} · per-stage push policy at each review week*"
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


k1, k2, k3, k4, k5, k6 = st.columns(6)
with k1:
    svc = cum['svc_level']
    c = "#c0392b" if svc < 0.6 else ("#d4850a" if svc < 0.85 else "#1a8a4a")
    st.markdown(_kpi_card("Service Level", f"{svc*100:.1f}%", c), unsafe_allow_html=True)
with k2:
    st.markdown(_kpi_card("Cumul. Sales",   f"{round(cum['sales'], -1):,.0f}", "#2c5f8a"), unsafe_allow_html=True)
with k3:
    st.markdown(_kpi_card("Missed Total",   f"{round(cum['missed'], -1):,.0f}", "#c0392b"), unsafe_allow_html=True)
with k4:
    # Stores-with-stockout: weekly events, summed up to current week (so the
    # same store stocking out in 2 weeks counts twice). Useful as a relative
    # stress indicator that scales with N.
    sw = cum.get('stores_w_stockout', 0)
    sw_clr = "#c0392b" if sw > 0 else "#1a8a4a"
    st.markdown(_kpi_card("Store-Stockout Events", f"{sw:,}", sw_clr), unsafe_allow_html=True)
with k5:
    sc_clr = "#c0392b" if cum['stockout_wks'] > 0 else "#1a8a4a"
    st.markdown(_kpi_card("Stockout Wks",   f"{cum['stockout_wks']}/{week}", sc_clr), unsafe_allow_html=True)
with k6:
    uf = cum['useful_pct']
    uc = "#1a8a4a" if uf > 80 else ("#d4850a" if uf > 50 else "#c0392b")
    st.markdown(_kpi_card("Useful Prod.",   f"{uf:.0f}%", uc), unsafe_allow_html=True)


# --- SC flow diagram (exactly one st.components.v1.html call) ---
st.markdown("")
_max_stage = max(params['mat_lt'], params['semi_lt'], params['fp_lt'], params['dist_lt'])
_rows_needed = math.ceil(_max_stage / MAX_PER_ROW)
_stage_h  = _rows_needed * 145
_stores_h = 118 + 28            # 1 aggregated store card + header; sized so LOST badge doesn't clip
_content_h = max(_stage_h, _stores_h)
_viz_h = 48 + 16 + _content_h + 28 + 32   # info bar + pad + content + phys flow + comment

# One-time planner-factor message: shown on the week the factor is locked
# (i.e., the first review week in seasonal mode) and remains visible
# afterwards as a small chip.
_pf = state.get('planner_factor')
if _pf is not None:
    _badge_bg = '#fff5d6' if state.get('planner_factor_locked') else '#f0f2f5'
    _badge_border = '#d4a018' if state.get('planner_factor_locked') else '#dde3ed'
    _badge_msg = ("⚡ Planner realizes factor of " if state.get('planner_factor_locked')
                  else "Planner factor (locked): ")
    # Display: use 3 significant figures and strip trailing zeros so a snapped
    # integer factor (3.0) shows as "3×" and an off-integer factor (2.85)
    # shows as "2.85×". Engine already snaps factors near integers, so this
    # is mostly a safety belt for non-preset cases.
    _pf_str = f"{_pf:.3g}×"
    st.markdown(
        f'<div style="background:{_badge_bg};border:1px solid {_badge_border};'
        f'border-radius:8px;padding:8px 16px;margin-bottom:8px;font-size:14px;'
        f'color:#1a2a40;"><b>{_badge_msg}{_pf_str}</b> '
        f'<span style="color:#5a6a7e;font-size:12px;">— planner started from a shape × '
        f'{BASE_FORECAST}/wk default; after the first review, all seasonal targets are scaled '
        f'by this factor.</span></div>',
        unsafe_allow_html=True,
    )

st.components.v1.html(make_sc_html(state, params), height=_viz_h, scrolling=False)


# --- Per-store zoom — session-state-controlled toggle (doesn't close on
#     week navigation). Falls back to closed-by-default the first time.
zoom_open = st.toggle(
    f"🔍 Per-store zoom — {n_stores} stores",
    key="ps_zoom_open",
)
if zoom_open:
    stores_arr = state.get('stores', [])
    per_dem    = state.get('per_store_dem', [])
    per_sales  = state.get('per_store_sales', [])
    per_missed = state.get('per_store_missed', [])
    allocs_arr = state.get('allocs', [])
    dist_pipes_arr = state.get('dist_pipes', [])
    dist_per_store = [sum(dp) for dp in dist_pipes_arr] if dist_pipes_arr else [0] * n_stores
    # Tier labels are stored once in W0 — same for every week.
    tier_labels = states[0].get('tier_labels', ['medium'] * len(stores_arr))

    if not stores_arr:
        st.info("No per-store data for this week.")
    else:
        n_small    = tier_labels.count('small')
        n_medium   = tier_labels.count('medium')
        n_high_lbl = tier_labels.count('high')
        t1, t2, t3 = st.columns(3)
        t1.metric(f"High-Selling ({TIER_WEIGHTS['high']:.1f}× rate)", n_high_lbl)
        t2.metric(f"Medium ({TIER_WEIGHTS['medium']:.1f}× rate)",     n_medium)
        t3.metric(f"Small ({TIER_WEIGHTS['small']:.1f}× rate)",       n_small)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric(f"Min stock (W{state['week']})",  f"{min(stores_arr):.0f}")
        m2.metric("Avg stock",  f"{sum(stores_arr)/len(stores_arr):.1f}")
        m3.metric("Max stock",  f"{max(stores_arr):.0f}")
        m4.metric("Std",        f"{float(np.std(stores_arr)):.2f}")
        m5.metric("Stockout stores (this wk)",
                  f"{state.get('stores_w_stockout', 0)} / {n_stores}")

        # --- Sales matrix: one row per store, one column per week ---
        st.markdown("**Sales matrix — every store, every week**")
        st.caption(
            "🟢 **Sold** (had stock, demand met) · "
            "🔴 **Missed** (demand but stockout) · "
            "🟡 **Held** (had stock, no demand — paid carrying cost for nothing) · "
            "⚪ **Idle** (no stock, no demand). "
            "Stores are numbered 1..N from high-selling (top of chart) to small (bottom)."
        )

        # Build long-format frame across all simulated weeks.
        # Held = had stock at week-end but no demand this week (the lesson:
        # carrying-cost wasted). Idle = no stock and no demand (empty).
        rows_data = []
        for w_idx in range(1, len(states)):
            st_obj = states[w_idx]
            pd_arr = st_obj.get('per_store_dem', [])
            ps_arr = st_obj.get('per_store_sales', [])
            pm_arr = st_obj.get('per_store_missed', [])
            stk_arr = st_obj.get('stores', [])
            for i in range(n_stores):
                dem    = pd_arr[i]  if i < len(pd_arr)  else 0
                sales  = ps_arr[i]  if i < len(ps_arr)  else 0
                missed = pm_arr[i]  if i < len(pm_arr)  else 0
                stk    = stk_arr[i] if i < len(stk_arr) else 0
                # Any positive end-of-week stock means the store is HELD,
                # not Idle. Stocks are rounded to 1 dp upstream so we use
                # a tight epsilon — 0.05 catches everything ≥ 0.1.
                if missed > 0.5:
                    s_lbl = "Missed"
                elif dem > 0.5:
                    s_lbl = "Sold"
                elif stk > 0.05:
                    s_lbl = "Held"
                else:
                    s_lbl = "Idle"
                rows_data.append({
                    'store':  i + 1,
                    'tier':   tier_labels[i],
                    'week':   w_idx,
                    'state':  s_lbl,
                    'demand': dem,
                    'sales':  sales,
                    'missed': missed,
                    'stock_end': stk,
                })
        mat_df = pd.DataFrame(rows_data)

        # Cap displayed rows for very large N (proportional slice by tier
        # from the START of each tier block — stores are already in order).
        MAX_MATRIX_ROWS = 80
        if n_stores > MAX_MATRIX_ROWS:
            n_f_show = max(1, int(round(MAX_MATRIX_ROWS * TIER_SHARE['high'])))
            n_m_show = max(1, int(round(MAX_MATRIX_ROWS * TIER_SHARE['medium'])))
            n_s_show = MAX_MATRIX_ROWS - n_f_show - n_m_show
            high_ids   = [i+1 for i, t in enumerate(tier_labels) if t == 'high'][:n_f_show]
            medium_ids = [i+1 for i, t in enumerate(tier_labels) if t == 'medium'][:n_m_show]
            small_ids  = [i+1 for i, t in enumerate(tier_labels) if t == 'small'][:n_s_show]
            keep_ids = set(high_ids + medium_ids + small_ids)
            mat_df = mat_df[mat_df['store'].isin(keep_ids)]
            st.caption(
                f"_Showing {len(keep_ids)} of {n_stores} stores "
                f"({len(high_ids)} high-selling / {len(medium_ids)} medium / "
                f"{len(small_ids)} small) — first stores of each tier._"
            )

        # Stores are now numbered high-selling → medium → small (1..N), so
        # ordering by store id gives the right group sequence.
        unique_stores = sorted(mat_df['store'].unique())

        # Highlight the currently-selected week with a vertical rule.
        current_week_df = pd.DataFrame({'week': [state['week']]})

        # Altair max_rows limit can bite at N×W > 5000 cells.
        try:
            alt.data_transformers.disable_max_rows()
        except Exception:
            pass

        n_rows = len(unique_stores)
        row_h  = max(8, min(14, 700 // max(1, n_rows)))
        heatmap = (
            alt.Chart(mat_df)
              .mark_rect(stroke='white', strokeWidth=0.4)
              .encode(
                  x=alt.X('week:O', title='Week'),
                  y=alt.Y('store:O', sort=unique_stores,
                          title='Store — top: high-selling · bottom: small'),
                  color=alt.Color(
                      'state:N',
                      scale=alt.Scale(
                          domain=['Sold', 'Missed', 'Held', 'Idle'],
                          range=['#1a8a4a', '#c0392b', '#f1c40f', '#e8e8e8'],
                      ),
                      legend=alt.Legend(title='Week state', orient='top'),
                  ),
                  tooltip=[
                      alt.Tooltip('store:O',    title='Store'),
                      alt.Tooltip('tier:N',     title='Bucket'),
                      alt.Tooltip('week:O',     title='Week'),
                      alt.Tooltip('demand:Q',   title='Demand'),
                      alt.Tooltip('sales:Q',    title='Sales'),
                      alt.Tooltip('missed:Q',   title='Missed'),
                      alt.Tooltip('stock_end:Q',title='Stock end-of-wk'),
                      alt.Tooltip('state:N',    title='State'),
                  ],
              )
              .properties(height=max(180, row_h * n_rows))
        )
        current_rule = (
            alt.Chart(current_week_df)
              .mark_rule(color='#1a2a40', strokeWidth=2, opacity=0.6)
              .encode(x='week:O')
        )
        st.altair_chart(heatmap + current_rule, use_container_width=True)

        # --- Per-bucket synthesis for the currently-selected week ---
        st.markdown(f"**Per-bucket synthesis — W{state['week']}**")
        ps_rate_arr = state.get('ps_rate', [0.0] * n_stores)
        bucket_order = ['high', 'medium', 'small']
        bkt_rows = []
        for bkt in bucket_order:
            idx = [i for i, t in enumerate(tier_labels) if t == bkt]
            if not idx:
                continue
            n_b = len(idx)
            stk_sum  = float(sum(stores_arr[i]    for i in idx if i < len(stores_arr)))
            dem_sum  = float(sum(per_dem[i]       for i in idx if i < len(per_dem)))
            sale_sum = float(sum(per_sales[i]     for i in idx if i < len(per_sales)))
            miss_sum = float(sum(per_missed[i]    for i in idx if i < len(per_missed)))
            alloc_sum= float(sum(allocs_arr[i]    for i in idx if i < len(allocs_arr)))
            pipe_sum = float(sum(dist_per_store[i]for i in idx if i < len(dist_per_store)))
            rate_sum = float(sum(ps_rate_arr[i]   for i in idx if i < len(ps_rate_arr)))
            stockouts = sum(1 for i in idx
                            if i < len(per_missed) and per_missed[i] > 0.5)
            service = (sale_sum / dem_sum * 100.0) if dem_sum > 0.5 else 100.0
            bkt_rows.append({
                'Bucket':         bkt.capitalize(),
                'Stores':         n_b,
                'Learned rate (avg/store)': round(rate_sum / n_b, 2),
                'Stock end (sum)':         round(stk_sum),
                'Stock end (avg)':         round(stk_sum / n_b, 1),
                'Demand wk (sum)':         round(dem_sum),
                'Sales wk (sum)':          round(sale_sum),
                'Missed wk (sum)':         round(miss_sum),
                'Service %':               round(service, 1),
                'Stockout stores':         f"{stockouts} / {n_b}",
                'Allocated this wk':       round(alloc_sum),
                'In dist-pipe':            round(pipe_sum),
            })
        bkt_df = pd.DataFrame(bkt_rows)
        st.dataframe(bkt_df, use_container_width=True, hide_index=True,
                     height=40 + len(bkt_df) * 38)
        st.caption(
            "_Service % = sales / demand for the bucket this week. "
            "Watch high-selling stores vs. small: a healthy plan keeps service high "
            "for high-selling stores (where most of the revenue lives) without "
            "letting the small bucket pile up unused stock (Held cells)._"
        )


# --- Show-calculations expander (when toggle is ON) ---
if st.session_state.get("show_calcs", False):
    with st.expander(f"🧪 Calculations for week {state['week']} — show your work", expanded=True):
        s = state
        prev = states[week - 1] if week > 0 else None
        cov_sup_local  = phys_lt + order_freq
        cov_semi_local = semi_lt + fp_lt + dist_lt + order_freq
        cov_fp_local   = fp_lt + dist_lt + order_freq
        cov_ship_local = dist_lt + order_freq

        if s['week'] == 0:
            st.markdown(
                "**Week 0 is the initial state — no engine logic has run yet.** "
                "All stocks and buffers sit at their pre-configured values, the pipes "
                "(in-transit slots) are empty, the supplier has no backlog, and no "
                "sales have happened. The first sale, the first order, and the first "
                "processing all start at Week 1. Click `+1 ▶` to step into the live "
                "simulation."
            )
        else:
            # 1. DEMAND & SALES (aggregate; per-store breakdown is in the zoom expander)
            st.markdown("#### 1. Demand and store sales")
            arr_total = s['dist_arr']
            store_start_total = (prev['store_stock'] if prev else 0)
            avail_total = store_start_total + arr_total
            overall_state = "fully met" if s['missed'] < 0.5 else "partially met"
            stockout_n = s.get('stores_w_stockout', 0)
            n_high_lbl   = states[0].get('tier_labels', []).count('high')
            n_medium_lbl = states[0].get('tier_labels', []).count('medium')
            n_small_lbl  = states[0].get('tier_labels', []).count('small')
            st.markdown(
                f"This week's total customer demand is **{s['demand']:.0f} units**, "
                f"drawn stochastically (multinomial) across **{n_stores} stores** "
                f"split into **{n_high_lbl} high-selling** ({TIER_WEIGHTS['high']:.1f}× rate), "
                f"**{n_medium_lbl} medium** ({TIER_WEIGHTS['medium']:.1f}× rate), "
                f"**{n_small_lbl} small** ({TIER_WEIGHTS['small']:.1f}× rate). "
                f"Same store keeps its tier every week.\n\n"
                f"The stores collectively started the week with **{store_start_total:.0f} units**. "
                f"**{arr_total:.0f} units arrived** from the Distribution pipe this morning, "
                f"so the network had **{avail_total:.0f} units available** to sell against "
                f"the {s['demand']:.0f} units of demand.\n\n"
                f"Demand was {overall_state}: **{s['sales']:.0f} sold**, "
                f"**{s['missed']:.0f} missed**, ending the week at "
                f"**{s['store_stock']:.0f} units** across the network "
                f"(min {s.get('stores_min', 0):.0f}, max {s.get('stores_max', 0):.0f}, "
                f"avg {s.get('stores_mean', 0):.1f}, std {s.get('stores_std', 0):.1f}). "
                f"**{stockout_n}** of {n_stores} stores stocked out this week."
            )

            # 2. ARRIVALS → BUFFERS
            st.markdown("#### 2. Upstream-pipe arrivals into buffers")
            rm_prev = prev['raw_mat_stock'] if prev else 0
            sm_prev = prev['semi_stock'] if prev else 0
            cw_prev = prev['cw_stock'] if prev else 0
            st.markdown(
                f"Every pipe advances one slot per week, so this week's arrivals are "
                f"whatever sat at the very front of each pipe at the end of last week. "
                f"At the **Material → RM** boundary, **{s['mat_arr']:.0f} units arrived** "
                f"and flowed into the RM buffer (which therefore goes from "
                f"{rm_prev:.0f} to **{rm_prev + s['mat_arr']:.0f}** before any "
                f"processing happens). Similarly, **{s['semi_arr']:.0f} units arrived "
                f"at the Semi buffer** (now at {sm_prev + s['semi_arr']:.0f}), and "
                f"**{s['fp_arr']:.0f} units arrived at the CW buffer** (now at "
                f"{cw_prev + s['fp_arr']:.0f}). These freshly-arrived units are "
                f"available for the planner's review and the processing steps below."
            )

            # 3. PLANNER REVIEW (if review week)
            calc = s.get('planner_calc')
            if calc is not None:
                st.markdown(f"#### 3. Planner review — *this is a review week (every {order_freq} wk)*")
                pf = s.get('planner_factor')
                seasonal_mode = planner_curve is not None
                if seasonal_mode and pf is not None:
                    st.markdown(
                        f"Because the demand profile is **seasonal**, the planner knows "
                        f"the curve shape but had to discover the actual amplitude. The "
                        f"locked adjustment factor is **f = {pf:.3g}×**, meaning actual "
                        f"demand has turned out to be {pf:.3g} times what the planner "
                        f"initially assumed (avg = {BASE_FORECAST}/wk). Therefore each "
                        f"target below is computed as **Σ planner_curve[w+1 … w+cov_x] "
                        f"× {pf:.3g}** — the sum of the next cov_x weeks of the scaled "
                        f"seasonal curve."
                    )
                    # Concrete numerical derivation for the supplier target.
                    # We pick the supplier as the example because it has the
                    # longest lookahead and therefore is the most opaque to
                    # readers. The other 3 stages follow the same pattern with
                    # shorter windows.
                    pc = params.get('planner_curve')
                    if pc is not None:
                        rows = []
                        running = 0.0
                        end_wk = min(s['week'] + cov_sup_local, weeks)
                        for i in range(s['week'] + 1, s['week'] + cov_sup_local + 1):
                            base_val = pc[i] if (0 < i < len(pc)) else 0.0
                            scaled = base_val * pf
                            running += scaled
                            note = "  (past sim end → 0)" if i > weeks else ""
                            rows.append({
                                "Week":                       f"W{i}{note}",
                                "Planner's shape (avg=100)":  f"{base_val:.1f}",
                                f"× f = {pf:.3g}":            f"{scaled:.0f}",
                                "Running sum":                f"{running:.0f}",
                            })
                        st.markdown(
                            f"**Worked example for the supplier order at W{s['week']}.** "
                            f"Coverage `cov_sup = mat + semi + fp + dist + freq = "
                            f"{mat_lt}+{semi_lt}+{fp_lt}+{dist_lt}+{order_freq} = "
                            f"**{cov_sup_local} weeks**`. The lookahead window is "
                            f"**W{s['week']+1} → W{s['week']+cov_sup_local}**. "
                            f"The planner's shape was scaled to avg = {BASE_FORECAST}/wk at sim "
                            f"start; each value below is the shape × the discovered factor "
                            f"**f = {pf:.3g}**:"
                        )
                        st.table(pd.DataFrame(rows).set_index("Week"))
                        st.markdown(
                            f"⇒ **target_sup = {running:.0f}** units. The planner wants the "
                            f"supplier-stage inventory position (stores + all WIP + pb) to be "
                            f"at least this much, so that the chain can deliver the "
                            f"already-anticipated seasonal demand over the next "
                            f"{cov_sup_local} weeks. The same logic applies at each of "
                            f"the other 3 stages with progressively shorter windows: "
                            f"cov_semi = {cov_semi_local} wk, cov_fp = {cov_fp_local} wk, "
                            f"cov_ship = {cov_ship_local} wk."
                        )
                else:
                    st.markdown(
                        f"Because the demand profile is **linear/flat**, the planner's "
                        f"forecast is simply the latest observed demand: "
                        f"**ff = {s['forecast']:.0f}/wk**. Each target is therefore "
                        f"computed as **ff × coverage**, where coverage = lead time "
                        f"from that stage to the store + ordering frequency. For the "
                        f"supplier: target_sup = ff × (mat+semi+fp+dist+freq) = "
                        f"{s['forecast']:.0f} × "
                        f"({mat_lt}+{semi_lt}+{fp_lt}+{dist_lt}+{order_freq}) = "
                        f"{s['forecast']:.0f} × {cov_sup_local} = "
                        f"**{int(s['forecast']) * cov_sup_local}** units."
                    )

                # Per-stage narrative for the supplier
                cd_sup = calc['sup']
                if cd_sup['final_order'] > 0:
                    why_sup = (f"Because **existing inventory ({cd_sup['existing']:.0f}) "
                              f"is below the target ({cd_sup['target']:.0f})**, the "
                              f"pooled gap is {cd_sup['pooled_order']:.0f}. ")
                    if cd_sup['ps_gap'] > cd_sup['pooled_order']:
                        why_sup += (f"The per-store check is even larger "
                                   f"({cd_sup['ps_gap']:.0f}) — one of the stores "
                                   f"would have starved under the pooled view, so we "
                                   f"raise the order to **{cd_sup['final_order']:.0f}** "
                                   f"units.")
                    else:
                        why_sup += f"The supplier order is therefore **{cd_sup['final_order']:.0f}** units."
                else:
                    why_sup = (f"Because **existing ({cd_sup['existing']:.0f}) already "
                              f"covers the target ({cd_sup['target']:.0f})**, no "
                              f"supplier order is placed this week.")
                st.markdown(f"**Supplier:** {why_sup}")

                # Per-stage table for at-a-glance comparison
                rows = []
                for stage_key, stage_label, cov_val in [
                    ('sup',  'Supplier',         cov_sup_local),
                    ('semi', 'Semi (RM→Semi)',   cov_semi_local),
                    ('fp',   'FP (Semi→FP)',     cov_fp_local),
                    ('ship', 'Ship (CW→Store)',  cov_ship_local),
                ]:
                    cd = calc[stage_key]
                    gap = cd['target'] - cd['existing']
                    rows.append({
                        'Stage':         stage_label,
                        'Coverage (wk)': cov_val,
                        'Target':        f"{cd['target']:.0f}",
                        'Existing':      f"{cd['existing']:.0f}",
                        'Gap (T−E)':     f"{gap:.0f}",
                        'Pooled order':  f"{cd['pooled_order']:.0f}",
                        'Per-store gap': f"{cd['ps_gap']:.0f}",
                        'Final order':   f"**{cd['final_order']:.0f}**",
                    })
                st.caption("Final order = max(pooled-gap, per-store-gap). The per-store check "
                           "fires when one store would starve even though aggregate stock looks fine.")
                st.table(pd.DataFrame(rows).set_index('Stage'))

                # Breakdown of what each 'Existing' value comprises.
                # Reading prev = states[week-1] gives us the START-OF-WEEK
                # backlog values (= end of last week's), which is what the
                # planner sees when reviewing at step 4 — BEFORE this week's
                # new order is added to pb / the per-stage backlogs.
                stores_now = s['store_stock']
                # mat_pipe etc. as the planner sees them at step 4: position 0
                # has been cleared at step 1 and its content folded into raw_mat
                # at step 3; positions 1..end are unchanged from end of last week.
                # Equivalent shorthand: stage downstream WIP = end-of-last-week
                # stage WIP minus the front slot that just transitioned to buffer.
                # The cleanest way to display this is via end-of-this-week values,
                # which equal pre-step-1 values for the unaffected positions.
                pb_pre  = (prev.get('backlog', 0)      if prev else 0)
                sb_pre  = (prev.get('semi_backlog', 0) if prev else 0)
                fb_pre  = (prev.get('fp_backlog', 0)   if prev else 0)
                shb_pre = (prev.get('ship_backlog', 0) if prev else 0)
                # WIP downstream of each stage, AS THE PLANNER SEES IT at step 4
                # (= aggregate of every unit the planner counts toward 'existing'
                # excluding stores and the stage's own backlog).
                wip_to_store_sup  = (calc['sup']['existing']  - stores_now - pb_pre)
                wip_to_store_semi = (calc['semi']['existing'] - stores_now - sb_pre)
                wip_to_store_fp   = (calc['fp']['existing']   - stores_now - fb_pre)
                wip_to_store_ship = (calc['ship']['existing'] - stores_now - shb_pre)
                st.markdown(
                    "**Why these 'Existing' values?** Each stage's existing inventory "
                    "is everything that's already in motion toward the customer **plus** "
                    "any of that stage's own orders that the planner placed earlier but "
                    "the factory hasn't yet executed (= the stage's own backlog at the "
                    "start of this week, BEFORE this week's new order is added). "
                    "Numerically, for this week:"
                )
                breakdown_rows = [
                    {'Stage': 'Supplier',         'Stores': f"{stores_now:.0f}",
                     'Pipes + buffers downstream': f"{wip_to_store_sup:.0f}",
                     "This stage's pre-order backlog": f"{pb_pre:.0f} (pb)",
                     'Total Existing':            f"**{calc['sup']['existing']:.0f}**"},
                    {'Stage': 'Semi (RM→Semi)',   'Stores': f"{stores_now:.0f}",
                     'Pipes + buffers downstream': f"{wip_to_store_semi:.0f}",
                     "This stage's pre-order backlog": f"{sb_pre:.0f} (semi_bl)",
                     'Total Existing':            f"**{calc['semi']['existing']:.0f}**"},
                    {'Stage': 'FP (Semi→FP)',     'Stores': f"{stores_now:.0f}",
                     'Pipes + buffers downstream': f"{wip_to_store_fp:.0f}",
                     "This stage's pre-order backlog": f"{fb_pre:.0f} (fp_bl)",
                     'Total Existing':            f"**{calc['fp']['existing']:.0f}**"},
                    {'Stage': 'Ship (CW→Store)',  'Stores': f"{stores_now:.0f}",
                     'Pipes + buffers downstream': f"{wip_to_store_ship:.0f}",
                     "This stage's pre-order backlog": f"{shb_pre:.0f} (ship_bl)",
                     'Total Existing':            f"**{calc['ship']['existing']:.0f}**"},
                ]
                st.table(pd.DataFrame(breakdown_rows).set_index('Stage'))
                st.caption(
                    "⚠️ Note on the **Supplier backlog** column above (pb) vs the "
                    "**Backlog** value shown in the diagram's top info bar: "
                    f"the diagram shows the END-OF-WEEK pb (= **{s.get('backlog', 0):.0f}**), "
                    "which INCLUDES this week's new order ("
                    f"+{calc['sup']['final_order']:.0f}) and EXCLUDES this week's supplier "
                    f"ship (−{s.get('supplier_shipped', 0):.0f}). The 'Existing' calculation "
                    f"above uses the start-of-week pb ({pb_pre:.0f}) — the value before the "
                    "new order is added. Reconciliation: "
                    f"start-of-week pb ({pb_pre:.0f}) + order ({calc['sup']['final_order']:.0f}) "
                    f"− shipped ({s.get('supplier_shipped', 0):.0f}) = end-of-week pb "
                    f"({s.get('backlog', 0):.0f}). Same logic for the other 3 backlogs."
                )

                # --- Per-store gap explanation (only when smart distribution is on) ---
                if smart_distrib and n_stores > 1:
                    st.markdown(
                        "**Why the 'Per-store gap' column?** The pooled targets above "
                        "treat the whole network as one big bucket. But with stochastic "
                        f"per-store demand drawn from {n_stores} stores, individual stores "
                        "can starve even when the aggregate looks fine. The per-store check "
                        "asks, for each stage independently:\n\n"
                        "> *Will each store individually have enough, assuming smart "
                        "distribution water-fills the common upstream pool to equalise "
                        "weeks-of-cover?*"
                    )
                    st.markdown(
                        f"The planner's expected allocation is the SAME cover-equalising "
                        f"water-fill the CW will run later. **Per-store rates are learned** "
                        f"from observed demand via EMA (α={PS_RATE_ALPHA}) — `ps_rate[i]` is "
                        f"the planner's belief about store i's weekly demand. For each stage:\n\n"
                        f"```\n"
                        f"own[i]   = stores[i] + Σ dist_pipes[i]    (i = 1..N, store-specific)\n"
                        f"fcst[i]  = ps_rate[i]                     (learned per-store rate)\n"
                        f"total    = Σ own[i] + common_pool\n"
                        f"target_cov = total / Σ fcst               (equal weeks-of-cover)\n\n"
                        f"# Smart water-fills toward `target_cov × fcst[i]` from common_pool:\n"
                        f"raw_share[i] = max(0, target_cov × fcst[i] − own[i])\n"
                        f"if Σ raw_share > common_pool: scale raw_share by common_pool / Σ raw_share\n"
                        f"supply[i]    = own[i] + raw_share[i]\n\n"
                        f"demand_per_store[i] = lookahead_total × fcst[i] / Σ fcst\n"
                        f"gap[i]   = max(0, demand_per_store[i] − supply[i])\n"
                        f"Per-store gap = Σ gap[i] − this stage's own backlog\n"
                        f"```\n\n"
                        f"`common_pool` per stage:\n"
                        f"- **Ship**:     0  (we're computing the pool itself)\n"
                        f"- **FP**:       fp_pipe + cw\n"
                        f"- **Semi-Fin**: semi_pipe + semi + fp_pipe + cw\n"
                        f"- **Supplier**: mat_pipe + raw_mat + semi_pipe + semi + "
                        f"fp_pipe + cw + pb\n\n"
                        f"Final order = `max(pooled-gap, per-store-gap)`."
                    )

                    # Worked example — Supplier stage (most reach)
                    cd_sup = calc['sup']
                    if seasonal_mode and pf is not None:
                        demand_X = sum(pc[i] if (0 < i < len(pc)) else 0.0
                                       for i in range(s['week'] + 1, s['week'] + cov_sup_local + 1)) * pf
                    else:
                        demand_X = s['forecast'] * cov_sup_local
                    common_pool_sup = (sum(s.get('mat_pipe', []))
                                      + s.get('raw_mat_stock', 0)
                                      + sum(s.get('semi_pipe', []))
                                      + s.get('semi_stock', 0)
                                      + sum(s.get('fp_pipe', []))
                                      + s.get('cw_stock', 0)
                                      + pb_pre)
                    stores_arr = np.array(s.get('stores', []), dtype=float)
                    dist_per_store = np.array([sum(dp) for dp in s.get('dist_pipes', [])], dtype=float)
                    own_arr = stores_arr + dist_per_store
                    # Learned per-store rate water-fill (matches engine's _ps_gap)
                    fcst_ps = np.maximum(np.array(s.get('ps_rate', [1.0] * n_stores), dtype=float), 1e-6)
                    tw_probs = fcst_ps / fcst_ps.sum() if fcst_ps.sum() > 0 else np.full(n_stores, 1.0/n_stores)
                    total_sup = float(own_arr.sum()) + common_pool_sup
                    target_cov = total_sup / max(float(fcst_ps.sum()), 1e-6)
                    raw_share = np.maximum(0.0, target_cov * fcst_ps - own_arr)
                    s_sum = float(raw_share.sum())
                    scaled_note = ""
                    if s_sum > common_pool_sup + 1e-9 and s_sum > 0:
                        raw_share = raw_share * (common_pool_sup / s_sum)
                        scaled_note = " (scaled to fit pool)"
                    supply = own_arr + raw_share
                    dem_per_arr = demand_X * tw_probs           # per-store horizon demand
                    gaps = np.maximum(0.0, dem_per_arr - supply)
                    total_gap = float(gaps.sum())
                    under_covered = int((gaps > 0.5).sum())
                    dem_per = float(dem_per_arr.mean())
                    st.markdown(
                        f"**Concrete example — Supplier per-store gap at W{s['week']}**:"
                    )
                    rows_ps = [
                        {'Quantity': 'Lookahead total demand (next ' + str(cov_sup_local) + ' wks)',
                         'Aggregate': f"{demand_X:.0f}",
                         'Per store (tier-avg)': f"{dem_per:.2f}"},
                        {'Quantity': 'Own dedicated stock (stores + dist pipes)',
                         'Aggregate': f"{float(own_arr.sum()):.0f}",
                         'Per store (avg/min/max)':
                             f"{float(own_arr.mean()):.2f} / "
                             f"{float(own_arr.min()):.0f} / "
                             f"{float(own_arr.max()):.0f}"},
                        {'Quantity': f"Common pool to allocate",
                         'Aggregate': f"{common_pool_sup:.0f}",
                         'Notes': scaled_note or "fits without scaling"},
                        {'Quantity': '⇒ Target weeks-of-cover after water-fill ((Σ own + pool) / Σ fcst)',
                         'Aggregate': f"{target_cov:.2f}",
                         'Notes': "each store i then targets target_cov × fcst[i]"},
                        {'Quantity': '⇒ Stores under-covered after water-fill',
                         'Aggregate': f"{under_covered} / {n_stores}",
                         'Notes': ""},
                        {'Quantity': '⇒ Sum of per-store gaps (Σ max(0, dx·prob[i] − supply[i]))',
                         'Aggregate': f"**{total_gap:.0f}**",
                         'Notes': ""},
                    ]
                    st.table(pd.DataFrame(rows_ps).set_index('Quantity'))
                    st.markdown(
                        f"After subtracting the supplier's already-pending backlog "
                        f"({pb_pre:.0f}), the Per-store gap contribution shown for "
                        f"Supplier in the table above is "
                        f"**{max(0, total_gap - pb_pre):.0f}**."
                    )
            else:
                next_review = ((s['week'] // order_freq) + 1) * order_freq
                st.markdown(
                    f"#### 3. Planner review — *not a review week*\n\n"
                    f"The planner only places orders every **{order_freq}** week(s); "
                    f"the next review is at **W{next_review}**. Between reviews the "
                    f"existing backlogs keep draining as the factory executes them, "
                    f"but no new orders are added."
                )

            # 4. SUPPLIER SHIP
            st.markdown("#### 4. Supplier ship")
            pb_before = (prev.get('backlog', 0) if prev else 0) + s['order']
            if s['supplier_shipped'] > 0:
                st.markdown(
                    f"The supplier's capacity this week is **{s['supplier_cap']:.0f} pcs/wk** "
                    f"(base {cap_start} ramped by the linear +{int(cap_ramp*100)}%/wk "
                    f"that started the week after the first order was placed). "
                    f"After today's planner order, the supplier backlog stands at "
                    f"**{pb_before:.0f}** units. The supplier ships "
                    f"**min({pb_before:.0f}, {s['supplier_cap']:.0f}) = "
                    f"{s['supplier_shipped']:.0f}** units into the Material pipe; "
                    f"the backlog ends the week at **{s['backlog']:.0f}**."
                )
            else:
                st.markdown(
                    f"The supplier has no backlog to ship this week "
                    f"(pb = {pb_before:.0f}). Capacity is still "
                    f"{s['supplier_cap']:.0f}/wk but is idle."
                )

            # 5. INTERNAL PROCESSING
            st.markdown("#### 5. Internal processing (RM → Semi → FP)")
            semi_bl_before = (prev.get('semi_backlog', 0) if prev else 0) + (calc['semi']['final_order'] if calc else 0)
            fp_bl_before   = (prev.get('fp_backlog', 0) if prev else 0)   + (calc['fp']['final_order']   if calc else 0)
            rm_now = s['raw_mat_before_prod']
            semi_now = sm_prev + s['semi_arr']
            si = s['semi_input']; fi = s['fp_input']
            st.markdown(
                f"**RM → Semi.** The Semi line's weekly capacity is "
                f"**{s['semi_cap']:.0f}**, the planner's outstanding Semi push order "
                f"(semi_backlog) is **{semi_bl_before:.0f}**, and there are "
                f"**{rm_now:.0f}** units of raw material on hand. The factory therefore "
                f"processes **min(RM, cap, backlog) = {si:.0f}** units — these leave the "
                f"RM buffer and enter the Semi pipe, where they will travel for "
                f"{semi_lt} week(s) before landing in the Semi buffer."
            )
            st.markdown(
                f"**Semi → FP.** The FP line's weekly capacity is **{s['fp_cap']:.0f}**, "
                f"the planner's FP backlog is **{fp_bl_before:.0f}**, and there are "
                f"**{semi_now:.0f}** units of semi-finished goods available. So "
                f"**fi = min(Semi, cap, fp_backlog) = {fi:.0f}** units move into the FP "
                f"pipe and will arrive at the CW buffer in {fp_lt} week(s)."
            )

            # 6. CW PUSH
            st.markdown("#### 6. CW → Stores push *(no capacity limit — pure logistics)*")
            cw_avail = cw_prev + s['fp_arr']
            ship_bl_before = (prev.get('ship_backlog', 0) if prev else 0) + (calc['ship']['final_order'] if calc else 0)
            ship_out = s['cw_shipped']
            if ship_out > 0:
                st.markdown(
                    f"There are **{cw_avail:.0f} units in CW** and the planner's "
                    f"ship_backlog stands at **{ship_bl_before:.0f}**. CW push is "
                    f"uncapped, so the warehouse pushes **min(CW, ship_backlog) = "
                    f"{ship_out:.0f}** units toward the stores. Smart distribution "
                    f"allocates them by demand share and per-store cover, prioritising "
                    f"whichever stores would otherwise starve first — this week "
                    f"**{sum(s.get('allocs', [])):.0f} total units** are split across "
                    f"the {n_stores} stores (min/max per store: "
                    f"{min(s.get('allocs', [0])):.0f} / {max(s.get('allocs', [0])):.0f}). "
                    f"They enter the Distribution pipes and will arrive at the stores "
                    f"in {dist_lt} week(s)."
                )
            else:
                st.markdown(
                    f"CW has **{cw_avail:.0f}** units on hand and ship_backlog is "
                    f"**{ship_bl_before:.0f}**. With at least one of those at zero, "
                    f"nothing is pushed this week."
                )

            # 7. PIPE UPDATE & COST BOOKING
            st.markdown("#### 7. Pipe shift and cost booking")
            st.markdown(
                f"At the end of the week, every pipe shifts left by one slot: the "
                f"front-of-pipe units (which 'arrived' at the start of this week) "
                f"have already been absorbed into the next buffer, and the back-of-pipe "
                f"slot receives this week's freshly-pushed units. So the Material pipe "
                f"now gains the **{s['supplier_shipped']:.0f}** units the supplier just "
                f"shipped, the Semi pipe gains **{si:.0f}**, the FP pipe gains "
                f"**{fi:.0f}**, and the **{n_stores}** Distribution pipes collectively "
                f"gain **{sum(s.get('allocs', [])):.0f}** units (water-filled across stores)."
            )
            st.markdown(
                f"**Costs are booked the moment units enter each stage** (no anticipation, "
                f"no double-counting), valued at the cumulative cost-to-stage:\n\n"
                f"- Raw-material cost: {s['supplier_shipped']:.0f} units × €{var_cost} × 50% = **€{s['cost_mat']:,.0f}**\n"
                f"- Semi processing cost (+25%): {si:.0f} × €{var_cost} × 25% = **€{s['cost_semi']:,.0f}**\n"
                f"- Finished-goods cost (+25%): {fi:.0f} × €{var_cost} × 25% = **€{s['cost_fp']:,.0f}**\n\n"
                f"These three lines feed the cumulative Total VC in the end-of-sim P&L. "
                f"Note that the entire €{var_cost} cost of a unit is booked across its "
                f"three stage transitions (50% + 25% + 25% = 100%), so the system never "
                f"double-counts a unit's cost."
            )


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
        st.markdown("#### Demand vs Fulfillment (aggregate)")
        rows = []
        for s in states[1:]:
            rows.append({'Week': s['week'], 'Component': 'Sales',  'Value': s['sales']})
            rows.append({'Week': s['week'], 'Component': 'Missed', 'Value': s['missed']})
        df_bars = pd.DataFrame(rows)
        bars = alt.Chart(df_bars).mark_bar(cornerRadiusTopLeft=2, cornerRadiusTopRight=2).encode(
            x=alt.X('Week:O'), y=alt.Y('Value:Q', title='Units', stack=True),
            color=alt.Color('Component:N',
                scale=alt.Scale(domain=['Sales', 'Missed'], range=['#1a8a4a', '#c0392b']),
                legend=alt.Legend(orient='top', title=None)),
        ).properties(height=260)
        rule = alt.Chart(pd.DataFrame({'Week': [week]})).mark_rule(
            color='#d4850a', strokeWidth=2, strokeDash=[4, 2]).encode(x='Week:O')
        st.altair_chart(bars + rule, use_container_width=True)

    with ch2:
        st.markdown("#### Store Stocks (aggregate) & Orders")
        stock_data = pd.DataFrame({
            'Week':         [s['week'] for s in states],
            'Stores total': [s['store_stock'] for s in states],
            'Order':        [s['order']       for s in states],
        })
        lines = alt.Chart(stock_data).mark_area(opacity=0.30, color='#2c5f8a').encode(
            x=alt.X('Week:O'),
            y=alt.Y('Stores total:Q', title='Units (sum across stores)'),
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


# --- Week-by-week data table (collapsed by default; downstream → upstream) ---
with st.expander("\U0001f4ca Detailed Week-by-Week Data", expanded=False):
    table_data = []
    for s in states:
        wk_rev    = s['sales'] * price
        wk_vc     = s.get('cost_mat', 0) + s.get('cost_semi', 0) + s.get('cost_fp', 0)
        wk_margin = wk_rev - wk_vc
        pf = s.get('planner_factor')
        table_data.append({
            'Week':        s['week'],

            # Planner state
            'Forecast':    s.get('forecast', 0),
            'Cover Tgt':   s.get('target_sup', 0),
            'Planner f':   round(pf, 2) if pf is not None else None,

            # Demand / Sales / Misses (aggregate; per-store detail in zoom)
            'Demand':      s['demand'],
            'Sales':       s['sales'],
            'Missed':      s['missed'],
            'Stockout Stores': s.get('stores_w_stockout', 0),

            # Stores (aggregate + spread)
            'Stores Total': s.get('store_stock', 0),
            'Stores Avg':   s.get('stores_mean', 0),
            'Stores Min':   s.get('stores_min', 0),
            'Stores Max':   s.get('stores_max', 0),
            'Alloc Total':  sum(s.get('allocs', [])),

            # CW → Store stage
            'Finishing Buffer': s.get('cw_stock', 0),
            'Distribution Push': s.get('cw_shipped', 0),
            'Distribution pipe': round(sum(sum(dp) for dp in s.get('dist_pipes', [])), 1),
            'Distribution BL':   s.get('ship_backlog', 0),
            'Ord Distribution':  s.get('order_ship', 0),

            # Semi → FP stage
            'Finishing pipe': round(sum(s.get('fp_pipe', [])), 1),
            'Finishing Proc': s.get('fp_input', 0),
            'Finishing BL':   s.get('fp_backlog', 0),
            'Ord Finishing':  s.get('order_fp', 0),

            # RM → Semi stage
            'Semi-Fin Buffer': s.get('semi_stock', 0),
            'Semi-Fin pipe':   round(sum(s.get('semi_pipe', [])), 1),
            'Semi-Fin Proc':   s.get('semi_input', 0),
            'Semi-Fin BL':     s.get('semi_backlog', 0),
            'Ord Semi-Fin':    s.get('order_semi', 0),

            # Supplier → RM stage
            'Material Buffer': s.get('raw_mat_stock', 0),
            'Material pipe':   round(sum(s.get('mat_pipe', [])), 1),
            'Material Ship':   s.get('supplier_shipped', 0),
            'Material BL':     s.get('backlog', 0),
            'Ord Material':    s['order'],
            'Material Cap':    s.get('supplier_cap', 0),

            # Totals
            'WIP total':   s.get('wip_total', 0),

            # Financials
            'Revenue':     round(wk_rev),
            'Cost RM':     round(s.get('cost_mat', 0)),
            'Cost Semi':   round(s.get('cost_semi', 0)),
            'Cost FP':     round(s.get('cost_fp', 0)),
            'Tot VC':      round(wk_vc),
            'Margin':      round(wk_margin),
        })
    st.dataframe(pd.DataFrame(table_data), use_container_width=True, height=500)
    st.caption(
        "Reads left-to-right downstream → upstream. **Buffer** = stock waiting at that stage. "
        "**pipe** = units in transit (sum). **Proc / Ship / Push** = units that moved this week. "
        "**BL** = planner's backlog of pushes-still-to-do at that stage. **Ord** = planner's order placed at this review. "
        "Costs are booked when units ENTER each stage (RM 50%, Semi +25%, FP +25%)."
    )


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
