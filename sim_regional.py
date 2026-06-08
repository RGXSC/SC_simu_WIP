"""Two-region warehouse simulator (CW → RW_A / RW_B → Stores).

Self-contained engine for the 2-RW model agreed in the spec. Implements:
  - N stores per region (total 2N), each region with 10/30/60 tier mix
  - Deterministic per-store demand (15:6:1 high:medium:small within region)
  - CW → RW pipes with cw_rw_lt; RW → Store pipes with rw_store_lt
  - Stage orders = MAX(chain-aggregate target, per-region shortfall sum)
  - Smart CW→RW water-fill (priority to starved region)
  - Smart RW→Store water-fill, strictly in-region
  - Planner discovers share_A at first review (locked once)
  - Lifetime production cap (chain-wide)

The existing run_simulation in app.py is left unchanged. This module is
used to simulate the 9 regional preset scenarios and produce the
synthesis slide; a follow-up wires it into the Streamlit UI.
"""
from __future__ import annotations
import math
import numpy as np

# Tier constants (mirror app.py)
TIER_SHARE   = {"small": 0.60, "medium": 0.30, "high": 0.10}
TIER_WEIGHTS = {"small": 0.5,  "medium": 1.0,  "high": 4.0}


# ─── Demand helpers (same algo as app._deterministic_per_store_demand) ──

def _bucket_counts(n: int) -> tuple[int, int, int]:
    n = int(max(1, n))
    n_high   = max(1, int(round(n * TIER_SHARE["high"])))
    n_medium = max(1, int(round(n * TIER_SHARE["medium"])))
    n_small  = n - n_high - n_medium
    if n_small < 0:
        n_small, n_medium = 0, max(0, n - n_high)
    return n_high, n_medium, n_small


def _deterministic_demand(week: int, dem_total: int, n_h: int, n_m: int, n_s: int) -> np.ndarray:
    """Returns integer per-store demand, summing exactly to dem_total."""
    N = n_h + n_m + n_s
    dem_total = int(max(0, dem_total))
    if N == 0 or dem_total == 0:
        return np.zeros(max(1, N), dtype=int)
    denom = 15.0 * n_h + 6.0 * n_m + 1.0 * n_s
    if denom <= 0:
        return np.zeros(N, dtype=int)
    bkt_h = int(15.0 * n_h * dem_total / denom)
    bkt_m = int( 6.0 * n_m * dem_total / denom)
    bkt_s = int( 1.0 * n_s * dem_total / denom)
    residual = dem_total - (bkt_h + bkt_m + bkt_s)
    if residual > 0:
        give = min(residual, 15 * n_h)
        bkt_h += give; residual -= give
        if residual > 0:
            give = min(residual, 6 * n_m)
            bkt_m += give; residual -= give
            bkt_s += residual
    out = np.zeros(N, dtype=int)
    # high: first `rem` stores always
    if n_h > 0:
        base = bkt_h // n_h; rem = bkt_h - base * n_h
        out[:n_h] = base
        if rem > 0: out[:rem] += 1
    def _fill(start, n, total):
        if n == 0 or total == 0: return
        base = total // n; rem = total - base * n
        out[start:start+n] = base
        if rem > 0:
            offset = ((week - 1) * rem) % n
            for k in range(rem):
                out[start + ((offset + k) % n)] += 1
    _fill(n_h,        n_m, bkt_m)
    _fill(n_h + n_m,  n_s, bkt_s)
    return out


# ─── Smart water-fill across N stores (Hamilton rounding) ───────────────

def _water_fill_int(ship_out: float, stocks: np.ndarray, pipes_sum: np.ndarray,
                    fcst: np.ndarray) -> np.ndarray:
    """Equalise weeks-of-cover. Returns int allocs summing to int(ship_out)."""
    n = len(stocks)
    if ship_out <= 0 or n == 0:
        return np.zeros(n, dtype=int)
    eps = 1e-6
    fcst = np.maximum(fcst, eps)
    ip = stocks.astype(float) + pipes_sum.astype(float)
    cov = ip / fcst
    order = np.argsort(cov)
    cov_sorted = cov[order].copy()
    fcst_sorted = fcst[order]
    allocs = np.zeros(n, dtype=float)
    remaining = float(ship_out)
    for k in range(n - 1):
        delta = cov_sorted[k + 1] - cov_sorted[k]
        if delta <= 0: continue
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
    floors = np.floor(allocs).astype(int)
    diff = int(ship_out) - int(floors.sum())
    if diff > 0:
        fracs = allocs - floors
        rank = np.lexsort((cov, -fracs))
        floors[rank[:diff]] += 1
    elif diff < 0:
        fracs = allocs - floors
        rank = np.lexsort((-cov, fracs))
        taken = 0
        for idx in rank:
            if taken >= -diff: break
            if floors[idx] > 0:
                floors[idx] -= 1; taken += 1
    return floors


# ─── Main regional engine ────────────────────────────────────────────────

def run_simulation_regional(
    *, weeks: int,
    n_per_region: int,
    demand_curve: list[int],         # length weeks+1 (index 0 unused) or len weeks
    region_split_pct: int = 50,      # ground-truth % to Region A (10..90, step 10)
    mat_lt: int = 1, semi_lt: int = 1, fp_lt: int = 1,
    cw_rw_lt: int = 1, rw_store_lt: int = 1,
    order_freq: int = 1,
    init_rawmat: int = 0, init_semi: int = 0,
    init_cw: int = 0, init_rw_total: int = 0, init_store_total: int = 0,
    cap_start: int = 1000, cap_ramp: float = 0.0,
    smart_distrib: bool = True,
    prod_cap: int | None = None,
    var_cost: float = 5.0, price: float = 10.0, fixed_pct: float = 0.20,
    base_forecast: int = 100,
    planner_curve: list[float] | None = None,
) -> dict:
    """Run a 2-RW simulation. Returns a summary dict + per-week states.

    Initial stock placement:
      - init_rw_total split between RW_A and RW_B by region_split_pct (ground truth)
      - init_store_total split between regions by region_split_pct, then
        within each region weighted by per-tier rate (high stores get more)

    Seasonal mode: pass planner_curve (length weeks+1, index 0 unused;
    or length weeks, prepended with 0). The planner BELIEVES this shape
    normalized to avg ≈ base_forecast. At the FIRST review week, it locks
    one scaling factor f_seasonal = Σ actual_demand[1..w] / Σ curve[1..w]
    independently of share_A. From then on every stage target is
    f_seasonal × Σ planner_curve[w+1..w+coverage_x] (capped at sim end).
    """
    seasonal_mode = planner_curve is not None
    if seasonal_mode:
        pc = [0.0] * (weeks + 2)
        if len(planner_curve) == weeks:
            for i in range(weeks):
                pc[i + 1] = float(planner_curve[i])
        else:
            for i in range(min(len(planner_curve), weeks + 1)):
                pc[i] = float(planner_curve[i])
        planner_curve_internal = pc
    else:
        planner_curve_internal = None
    f_seasonal = None  # locked at first review
    # ── Setup ──
    region_split_a = max(0.10, min(0.90, region_split_pct / 100.0))
    region_split_b = 1.0 - region_split_a

    n_h, n_m, n_s = _bucket_counts(n_per_region)
    N_REG = n_h + n_m + n_s
    if N_REG == 0:
        N_REG = 1

    # ── Initial stocks ──
    rw_a = int(round(init_rw_total * region_split_a))
    rw_b = int(init_rw_total - rw_a)

    store_a_total = int(round(init_store_total * region_split_a))
    store_b_total = int(init_store_total - store_a_total)

    # Within each region, distribute init stock by EXPECTED PER-STORE
    # weekly demand averaged over a full rotation cycle, not by uniform
    # tier weight. This fixes the artifact where stores at the front of
    # a tier (which always get the +1 from integer rounding in the
    # deterministic high-tier rule) systematically missed sales because
    # every same-tier store started with identical init.
    #
    # Averaging over `weeks` weeks captures the medium/small rotation
    # AND the permanent high-tier "first N always +1" bias, so each
    # store's init reflects its real expected demand over the run.
    base_dem_a = max(1, int(round(base_forecast * region_split_a)))
    base_dem_b = max(1, int(round(base_forecast * region_split_b)))
    expected_per_store_a = np.zeros(N_REG, dtype=float)
    expected_per_store_b = np.zeros(N_REG, dtype=float)
    for _w in range(1, max(2, weeks + 1)):
        expected_per_store_a += _deterministic_demand(_w, base_dem_a, n_h, n_m, n_s).astype(float)
        expected_per_store_b += _deterministic_demand(_w, base_dem_b, n_h, n_m, n_s).astype(float)
    expected_per_store_a = np.maximum(expected_per_store_a, 1e-3)
    expected_per_store_b = np.maximum(expected_per_store_b, 1e-3)

    def _split_by_expected(total: int, expected: np.ndarray) -> np.ndarray:
        if N_REG == 0 or total == 0:
            return np.zeros(N_REG, dtype=int)
        raw = total * expected / expected.sum()
        floors = np.floor(raw).astype(int)
        rem = total - int(floors.sum())
        if rem > 0:
            order = np.argsort(-(raw - floors))
            floors[order[:rem]] += 1
        return floors

    stores_a = _split_by_expected(store_a_total, expected_per_store_a)
    stores_b = _split_by_expected(store_b_total, expected_per_store_b)

    # ── Pipes ──
    mat_pipe   = [0.0] * max(1, mat_lt)
    semi_pipe  = [0.0] * max(1, semi_lt)
    fp_pipe    = [0.0] * max(1, fp_lt)
    cw_rw_pipe_a = [0.0] * max(1, cw_rw_lt)
    cw_rw_pipe_b = [0.0] * max(1, cw_rw_lt)
    dist_pipes_a = [[0.0] * max(1, rw_store_lt) for _ in range(N_REG)]
    dist_pipes_b = [[0.0] * max(1, rw_store_lt) for _ in range(N_REG)]

    raw_mat = float(init_rawmat); semi = float(init_semi); cw = float(init_cw)
    pb = 0.0          # supplier pre-buffer
    semi_backlog = 0.0; fp_backlog = 0.0
    ship_backlog_a = 0.0; ship_backlog_b = 0.0

    cum_produced = float(init_rawmat + init_semi + init_cw + init_rw_total + init_store_total)

    # ── Coverages (downstream LT + freq) ──
    cov_sup_a  = mat_lt + semi_lt + fp_lt + cw_rw_lt + rw_store_lt + order_freq
    cov_semi_a = semi_lt + fp_lt + cw_rw_lt + rw_store_lt + order_freq
    cov_fp_a   = fp_lt + cw_rw_lt + rw_store_lt + order_freq
    cov_cwrw   = cw_rw_lt + rw_store_lt + order_freq
    cov_ship   = rw_store_lt + order_freq

    # ── Demand normalisation ──
    if len(demand_curve) == weeks:
        demand = [0] + list(demand_curve)
    else:
        demand = list(demand_curve)

    # ── Planner state ──
    ff = float(base_forecast)
    ps_rate_a = np.full(N_REG, ff * region_split_a / N_REG, dtype=float)
    ps_rate_b = np.full(N_REG, ff * region_split_b / N_REG, dtype=float)
    PS_ALPHA = 0.3

    discovered_share = False
    share_a = 0.5  # blind belief pre-review
    cum_sales_a = 0
    cum_sales_total = 0

    order_weeks = list(range(order_freq, weeks + 1, order_freq)) if order_freq > 1 else list(range(1, weeks + 1))

    states = []
    states.append({
        'week': 0, 'cw': cw, 'rw_a': rw_a, 'rw_b': rw_b,
        'stores_a': stores_a.tolist(), 'stores_b': stores_b.tolist(),
        'sales_a': 0, 'sales_b': 0, 'missed_a': 0, 'missed_b': 0,
        'demand_a': 0, 'demand_b': 0, 'sup_order': 0,
    })

    # ── Weekly loop ──
    for w in range(1, weeks + 1):
        # 1. Arrivals
        m_arr  = mat_pipe[0];  mat_pipe[0]  = 0.0
        sm_arr = semi_pipe[0]; semi_pipe[0] = 0.0
        fp_arr = fp_pipe[0];   fp_pipe[0]   = 0.0
        rw_a_arr = cw_rw_pipe_a[0]; cw_rw_pipe_a[0] = 0.0
        rw_b_arr = cw_rw_pipe_b[0]; cw_rw_pipe_b[0] = 0.0
        store_arr_a = np.array([dp[0] for dp in dist_pipes_a], dtype=float)
        store_arr_b = np.array([dp[0] for dp in dist_pipes_b], dtype=float)
        for dp in dist_pipes_a: dp[0] = 0.0
        for dp in dist_pipes_b: dp[0] = 0.0

        raw_mat += m_arr; semi += sm_arr; cw += fp_arr
        rw_a += rw_a_arr; rw_b += rw_b_arr
        stores_a_now = stores_a + store_arr_a.astype(int)
        stores_b_now = stores_b + store_arr_b.astype(int)

        # 2. Demand split by ground-truth region_split, then by tier within region
        dem_tot = int(demand[w]) if w < len(demand) else int(demand[-1])
        dem_a = int(round(dem_tot * region_split_a))
        dem_b = dem_tot - dem_a
        per_a = _deterministic_demand(w, dem_a, n_h, n_m, n_s)
        per_b = _deterministic_demand(w, dem_b, n_h, n_m, n_s)

        # 3. Sales
        sales_a_arr = np.minimum(stores_a_now, per_a)
        sales_b_arr = np.minimum(stores_b_now, per_b)
        missed_a = int((per_a - sales_a_arr).sum())
        missed_b = int((per_b - sales_b_arr).sum())
        sales_a = int(sales_a_arr.sum()); sales_b = int(sales_b_arr.sum())
        stores_a = (stores_a_now - sales_a_arr).astype(int)
        stores_b = (stores_b_now - sales_b_arr).astype(int)

        # Per-store EMA rate update (planner learns rate per store)
        ps_rate_a = PS_ALPHA * per_a.astype(float) + (1 - PS_ALPHA) * ps_rate_a
        ps_rate_b = PS_ALPHA * per_b.astype(float) + (1 - PS_ALPHA) * ps_rate_b

        cum_sales_a += sales_a
        cum_sales_total += sales_a + sales_b

        # 4. Order step (review weeks only). First review locks discovered share_A.
        sup_ord = 0
        if w in order_weeks:
            # Discover share_A at first review
            if not discovered_share:
                if cum_sales_total > 0:
                    share_a = cum_sales_a / cum_sales_total
                    snap = round(share_a * 10) / 10
                    if abs(share_a - snap) < 0.05:
                        share_a = float(snap)
                share_a = max(0.10, min(0.90, share_a))
                discovered_share = True
                # Independently lock the seasonal magnitude factor f_seasonal
                # = Σ actual_total[1..w] / Σ curve[1..w] (the planner's
                # discovered "size of the season"). Snap to clean integer or
                # 0.1 increment within rounding tolerance.
                if seasonal_mode and f_seasonal is None:
                    actual_cum = float(sum(demand[i] for i in range(1, w + 1)
                                           if i < len(demand)))
                    expected_cum = float(sum(planner_curve_internal[i]
                                             for i in range(1, w + 1)))
                    if expected_cum > 0.01:
                        raw = actual_cum / expected_cum
                        snap_i = round(raw)
                        if abs(raw - snap_i) < 0.05 and snap_i > 0:
                            f_seasonal = float(snap_i)
                        else:
                            snap_1 = round(raw * 10) / 10
                            f_seasonal = snap_1 if abs(raw - snap_1) < 0.02 else raw
                    else:
                        f_seasonal = 1.0
            share_b = 1.0 - share_a

            # Stage-target "rate" — flat uses this week's observed demand;
            # seasonal uses a forward look-ahead over the planner curve.
            def _lookahead(cov):
                if not seasonal_mode or f_seasonal is None:
                    return None
                tot = 0.0
                for i in range(w + 1, min(w + cov + 1, len(planner_curve_internal))):
                    tot += planner_curve_internal[i]
                return f_seasonal * tot

            ff_now = float(dem_tot) if dem_tot > 0 else ff
            ex_sup  = float(stores_a.sum() + stores_b.sum() + sum(mat_pipe) + raw_mat
                            + sum(semi_pipe) + semi + sum(fp_pipe) + cw
                            + rw_a + rw_b + sum(cw_rw_pipe_a) + sum(cw_rw_pipe_b)
                            + float(sum(sum(dp) for dp in dist_pipes_a))
                            + float(sum(sum(dp) for dp in dist_pipes_b))
                            + pb)
            ex_semi = float(stores_a.sum() + stores_b.sum() + sum(semi_pipe) + semi
                            + sum(fp_pipe) + cw + rw_a + rw_b + sum(cw_rw_pipe_a)
                            + sum(cw_rw_pipe_b)
                            + float(sum(sum(dp) for dp in dist_pipes_a))
                            + float(sum(sum(dp) for dp in dist_pipes_b))
                            + semi_backlog)
            ex_fp   = float(stores_a.sum() + stores_b.sum() + sum(fp_pipe) + cw
                            + rw_a + rw_b + sum(cw_rw_pipe_a) + sum(cw_rw_pipe_b)
                            + float(sum(sum(dp) for dp in dist_pipes_a))
                            + float(sum(sum(dp) for dp in dist_pipes_b))
                            + fp_backlog)

            if seasonal_mode and f_seasonal is not None:
                tgt_sup_total  = _lookahead(cov_sup_a)  or 0.0
                tgt_semi_total = _lookahead(cov_semi_a) or 0.0
                tgt_fp_total   = _lookahead(cov_fp_a)   or 0.0
                tgt_cwrw_total = _lookahead(cov_cwrw)   or 0.0
            else:
                tgt_sup_total  = ff_now * cov_sup_a
                tgt_semi_total = ff_now * cov_semi_a
                tgt_fp_total   = ff_now * cov_fp_a
                tgt_cwrw_total = ff_now * cov_cwrw

            pooled_sup  = math.ceil(max(0, tgt_sup_total  - ex_sup))
            pooled_semi = math.ceil(max(0, tgt_semi_total - ex_semi))
            pooled_fp   = math.ceil(max(0, tgt_fp_total   - ex_fp))

            # Per-region shortfalls. Upstream (raw, semi, FP, CW, mat/semi/fp
            # pipes, pb) is not yet allocated to a region — credit it to each
            # region by the discovered (or default 50/50) share so the
            # per-region MAX doesn't double-count vs the pooled order.
            upstream_undiff = float(raw_mat + semi + cw + sum(mat_pipe)
                                    + sum(semi_pipe) + sum(fp_pipe) + pb)
            inv_a_full = float(stores_a.sum() + rw_a + sum(cw_rw_pipe_a)
                               + float(sum(sum(dp) for dp in dist_pipes_a)))
            inv_b_full = float(stores_b.sum() + rw_b + sum(cw_rw_pipe_b)
                               + float(sum(sum(dp) for dp in dist_pipes_b)))
            def _reg_short(stage_cov: int, region: str) -> float:
                # Seasonal-aware: scale the look-ahead by the region's share
                if seasonal_mode and f_seasonal is not None:
                    la = _lookahead(stage_cov) or 0.0
                    tgt = la * (share_a if region == 'A' else share_b)
                else:
                    tgt = ff_now * stage_cov * (share_a if region == 'A' else share_b)
                inv = (inv_a_full if region == 'A' else inv_b_full)
                inv += (share_a if region == 'A' else share_b) * upstream_undiff
                return max(0.0, tgt - inv)

            sup_reg_total  = _reg_short(cov_sup_a, 'A') + _reg_short(cov_sup_a, 'B')
            semi_reg_total = _reg_short(cov_semi_a, 'A') + _reg_short(cov_semi_a, 'B')
            fp_reg_total   = _reg_short(cov_fp_a, 'A') + _reg_short(cov_fp_a, 'B')

            sup_ord  = max(pooled_sup,  math.ceil(max(0, sup_reg_total  - pb)))
            semi_ord = max(pooled_semi, math.ceil(max(0, semi_reg_total - semi_backlog)))
            fp_ord   = max(pooled_fp,   math.ceil(max(0, fp_reg_total   - fp_backlog)))

            # Per-region CW→RW push order = MAX(pooled-share, regional gap)
            if seasonal_mode and f_seasonal is not None:
                la_cwrw = _lookahead(cov_cwrw) or 0.0
                tgt_a = la_cwrw * share_a
                tgt_b = la_cwrw * share_b
            else:
                tgt_a = ff_now * cov_cwrw * share_a
                tgt_b = ff_now * cov_cwrw * share_b
            inv_a_cw = float(rw_a + sum(cw_rw_pipe_a)
                             + float(sum(sum(dp) for dp in dist_pipes_a))
                             + float(stores_a.sum()))
            inv_b_cw = float(rw_b + sum(cw_rw_pipe_b)
                             + float(sum(sum(dp) for dp in dist_pipes_b))
                             + float(stores_b.sum()))
            cwrw_a_ord = math.ceil(max(0, tgt_a - inv_a_cw - ship_backlog_a))
            cwrw_b_ord = math.ceil(max(0, tgt_b - inv_b_cw - ship_backlog_b))

            # Apply lifetime cap
            if prod_cap is not None:
                headroom = max(0.0, float(prod_cap) - cum_produced)
                sup_ord = int(min(sup_ord, headroom))
            cum_produced += sup_ord

            pb += sup_ord
            semi_backlog += semi_ord
            fp_backlog   += fp_ord
            ship_backlog_a += cwrw_a_ord
            ship_backlog_b += cwrw_b_ord

        # 5. Supplier ships
        pc = min(cap_start * (1 + cap_ramp), cap_start * 10)
        shipped = math.ceil(min(pb, pc)) if pb > 0.01 else 0.0
        pb -= shipped

        # 6. Semi production
        sc = min(cap_start * (1 + cap_ramp), cap_start * 10)
        si = math.ceil(min(raw_mat, sc, semi_backlog)) if (raw_mat > 0.01 and semi_backlog > 0.01) else 0.0
        raw_mat -= si; semi_backlog -= si

        # 7. FP production
        fpc = min(cap_start * (1 + cap_ramp), cap_start * 10)
        fi = math.ceil(min(semi, fpc, fp_backlog)) if (semi > 0.01 and fp_backlog > 0.01) else 0.0
        semi -= fi; fp_backlog -= fi

        # 8. CW → RW push (smart-aware allocation between RW_A and RW_B)
        push_a = math.ceil(min(cw, ship_backlog_a)) if ship_backlog_a > 0.01 else 0.0
        push_b = math.ceil(min(cw - push_a, ship_backlog_b)) if ship_backlog_b > 0.01 else 0.0
        if smart_distrib and discovered_share and (push_a + push_b > 0):
            # Re-allocate to equalise weeks-of-cover between regions.
            cov_a = (rw_a + sum(cw_rw_pipe_a) + float(stores_a.sum())) / max(1e-6, ff_now * share_a)
            cov_b = (rw_b + sum(cw_rw_pipe_b) + float(stores_b.sum())) / max(1e-6, ff_now * (1 - share_a))
            total_push = int(push_a + push_b)
            if cov_a < cov_b:
                # Prioritise A
                gap = math.ceil(max(0, (cov_b - cov_a) * ff_now * share_a))
                pri = min(total_push, gap)
                rest = total_push - pri
                push_a = pri + int(round(rest * share_a))
                push_b = total_push - push_a
            elif cov_b < cov_a:
                gap = math.ceil(max(0, (cov_a - cov_b) * ff_now * (1 - share_a)))
                pri = min(total_push, gap)
                rest = total_push - pri
                push_b = pri + int(round(rest * (1 - share_a)))
                push_a = total_push - push_b
            else:
                push_a = int(round(total_push * share_a))
                push_b = total_push - push_a
            # Cap by ship_backlogs (don't overship beyond what was ordered)
            push_a = min(push_a, int(ship_backlog_a))
            push_b = min(push_b, int(ship_backlog_b))

        cw -= (push_a + push_b)
        ship_backlog_a -= push_a; ship_backlog_b -= push_b

        # 9. RW → Store push (in-region water-fill, strict isolation)
        allocs_a = np.zeros(N_REG, dtype=int); allocs_b = np.zeros(N_REG, dtype=int)
        # The "ship_out" at this leg = whatever RWs can release this week.
        # We use the RW stock as the immediate cap.
        push_store_a = int(rw_a)
        push_store_b = int(rw_b)
        # But cap by the demand-aware coverage need:
        need_a = max(0, math.ceil(ff_now * share_a * cov_ship) - int(stores_a.sum()))
        need_b = max(0, math.ceil(ff_now * (1 - share_a) * cov_ship) - int(stores_b.sum()))
        push_store_a = min(push_store_a, need_a) if discovered_share else min(push_store_a, math.ceil(ff_now * 0.5 * cov_ship))
        push_store_b = min(push_store_b, need_b) if discovered_share else min(push_store_b, math.ceil(ff_now * 0.5 * cov_ship))

        if push_store_a > 0:
            pipes_a_sum = np.array([sum(dp) for dp in dist_pipes_a], dtype=float)
            if smart_distrib and discovered_share:
                allocs_a = _water_fill_int(push_store_a, stores_a.astype(float),
                                           pipes_a_sum, ps_rate_a)
            else:
                # Push: tier-proportional (largest-remainder)
                p = ps_rate_a / max(1e-9, ps_rate_a.sum())
                raw = push_store_a * p
                floors = np.floor(raw).astype(int)
                rem = push_store_a - int(floors.sum())
                if rem > 0:
                    order = np.argsort(-(raw - floors))
                    floors[order[:rem]] += 1
                allocs_a = floors
        if push_store_b > 0:
            pipes_b_sum = np.array([sum(dp) for dp in dist_pipes_b], dtype=float)
            if smart_distrib and discovered_share:
                allocs_b = _water_fill_int(push_store_b, stores_b.astype(float),
                                           pipes_b_sum, ps_rate_b)
            else:
                p = ps_rate_b / max(1e-9, ps_rate_b.sum())
                raw = push_store_b * p
                floors = np.floor(raw).astype(int)
                rem = push_store_b - int(floors.sum())
                if rem > 0:
                    order = np.argsort(-(raw - floors))
                    floors[order[:rem]] += 1
                allocs_b = floors
        rw_a -= int(allocs_a.sum()); rw_b -= int(allocs_b.sum())

        # 10. Pipe shifts
        mat_pipe   = mat_pipe[1:]   + [float(shipped)]
        semi_pipe  = semi_pipe[1:]  + [float(si)]
        fp_pipe    = fp_pipe[1:]    + [float(fi)]
        cw_rw_pipe_a = cw_rw_pipe_a[1:] + [float(push_a)]
        cw_rw_pipe_b = cw_rw_pipe_b[1:] + [float(push_b)]
        for i in range(N_REG):
            dist_pipes_a[i] = dist_pipes_a[i][1:] + [float(allocs_a[i])]
            dist_pipes_b[i] = dist_pipes_b[i][1:] + [float(allocs_b[i])]

        states.append({
            'week': w, 'cw': cw, 'rw_a': rw_a, 'rw_b': rw_b,
            'stores_a': stores_a.tolist(), 'stores_b': stores_b.tolist(),
            'sales_a': sales_a, 'sales_b': sales_b,
            'missed_a': missed_a, 'missed_b': missed_b,
            'demand_a': dem_a, 'demand_b': dem_b,
            'per_store_dem_a':    per_a.tolist(),
            'per_store_sales_a':  sales_a_arr.tolist(),
            'per_store_missed_a': (per_a - sales_a_arr).tolist(),
            'per_store_dem_b':    per_b.tolist(),
            'per_store_sales_b':  sales_b_arr.tolist(),
            'per_store_missed_b': (per_b - sales_b_arr).tolist(),
            'sup_order': sup_ord,
        })

    # ── Summary ──
    tot_dem_a   = sum(s['demand_a'] for s in states[1:])
    tot_dem_b   = sum(s['demand_b'] for s in states[1:])
    tot_dem     = tot_dem_a + tot_dem_b
    tot_sales_a = sum(s['sales_a'] for s in states[1:])
    tot_sales_b = sum(s['sales_b'] for s in states[1:])
    tot_sales   = tot_sales_a + tot_sales_b
    tot_missed  = sum(s['missed_a'] + s['missed_b'] for s in states[1:])

    init_total = init_rawmat + init_semi + init_cw + init_rw_total + init_store_total
    tot_produced = init_total + sum(s['sup_order'] for s in states[1:])

    end_chain = (cw + rw_a + rw_b + raw_mat + semi
                 + int(stores_a.sum()) + int(stores_b.sum())
                 + sum(mat_pipe) + sum(semi_pipe) + sum(fp_pipe)
                 + sum(cw_rw_pipe_a) + sum(cw_rw_pipe_b)
                 + sum(sum(dp) for dp in dist_pipes_a)
                 + sum(sum(dp) for dp in dist_pipes_b))

    revenue = tot_sales * price
    cogs    = tot_produced * var_cost
    fixed   = base_forecast * weeks * price * fixed_pct   # same fixed-cost basis as app.py
    margin  = revenue - cogs - fixed

    return {
        'states': states,
        'svc':        tot_sales / tot_dem if tot_dem else 0.0,
        'svc_a':      tot_sales_a / tot_dem_a if tot_dem_a else 0.0,
        'svc_b':      tot_sales_b / tot_dem_b if tot_dem_b else 0.0,
        'tot_demand': tot_dem,
        'tot_sales':  tot_sales,
        'sales_a':    tot_sales_a, 'sales_b': tot_sales_b,
        'tot_missed': tot_missed,
        'tot_produced': tot_produced,
        'end_chain_stock': end_chain,
        'sell_through': tot_sales / tot_produced if tot_produced else 0.0,
        'revenue': revenue, 'cogs': cogs, 'fixed': fixed, 'margin': margin,
        'margin_pct_rev': margin / revenue if revenue else 0.0,
        'share_a_locked':    share_a if discovered_share else None,
        'f_seasonal_locked': f_seasonal,
    }
