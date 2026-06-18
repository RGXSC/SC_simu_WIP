"""Monte-Carlo 'where should the stock sit?' engine — N SKUs x M stores.

This is the stochastic, multi-SKU / multi-store generalisation of
``sim_stash``. The teaching point is identical and only gets sharper with
scale: you buy the right TOTAL quantity, but you cannot know in advance
which SKU will be a winner or which store will sell it, so splitting the
whole buy into stores on day 1 strands stock in the cold cells and starves
the hot ones. Keeping a slice central lets the warehouse send units to
wherever demand actually showed up.

Demand model (decided with the user)
------------------------------------
Everything is flat across the 26 weeks; the only randomness is the
per-(SKU, store) sell-rate, formed by multiplying two mean-1 draws:

    rate[run, k, m] = baseline * sku_strength[run, k] * store_share[run, k, m]

  * baseline          = forecast_per_week / (n_sku * n_store)   (uniform —
                        the buyer's equal-split belief, knowing nothing)
  * sku_strength[k]   ~ Dist 1  ("actual vs forecast"): one draw per SKU,
                        shared by all of that SKU's stores. Mean 1, so the
                        realised season total can land above OR below the
                        forecast (aggregate forecast error emerges naturally).
  * store_share[k,m]  ~ Dist 2  ("store vs average"): one draw per
                        (SKU, store) couple. Mean 1.

Both draws have mean 1, so E[rate] = baseline and E[season total] =
forecast. Each rate is held flat for all weeks (drawn once per run).

Policies compared (per SKU, independent warehouses, then aggregated)
--------------------------------------------------------------------
  * DUMP:  whole buy split evenly into stores on day 1, warehouse empty,
           no replenishment.
  * KEEP:  hold ``hold_pct`` of each SKU's buy central; the rest split
           evenly into stores on day 1; each week the warehouse tops every
           store up to ``COVER_TARGET_WEEKS`` of its (discovered, flat)
           demand, water-filling proportionally to need when it cannot
           cover everyone.

Two invariants are asserted every run (same discipline as sim_stash):
  * no hoarding   — after ordering, every store is at its cover target
                    unless its SKU's warehouse is fully drained.
  * conservation  — bought == sold + on-hand at end (nothing is stranded in
                    transit because the final week places no order).
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np

WEEKS              = 26
WAREHOUSE_LT       = 1      # weeks (order placed this week lands next week)
COVER_TARGET_WEEKS = 2      # warehouse tops stores up to 2 weeks of cover
PRICE              = 10.0
VAR_COST           = 5.0

# Tiny tolerance for float mass-conservation checks.
_EPS = 1e-6


# ─────────────────────────── demand draws ──────────────────────────────

def draw_mean1(rng: np.random.Generator, family: str, cv: float,
               size: tuple[int, ...]) -> np.ndarray:
    """Return positive draws with mean 1 and coefficient of variation ``cv``.

    cv = std / mean. cv = 0 returns an exact array of ones (deterministic),
    which lets the page show the no-noise baseline cleanly.

    family: 'lognormal' | 'gamma' | 'truncnormal' (user-selectable).
    """
    if cv <= 0:
        return np.ones(size)

    if family == "lognormal":
        # mean-1 lognormal: sigma^2 = ln(1+cv^2), mu = -sigma^2/2
        sigma = np.sqrt(np.log(1.0 + cv * cv))
        mu    = -0.5 * sigma * sigma
        return np.exp(rng.normal(mu, sigma, size))

    if family == "gamma":
        # mean-1 gamma: shape = 1/cv^2, scale = cv^2  (mean = shape*scale = 1)
        shape = 1.0 / (cv * cv)
        return rng.gamma(shape, cv * cv, size)

    if family == "truncnormal":
        # symmetric noise clipped at ~0. Clipping nudges the mean up a touch
        # at high cv; we re-centre so the realised mean stays ~1.
        x = rng.normal(1.0, cv, size)
        np.clip(x, 1e-3, None, out=x)
        return x / x.mean()

    raise ValueError(f"unknown distribution family: {family!r}")


# ─────────────────────────── result container ──────────────────────────

@dataclass
class MCResult:
    """Per-run aggregate outcomes (length-``runs`` arrays) for both policies."""
    runs: int
    dump_margin: np.ndarray   # per run
    keep_margin: np.ndarray
    dump_sold: np.ndarray
    keep_sold: np.ndarray
    dump_lost: np.ndarray
    keep_lost: np.ndarray
    dump_stuck: np.ndarray
    keep_stuck: np.ndarray
    bought: int               # same for every run (a planning decision)
    meta: dict = field(default_factory=dict)

    @property
    def gap(self) -> np.ndarray:
        """Margin advantage of keeping stock central, per run."""
        return self.keep_margin - self.dump_margin


# ───────────────────────────── the engine ──────────────────────────────

def _run_policy(rate: np.ndarray, store0: np.ndarray, wh0: np.ndarray,
                replenish: bool, check_invariants: bool = True):
    """Simulate every (run, SKU, store) for WEEKS weeks under one policy.

    rate   : (R, K, M) flat weekly sell-rate per cell.
    store0 : (R, K, M) day-1 store stock.
    wh0    : (R, K)    day-1 warehouse stock per SKU.
    replenish: KEEP policy if True; DUMP (warehouse idle) if False.
    check_invariants: per-week no-hoarding assert. Cheap on small grids but
                      doubles work on big ones, so callers running a parameter
                      sweep can disable it after the smoke run has passed.

    Returns (sold, lost, stuck) — each summed to a per-run total (R,).
    """
    stores      = store0.astype(float).copy()
    wh          = wh0.astype(float).copy()
    in_transit  = np.zeros_like(stores)
    target      = rate * COVER_TARGET_WEEKS          # (R,K,M), flat → constant

    sold_tot = np.zeros(rate.shape[0])
    lost_tot = np.zeros(rate.shape[0])

    for w in range(WEEKS):
        # 1. arrivals from last week's order (1-wk lead time)
        stores += in_transit

        # 2. sales (capped by what's on the shelf)
        sold = np.minimum(stores, rate)
        stores -= sold
        sold_tot += sold.sum(axis=(1, 2))
        lost_tot += (rate - sold).sum(axis=(1, 2))

        # 3. replenishment — KEEP only, and never on the final week (a
        #    shipment then could not land within the season).
        in_transit = np.zeros_like(stores)
        place_orders = replenish and (w < WEEKS - 1)
        if place_orders:
            need       = np.maximum(0.0, target - stores)        # (R,K,M)
            total_need = need.sum(axis=2)                        # (R,K)
            # If the warehouse can cover everyone, fill to target; else
            # water-fill proportionally to need and drain the warehouse.
            with np.errstate(divide="ignore", invalid="ignore"):
                frac = np.where(total_need > 0,
                                np.minimum(1.0, wh / total_need), 0.0)
            ship = need * frac[:, :, None]                       # (R,K,M)
            in_transit = ship
            wh -= ship.sum(axis=2)

            # Invariant: no hoarding. After ordering, every store is at its
            # target unless the SKU warehouse is drained to ~0.
            if check_invariants:
                pos = stores + ship
                ok = (wh <= _EPS) | np.all(pos >= target - 1e-4, axis=2)
                assert ok.all(), "warehouse hoarding: stock held while a store sits below cover"

    stuck = wh.sum(axis=1) + stores.sum(axis=(1, 2))             # (R,)
    return sold_tot, lost_tot, stuck


def simulate_mc(forecast_per_week: float,
                n_sku: int,
                n_store: int,
                hold_pct: float,
                target_sell_through: float,
                dist1_family: str = "lognormal", dist1_cv: float = 0.6,
                dist2_family: str = "lognormal", dist2_cv: float = 0.6,
                runs: int = 10_000,
                price: float = PRICE, var_cost: float = VAR_COST,
                fixed_cost: float = 0.0,
                seed: int | None = 0,
                compute_dump: bool = True,
                check_invariants: bool = True) -> MCResult:
    """Run ``runs`` Monte-Carlo seasons for both policies and aggregate.

    forecast_per_week  : assortment-wide forecast units/week (all SKUs+stores).
    hold_pct           : 0..1 fraction kept central in the KEEP policy.
    target_sell_through: 0..1; total buy = forecast_total / target.
    dist*_cv           : coefficient of variation of each mean-1 noise source.
    """
    rng = np.random.default_rng(seed)
    R, K, M = runs, int(n_sku), int(n_store)

    # ── demand draws (flat across weeks; held per run) ──
    baseline = forecast_per_week / (K * M)
    sku_strength = draw_mean1(rng, dist1_family, dist1_cv, (R, K, 1))     # per SKU
    store_share  = draw_mean1(rng, dist2_family, dist2_cv, (R, K, M))     # per cell
    rate = baseline * sku_strength * store_share                         # (R,K,M)

    # ── the buy (one planning number, identical across runs) ──
    forecast_total = forecast_per_week * WEEKS
    bought = int(round(forecast_total / max(target_sell_through, 1e-9)))
    # Split equally across cells — the buyer's uniform prior. Each SKU gets an
    # equal slice; KEEP holds hold_pct of that slice central.
    buy_per_sku  = bought / K
    wh_per_sku   = buy_per_sku * hold_pct
    to_stores_k  = buy_per_sku - wh_per_sku
    per_store    = to_stores_k / M

    store0 = np.full((R, K, M), per_store)
    # DUMP: nothing central, whole slice spread into stores.
    dump_store0 = np.full((R, K, M), buy_per_sku / M)
    dump_wh0    = np.zeros((R, K))
    keep_wh0    = np.full((R, K), wh_per_sku)

    if compute_dump:
        d_sold, d_lost, d_stuck = _run_policy(rate, dump_store0, dump_wh0,
                                              replenish=False,
                                              check_invariants=check_invariants)
    else:
        # NaN, not 0, so any downstream code that forgets to gate on
        # compute_dump (e.g. MCResult.gap) loudly produces NaN rather than
        # silently lying. The table loop never reads these.
        d_sold = d_lost = d_stuck = np.full(R, np.nan)
    k_sold, k_lost, k_stuck = _run_policy(rate, store0, keep_wh0,
                                          replenish=True,
                                          check_invariants=check_invariants)

    # ── conservation invariant (bought == sold + on-hand, per run) ──
    if check_invariants:
        bought_total = float(bought)  # both policies buy the same total
        if compute_dump:
            assert np.allclose(d_sold + d_stuck, bought_total, atol=1e-3), "DUMP mass not conserved"
        assert np.allclose(k_sold + k_stuck, bought_total, atol=1e-3), "KEEP mass not conserved"

    def margin(sold, stuck):
        turnover = sold * price
        cogs     = (sold + stuck) * var_cost     # pay for everything bought
        return turnover - cogs - fixed_cost

    return MCResult(
        runs=R,
        dump_margin=margin(d_sold, d_stuck), keep_margin=margin(k_sold, k_stuck),
        dump_sold=d_sold, keep_sold=k_sold,
        dump_lost=d_lost, keep_lost=k_lost,
        dump_stuck=d_stuck, keep_stuck=k_stuck,
        bought=bought,
        meta=dict(n_sku=K, n_store=M, hold_pct=hold_pct,
                  forecast_per_week=forecast_per_week,
                  target_sell_through=target_sell_through,
                  dist1=(dist1_family, dist1_cv), dist2=(dist2_family, dist2_cv)),
    )


def summarise(res: MCResult) -> dict:
    """Headline stats for the page (percentiles of the keep-vs-dump gap)."""
    gap = res.gap
    return {
        "bought":        res.bought,
        "mean_gap":      float(gap.mean()),
        "p10_gap":       float(np.percentile(gap, 10)),
        "p50_gap":       float(np.percentile(gap, 50)),
        "p90_gap":       float(np.percentile(gap, 90)),
        "keep_wins_pct": float((gap > 0).mean() * 100.0),
        "dump_margin_mean": float(res.dump_margin.mean()),
        "keep_margin_mean": float(res.keep_margin.mean()),
        "dump_sellthrough": float(res.dump_sold.mean() / res.bought * 100.0),
        "keep_sellthrough": float(res.keep_sold.mean() / res.bought * 100.0),
        "dump_lost_mean":   float(res.dump_lost.mean()),
        "keep_lost_mean":   float(res.keep_lost.mean()),
    }
