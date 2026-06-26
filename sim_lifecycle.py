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
  * Each week stores sell up to their tier rate (capped by stock). The
    warehouse refills proportionally to demand-aware need (target = 2 weeks
    of cover), 1-week lead time.
  * No randomness — same inputs always give the same outcome.

Outputs:
  * Per-week state for the page's Reveal 1 chart (stock split into WH /
    HIGH-tier total / LOW-tier total; per-tier sales, lost sales, % stocked).
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
COVER_TARGET_WEEKS = 2
WAREHOUSE_LT     = 1
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
    """Stock + flow snapshot at the end of a week (or week 0 = pre-sales)."""
    week:              int    # 0..horizon
    wh:                float  # warehouse stock at end of week
    stock_high_total:  float  # sum of stock across the S_high HIGH-tier stores
    stock_low_total:   float  # sum of stock across the S_low LOW-tier stores
    sold_high:         float  # sales by the HIGH tier this week
    sold_low:          float
    lost_high:         float  # demand the HIGH tier couldn't fulfil this week
    lost_low:          float
    pct_high_stocked:  float  # fraction of HIGH stores still able to sell (0..1)
    pct_low_stocked:   float  # same for LOW stores


# ───────────────────────────── the engine ─────────────────────────────────

def simulate(maison_size: int, sku_network: int, buy: int,
             lifespan_months: float, profile: str,
             price: float = 100.0, var_cost: float = 30.0) -> dict:
    """Run the deterministic lifecycle for one SKU and return per-week state.

    All inputs validated/clipped to safe ranges so the function is total."""
    M = max(1, int(maison_size))
    S = max(0, min(int(sku_network), M))
    N = max(0, int(buy))
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
    demand_high_week = N * weights * (S_high * HIGH_RATE_MULT) / footprint
    demand_low_week  = N * weights * (S_low  * 1.0           ) / footprint

    # Per-STORE rate (used by the planner's smart target). Same value every
    # week if the profile were flat; here it tracks the curve so the refill
    # target is "2 weeks of the upcoming demand".
    def _rate_high(t):
        # use this week's per-store rate as the planner's belief about cover
        return demand_high_week[t] / S_high if S_high > 0 else 0.0
    def _rate_low(t):
        return demand_low_week[t]  / S_low  if S_low  > 0 else 0.0

    # Day-1 allocation: 1 unit per SKU-network store, remainder in WH.
    stock_high = float(S_high)
    stock_low  = float(S_low)
    wh         = float(max(0, N - S))
    in_transit_high = 0.0
    in_transit_low  = 0.0

    states: list[WeekState] = [
        WeekState(0, wh, stock_high, stock_low, 0, 0, 0, 0,
                  1.0 if S_high > 0 else 0.0,
                  1.0 if S_low  > 0 else 0.0)
    ]

    for t in range(horizon):
        # 1. arrivals from last week's order (1-week LT)
        stock_high += in_transit_high
        stock_low  += in_transit_low

        # 2. sales — capped by stock
        d_h = demand_high_week[t]
        d_l = demand_low_week[t]
        sold_h = min(stock_high, d_h)
        sold_l = min(stock_low,  d_l)
        stock_high -= sold_h
        stock_low  -= sold_l
        lost_h = d_h - sold_h
        lost_l = d_l - sold_l

        # 3. refill from WH (planner smart, 2-wk target, water-fill).
        #    Last week places no order — it could never land in season.
        in_transit_high = in_transit_low = 0.0
        place_orders = (t < horizon - 1) and (wh > 0)
        if place_orders:
            target_h_total = COVER_TARGET_WEEKS * _rate_high(t) * S_high
            target_l_total = COVER_TARGET_WEEKS * _rate_low(t)  * S_low
            need_h = max(0.0, target_h_total - stock_high)
            need_l = max(0.0, target_l_total - stock_low)
            total_need = need_h + need_l
            if total_need > 0:
                frac = min(1.0, wh / total_need)
                in_transit_high = need_h * frac
                in_transit_low  = need_l * frac
                wh -= (in_transit_high + in_transit_low)

        # Per-tier coverage: aggregate stock divided by stores. Capped at 1
        # because >1 unit/store still reads as "100% covered".
        pct_h = min(1.0, stock_high / S_high) if S_high > 0 else 0.0
        pct_l = min(1.0, stock_low  / S_low ) if S_low  > 0 else 0.0

        states.append(WeekState(t + 1, wh, stock_high, stock_low,
                                 sold_h, sold_l, lost_h, lost_l,
                                 pct_h, pct_l))

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
    states = [WeekState(t, float(max(0, N - S)),
                        float(S_high), float(S_low),
                        0, 0, 0, 0,
                        1.0 if S_high > 0 else 0.0,
                        1.0 if S_low  > 0 else 0.0)
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
