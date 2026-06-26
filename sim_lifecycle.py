"""Single-SKU lifecycle simulator — DETERMINISTIC.

Models the life of ONE product:
  * You buy N units of the SKU.
  * You choose to sell it in S of your M maison-wide stores (the "SKU network").
  * Top-down placement: best S stores first. Stores belong to 2 tiers:
        - 20% HIGH-selling stores  (rate 16x LOW)
        - 80% LOW-selling stores   (rate  1x)
    so that 20% of stores generate 80% of demand chain-wide.
  * On day 1: 1 unit per SKU-network store on the shelf; the remainder
    (N − S) sits in a central warehouse.
  * Demand over the SKU's life is shaped by a profile (gamma-curve, same
    machinery as the rest of the app) over `lifespan_months` × 4.33 weeks.
  * Total life demand = N (mass-balance assumption fixed earlier with the
    user — buy IS the demand).
  * Each week the warehouse tops the stores up to 2 weeks of cover (never
    below this week's demand) INSTANTLY -- no lead time -- before sales,
    serving the HIGH-selling stores first when it can't cover everyone.
    So a store never loses a sale while the warehouse still holds stock;
    lost sales come only from MISPLACEMENT -- stock committed on day 1 to
    low-selling stores that will never sell it strands units the high
    sellers needed.
  * No randomness — same inputs always give the same outcome.

Outputs:
  * Per-week state for the page's charts: stock split four ways
    (high-store / low-store stock that will sell, warehouse stock that will
    serve remaining demand, and all surplus = "available to overperform");
    per-tier sales, lost sales, % stocked.
  * Aggregate season metrics (sold, lost, stuck, margin, sell-through).

The optimisation matrix on Reveal 2 simply re-runs simulate() for each
(S, N) cell — cheap because the loop is just a few floats per week.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np

from sim_common import gamma_pdf


# ───────────────────────────── constants ──────────────────────────────────

HIGH_SHARE       = 0.20       # 20% of maison stores are HIGH-tier
HIGH_RATE_MULT   = 16.0       # HIGH-vs-LOW per-store demand ratio
                              # (with shares 20/80 this gives 80/20 of total demand:
                              #  0.20·16 + 0.80·1 = 4.0, share of HIGH = 3.2/4.0 = 80%)
COVER_TARGET_WEEKS = 2           # warehouse tops stores to 2 weeks of cover
                                 # (replenishment is INSTANT — no lead time)
WEEKS_PER_MONTH  = 52.0 / 12.0   # 4.333…

# Demand-shape profiles (peak position ratio, gamma shape k). Same family
# as sim_common.SEASONAL_PARAMS so the curves look consistent across the app.
PROFILES: dict[str, tuple[float, float]] = {
    "Ultra-steep":  (2.0 / 26.0, 2.5),   # peak right at the start
    "Very steep":   (3.0 / 26.0, 2.5),   # peak ~W3 (in a 26wk horizon)
    "Steep":        (6.0 / 26.0, 3.0),   # peak ~W6
    "Medium":       (9.0 / 26.0, 2.5),   # peak mid-life
    "Flat":         (13.0 / 26.0, 1.8),  # very gentle, late peak
}


# ───────────────────────────── helpers ────────────────────────────────────

def horizon_weeks(lifespan_months: float) -> int:
    """How many weeks to simulate given the SKU's lifespan."""
    return max(1, int(round(lifespan_months * WEEKS_PER_MONTH)))


def demand_curve(horizon: int, profile: str) -> np.ndarray:
    """Normalised weekly demand weights (sums to 1) for the chosen shape."""
    ratio, k = PROFILES.get(profile, PROFILES["Steep"])
    theta = (ratio * horizon) / max(k - 1.0, 0.1)
    raw = np.array([gamma_pdf(w, k, theta) for w in range(1, horizon + 1)])
    s = raw.sum()
    return raw / s if s > 0 else np.full(horizon, 1.0 / horizon)


# ───────────────────────────── state container ────────────────────────────

@dataclass
class WeekState:
    """Stock + flow snapshot at the end of a week (or week 0 = pre-sales).

    Four DISJOINT stock pools (sum = wh + stock_high_total + stock_low_total):

      high_committed  = stock in high-selling stores that will still serve
                        future demand (= min(stock_high, remaining high demand))
      low_committed   = stock in low-selling stores that will still serve
                        future demand (= min(stock_low,  remaining low  demand))
      wh_perform      = warehouse stock that will be shipped to fulfil the
                        demand the stores can't (= min(wh, unmet))
      overperform     = everything else -- stock currently on hand with no
                        remaining forecast demand to match. Includes units
                        stranded in low-selling stores that demand will never
                        reach, plus any warehouse excess.

    This is the "skus above network + forecast" pool the user asked for.
    It STARTS AT 0 (everything was bought to demand) and grows over the
    season as lost-sales accumulate -- because every unit of demand that
    a store couldn't fulfil leaves a matching unit of stock on hand with
    no demand to absorb it.
    """
    week:              int    # 0..horizon
    wh:                int    # warehouse stock at end of week
    stock_high_total:  int    # sum of stock across the S_high HIGH-tier stores
    stock_low_total:   int    # sum of stock across the S_low LOW-tier stores
    sold_high:         int    # sales by the HIGH tier this week
    sold_low:          int
    lost_high:         int    # demand the HIGH tier couldn't fulfil this week
    lost_low:          int
    pct_high_stocked:  float  # fraction of HIGH stores still able to sell (0..1)
    pct_low_stocked:   float  # same for LOW stores
    # ── four-pool decomposition (sums to total stock on hand) ──
    high_committed:    int
    low_committed:     int
    wh_perform:        int
    overperform:       int


# ───────────────────────────── the engine ─────────────────────────────────

def simulate(maison_size: int, sku_network: int, buy: int,
             lifespan_months: float, profile: str,
             price: float = 100.0, var_cost: float = 30.0) -> dict:
    """Run the deterministic lifecycle for one SKU and return per-week state.

    All inputs validated/clipped to safe ranges so the function is total."""
    M = max(1, int(maison_size))
    S_chosen = max(0, min(int(sku_network), M))
    N = max(0, int(buy))
    # Can't stock more stores than units bought (1 unit per store at day 1).
    # The effective network is bounded by N. This silently clamps when the
    # user picks an over-wide network for their buy; the page surfaces this
    # in a banner so the gap stays visible.
    S = min(S_chosen, N)
    horizon = horizon_weeks(lifespan_months)

    # Top-down placement: best stores first.
    M_high = max(1, int(round(M * HIGH_SHARE)))
    M_low  = M - M_high
    S_high = min(S, M_high)
    S_low  = max(0, S - M_high)

    weights = demand_curve(horizon, profile)              # (horizon,) sums to 1

    # Demand-aware footprint: each HIGH store wants 16x what a LOW store wants.
    footprint = S_high * HIGH_RATE_MULT + S_low * 1.0
    if footprint <= 0 or N == 0:
        # Degenerate: no network or no buy -> nothing happens.
        return _empty_result(M, S, N, horizon, S_high, S_low,
                              price, var_cost)

    # Per-week demand FOR EACH TIER AS A WHOLE (sum over its stores).
    # These are floats. Each week we draw an INTEGER demand by taking the
    # increment of the cumulative floored demand -- so the per-week unit
    # counts are whole pieces and the season total still equals N exactly.
    demand_high_week = N * weights * (S_high * HIGH_RATE_MULT) / footprint
    demand_low_week  = N * weights * (S_low  * 1.0           ) / footprint
    cum_high_float = np.cumsum(demand_high_week)
    cum_low_float  = np.cumsum(demand_low_week)
    cum_high_int = 0                                 # integer demand drawn so far
    cum_low_int  = 0
    # Whole-season integer demand per tier (for the remaining-demand split).
    total_demand_high = int(np.floor(cum_high_float[-1] + 1e-9))
    total_demand_low  = int(np.floor(cum_low_float[-1]  + 1e-9))

    # Day-1 allocation: 1 unit per store carrying the product, remainder in
    # the warehouse. ALL stock quantities are integer pieces -- never halves.
    stock_high = int(S_high)
    stock_low  = int(S_low)
    wh         = int(max(0, N - S))

    def _decomp(sh, sl, w, rem_h, rem_l):
        """Four-pool decomposition of stock on hand.

        Overperform is computed GLOBALLY: max(0, total stock - total
        remaining demand). It is the only physically honest definition --
        a unit can only be "above network and forecast" if NO remaining
        demand anywhere can match it. So overperform starts at 0 (you
        bought to demand), grows ONLY as lost sales accumulate (each lost
        sale leaves a unit of stock with no demand to absorb it), and
        cannot coexist with un-served demand on the same unit.

        We physically locate that overperform amount first in the LOW-
        selling stores (since that's where stranding happens), then the
        warehouse, then the high stores. Whatever each pool retains after
        the over-share is removed is its "committed / will perform" share.
        The four sum to sh + sl + w."""
        rem_total = max(0, rem_h) + max(0, rem_l)
        total = sh + sl + w
        over = max(0, total - rem_total)
        # locate the overperform: low stores first, then warehouse, then high
        over_low  = min(sl, over);          over_rem = over - over_low
        over_wh   = min(w,  over_rem);      over_rem = over_rem - over_wh
        over_high = min(sh, over_rem)
        lc = sl - over_low
        wp = w  - over_wh                   # warehouse share that will ship
        hc = sh - over_high
        return hc, lc, wp, over

    hc, lc, wp, over = _decomp(stock_high, stock_low, wh,
                                total_demand_high, total_demand_low)
    states: list[WeekState] = [
        WeekState(0, wh, stock_high, stock_low, 0, 0, 0, 0,
                  1.0 if S_high > 0 else 0.0,
                  1.0 if S_low  > 0 else 0.0,
                  hc, lc, wp, over)
    ]

    for t in range(horizon):
        # 1. discretise this week's demand into INTEGER pieces.
        new_cum_h = int(np.floor(cum_high_float[t] + 1e-9))
        new_cum_l = int(np.floor(cum_low_float[t]  + 1e-9))
        d_h = new_cum_h - cum_high_int
        d_l = new_cum_l - cum_low_int
        cum_high_int = new_cum_h
        cum_low_int  = new_cum_l

        # 2. INSTANT replenishment (no lead-time lag): the warehouse tops the
        #    stores up to 2 weeks of cover -- but never below this week's
        #    demand -- BEFORE sales, so a store never loses a sale while the
        #    warehouse still holds stock. The smart planner serves the
        #    HIGH-selling stores first when the warehouse can't cover both.
        rate_h = demand_high_week[t] / S_high if S_high > 0 else 0.0
        rate_l = demand_low_week[t]  / S_low  if S_low  > 0 else 0.0
        target_h = max(d_h, int(round(COVER_TARGET_WEEKS * rate_h * S_high)))
        target_l = max(d_l, int(round(COVER_TARGET_WEEKS * rate_l * S_low)))
        need_h = max(0, target_h - stock_high)
        need_l = max(0, target_l - stock_low)
        total_need = need_h + need_l
        if total_need > 0 and wh > 0:
            if total_need <= wh:
                ship_h, ship_l = need_h, need_l
            else:
                ship_h = min(need_h, wh)        # HIGH first
                ship_l = wh - ship_h
            stock_high += ship_h
            stock_low  += ship_l
            wh -= (ship_h + ship_l)

        # 3. sales -- capped by integer stock (now topped up)
        sold_h = min(stock_high, d_h)
        sold_l = min(stock_low,  d_l)
        stock_high -= sold_h
        stock_low  -= sold_l
        lost_h = d_h - sold_h
        lost_l = d_l - sold_l

        # 4. Decompose stock on hand against demand STILL to come.
        rem_h = total_demand_high - cum_high_int
        rem_l = total_demand_low  - cum_low_int
        hc, lc, wp, over = _decomp(stock_high, stock_low, wh, rem_h, rem_l)

        # Per-tier coverage: integer stock divided by store count, capped at 1.
        pct_h = min(1.0, stock_high / S_high) if S_high > 0 else 0.0
        pct_l = min(1.0, stock_low  / S_low ) if S_low  > 0 else 0.0

        states.append(WeekState(t + 1, wh, stock_high, stock_low,
                                 sold_h, sold_l, lost_h, lost_l,
                                 pct_h, pct_l, hc, lc, wp, over))

    # ── Season totals ──
    sold  = sum(s.sold_high + s.sold_low for s in states)
    lost  = sum(s.lost_high + s.lost_low for s in states)
    stuck = states[-1].wh + states[-1].stock_high_total + states[-1].stock_low_total
    sales_eur = sold * price
    cogs      = N * var_cost           # you paid for the whole buy
    margin    = sales_eur - cogs
    margin_pct = (margin / sales_eur * 100.0) if sales_eur > 0 else 0.0
    st_pct    = (sold / N * 100.0) if N > 0 else 0.0

    return {
        "states":            states,
        "horizon":           horizon,
        "S_chosen":          S_chosen,    # what the user asked for
        "S_effective":       S,           # what the model actually used
        "S_high":            S_high,
        "S_low":             S_low,
        "M_high":            M_high,
        "M_low":             M_low,
        "bought":            N,
        "sold":              sold,
        "lost":              lost,
        "stuck":             stuck,
        "sales_eur":         sales_eur,
        "margin":            margin,
        "margin_pct":        margin_pct,
        "sell_through_pct":  st_pct,
        "demand_curve":      weights,
    }


def _empty_result(M, S, N, horizon, S_high, S_low, price, var_cost):
    wh0 = int(max(0, N - S))
    states = [WeekState(t, wh0, int(S_high), int(S_low),
                        0, 0, 0, 0,
                        1.0 if S_high > 0 else 0.0,
                        1.0 if S_low  > 0 else 0.0,
                        int(S_high), int(S_low), 0, wh0)
              for t in range(horizon + 1)]
    return {
        "states":           states, "horizon": horizon,
        "S_high": S_high, "S_low": S_low,
        "M_high": int(round(M * HIGH_SHARE)),
        "M_low":  M - int(round(M * HIGH_SHARE)),
        "bought": N, "sold": 0, "lost": 0,
        "stuck":  float(N), "sales_eur": 0.0,
        "margin": -N * var_cost, "margin_pct": 0.0,
        "sell_through_pct": 0.0,
        "demand_curve": np.zeros(horizon),
    }


# ───────────────────────────── grid sweep ─────────────────────────────────

def simulate_grid(maison_size: int,
                  sku_networks: list[int], buys: list[int],
                  lifespan_months: float, profile: str,
                  price: float, var_cost: float) -> dict:
    """Run simulate() over every (sku_network, buy) cell.

    Returns 2-D arrays (rows = sku_networks, cols = buys) for the three
    metrics the Reveal 2 matrix highlights:
      sales_eur, margin, margin_pct
    Plus the aggregate sold and stuck for tooltips.
    """
    NR, NC = len(sku_networks), len(buys)
    sales = np.zeros((NR, NC))
    margin = np.zeros((NR, NC))
    margin_pct = np.zeros((NR, NC))
    sold = np.zeros((NR, NC))
    stuck = np.zeros((NR, NC))

    for i, S in enumerate(sku_networks):
        for j, N in enumerate(buys):
            r = simulate(maison_size, S, N, lifespan_months, profile,
                         price=price, var_cost=var_cost)
            sales[i, j]      = r["sales_eur"]
            margin[i, j]     = r["margin"]
            margin_pct[i, j] = r["margin_pct"]
            sold[i, j]       = r["sold"]
            stuck[i, j]      = r["stuck"]
    return dict(sales=sales, margin=margin, margin_pct=margin_pct,
                sold=sold, stuck=stuck,
                sku_networks=sku_networks, buys=buys)
