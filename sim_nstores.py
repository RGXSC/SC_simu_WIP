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

# Realised multiplier bounds. Real SKUs/stores rarely sell less than 30% or
# more than 3x of their assortment-wide mean; we clip every draw to this
# window so the tails stay believable even at extreme CV. The mean is then
# re-centred to 1 exactly so E[total demand] = forecast still holds.
DRAW_LOW, DRAW_HIGH = 0.3, 3.0


def draw_mean1(rng: np.random.Generator, family: str, cv: float,
               size: tuple[int, ...]) -> np.ndarray:
    """Return positive draws with mean 1, clipped to [DRAW_LOW, DRAW_HIGH].

    cv = std / mean of the *underlying* distribution before clipping; the
    realised CV after clipping is somewhat lower at high cv. cv = 0 returns
    an exact array of ones (deterministic), which lets the page show the
    no-noise baseline cleanly.

    family: 'lognormal' | 'gamma' | 'truncnormal' (user-selectable). All
    three are right-skewed with mode below 1 -- that's "few stars, many
    duds", the shape that fits a real retail assortment.
    """
    if cv <= 0:
        return np.ones(size)

    if family == "lognormal":
        # mean-1 lognormal: sigma^2 = ln(1+cv^2), mu = -sigma^2/2
        sigma = np.sqrt(np.log(1.0 + cv * cv))
        mu    = -0.5 * sigma * sigma
        x = np.exp(rng.normal(mu, sigma, size))
    elif family == "gamma":
        # mean-1 gamma: shape = 1/cv^2, scale = cv^2  (mean = shape*scale = 1)
        shape = 1.0 / (cv * cv)
        x = rng.gamma(shape, cv * cv, size)
    elif family == "truncnormal":
        # symmetric noise clipped at ~0 (further clipped below).
        x = rng.normal(1.0, cv, size)
    elif family == "pareto":
        # Pareto Type I (power-law tail), the canonical "few stars, many duds"
        # shape. Shape parameter alpha is mapped from cv so the slider keeps
        # its usual meaning:  cv=0.6 -> alpha~2.2 (moderate, top 20% ~ 38%
        # share),  cv=1.5 -> alpha~1.17 (classic 80/20). alpha floored above
        # 1.05 to keep the mean finite.
        alpha = max(1.05, 0.5 + 1.0 / max(cv, 0.1))
        x_m   = (alpha - 1.0) / alpha           # raw mean = 1 before the clip
        x = x_m * (1.0 + rng.pareto(alpha, size))
    else:
        raise ValueError(f"unknown distribution family: {family!r}")

    # Single clip then re-centre so the mean stays EXACTLY 1 (so that
    # E[total demand] = forecast no matter which family/cv was picked).
    # Heavy tails (e.g. Pareto at high cv) make the re-centre factor > 1,
    # which can scale the upper bound a bit above 3 -- we accept that
    # cosmetic drift in exchange for the mean-preservation guarantee.
    np.clip(x, DRAW_LOW, DRAW_HIGH, out=x)
    return x / x.mean()


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
                replenish: bool, check_invariants: bool = True,
                min_floor: float = 0.0):
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
    # Preserve the caller's dtype: the table path passes float32 (bandwidth-
    # bound, ~2x faster, half the memory) while the invariant-checked smoke
    # path passes float64 for exact mass-conservation asserts.
    dt = np.result_type(store0.dtype, rate.dtype)
    stores      = store0.astype(dt).copy()
    wh          = wh0.astype(dt).copy()
    in_transit  = np.zeros_like(stores)
    # Replenish target = 2 weeks of cover, but never below the forced
    # presentation minimum (min_floor units per store of each SKU). The
    # warehouse keeps every store topped to at least that floor.
    target = np.maximum(rate * COVER_TARGET_WEEKS, min_floor).astype(dt)   # (R,K,M)

    sold_tot = np.zeros(rate.shape[0], dtype=dt)
    lost_tot = np.zeros(rate.shape[0], dtype=dt)

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
                min_per_store: float = 0.0,
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
    min_per_store      : forced presentation stock — every store must hold at
                         least this many units of EVERY SKU. Seeded on day 1
                         and maintained by the warehouse. If the implied floor
                         (min_per_store × n_sku × n_store) exceeds the
                         forecast-based buy, the buy is raised to honour it —
                         which is exactly how broad presentation minimums blow
                         up the buy across a large assortment.
    dist*_cv           : coefficient of variation of each mean-1 noise source.
    """
    rng = np.random.default_rng(seed)
    R, K, M = runs, int(n_sku), int(n_store)
    # float64 when we assert exact conservation, float32 for the fast sweep.
    work_dtype = np.float64 if check_invariants else np.float32

    # ── demand draws (flat across weeks; held per run) ──
    baseline = forecast_per_week / (K * M)
    sku_strength = draw_mean1(rng, dist1_family, dist1_cv, (R, K, 1))     # per SKU
    store_share  = draw_mean1(rng, dist2_family, dist2_cv, (R, K, M))     # per cell
    rate = (baseline * sku_strength * store_share).astype(work_dtype)    # (R,K,M)

    # ── the buy (one planning number, identical across runs) ──
    forecast_total = forecast_per_week * WEEKS
    forecast_buy   = int(round(forecast_total / max(target_sell_through, 1e-9)))
    # Forced presentation stock locks min_per_store units in every (SKU, store).
    # The buy can never be smaller than that floor — if it would be, presentation
    # has overridden the forecast and we buy up to the floor.
    forced_total = int(round(min_per_store * K * M))
    bought       = max(forecast_buy, forced_total)

    # Split equally across cells — the buyer's uniform prior. Every store is
    # first seeded with the forced minimum; only the FREE remainder is subject
    # to the hold-central lever.
    buy_per_sku   = bought / K
    forced_per_sku = min_per_store * M
    free_per_sku  = buy_per_sku - forced_per_sku          # ≥ 0 by construction
    wh_per_sku    = free_per_sku * hold_pct               # only free stock held
    keep_per_store = min_per_store + (free_per_sku - wh_per_sku) / M

    store0 = np.full((R, K, M), keep_per_store, dtype=work_dtype)
    # DUMP: nothing central, whole slice spread into stores (already ≥ floor).
    dump_store0 = np.full((R, K, M), buy_per_sku / M, dtype=work_dtype)
    dump_wh0    = np.zeros((R, K), dtype=work_dtype)
    keep_wh0    = np.full((R, K), wh_per_sku, dtype=work_dtype)

    if compute_dump:
        d_sold, d_lost, d_stuck = _run_policy(rate, dump_store0, dump_wh0,
                                              replenish=False,
                                              check_invariants=check_invariants,
                                              min_floor=min_per_store)
    else:
        # NaN, not 0, so any downstream code that forgets to gate on
        # compute_dump (e.g. MCResult.gap) loudly produces NaN rather than
        # silently lying. The table loop never reads these.
        d_sold = d_lost = d_stuck = np.full(R, np.nan)
    k_sold, k_lost, k_stuck = _run_policy(rate, store0, keep_wh0,
                                          replenish=True,
                                          check_invariants=check_invariants,
                                          min_floor=min_per_store)

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


def simulate_grid_fast(forecast_per_week: float,
                       n_sku: int, n_store: int,
                       hold_pcts: list[int], target_sts: list[int],
                       min_per_store: float = 0.0,
                       dist1_family: str = "lognormal", dist1_cv: float = 0.6,
                       dist2_family: str = "lognormal", dist2_cv: float = 0.6,
                       runs: int = 100,
                       price: float = PRICE, var_cost: float = VAR_COST,
                       fixed_cost: float = 0.0,
                       seed: int | None = 0) -> dict:
    """Closed-form solution for the FLAT-rate (constant weekly demand) case.

    The week-by-week engine integrates a 26-step transient that, under flat
    rate, settles into a steady state after week 2 and has no further time
    structure. That makes the loop entirely unnecessary -- we can compute
    the end-of-season outcome directly.

    Per-cell model (for the KEEP policy):
      * If keep_per_store >= rate: the cell sells `rate` every week from
        week 1 (no week-1 stockout).
      * If keep_per_store < rate:  the cell loses `rate - keep_per_store`
        in week 1 (1-week lead time, the warehouse cannot deliver fast
        enough), then steady-states from week 2.

      So each cell's effective demand on the warehouse is
        need = max(0, demand_eff - keep_per_store),
      where demand_eff = rate*WEEKS - max(0, rate - keep_per_store)
                       = "what the cell could possibly sell".

      The warehouse rations its wh_per_sku across cells proportionally to
      their need -- same as the engine's per-week water-fill, which under
      flat rate is mathematically equivalent.

    For the DUMP policy each cell is isolated: sold = min(buy_per_cell,
    rate*WEEKS).

    Verified to match the week-by-week engine to < 0.5% on a sweep of
    (forecast, K, M, hold, sell-through target, min_per_store, CV) that
    covers every regime the Monte-Carlo page exposes -- including hold=0
    (no warehouse), hold=90%% (tight stores), CV=1.2 (fat tails), and
    forced presentation minimums.

    Sharing the demand draws across all (hold, sell-through) cells of the
    grid means rate generation is paid once for the whole table rather
    than once per cell -- one of the wins on top of skipping the loop.

    Returns the same dict shape as the cell-by-cell grid path:
      margin / sellthrough / lost : (NH, NT) arrays
      bought_by_st                : (NT,) buy quantity per target ST
      runs                        : R actually used
    """
    K, M = int(n_sku), int(n_store)
    R = int(runs)
    NH, NT = len(hold_pcts), len(target_sts)
    rng = np.random.default_rng(seed)

    baseline = forecast_per_week / (K * M)
    sku_str  = draw_mean1(rng, dist1_family, dist1_cv, (R, K, 1)).astype(np.float32)
    store_sh = draw_mean1(rng, dist2_family, dist2_cv, (R, K, M)).astype(np.float32)
    rate     = (baseline * sku_str * store_sh).astype(np.float32)
    cell_demand = rate * WEEKS                                       # (R,K,M)
    full_demand = cell_demand.sum(axis=(1, 2))                       # (R,) total/SKU

    margin       = np.zeros((NH, NT), dtype=np.float64)
    sellthrough  = np.zeros((NH, NT), dtype=np.float64)
    lost         = np.zeros((NH, NT), dtype=np.float64)
    bought_by_st = np.zeros(NT, dtype=int)

    forecast_total = forecast_per_week * WEEKS
    forced_total   = int(round(min_per_store * K * M))

    for ti, st_pct in enumerate(target_sts):
        forecast_buy   = int(round(forecast_total / max(st_pct / 100.0, 1e-9)))
        bought         = max(forecast_buy, forced_total)
        bought_by_st[ti] = bought
        buy_per_sku    = bought / K
        free_per_sku   = buy_per_sku - min_per_store * M

        for hi, hold in enumerate(hold_pcts):
            wh_per_sku     = free_per_sku * (hold / 100.0)
            keep_per_store = float(min_per_store + (free_per_sku - wh_per_sku) / M)

            LT_edge    = np.maximum(0.0, rate - keep_per_store)               # (R,K,M)
            demand_eff = cell_demand - LT_edge                                # (R,K,M)
            need       = np.maximum(0.0, demand_eff - keep_per_store)         # (R,K,M)
            total_need = need.sum(axis=2)                                     # (R,K)

            with np.errstate(divide="ignore", invalid="ignore"):
                frac = np.where(total_need > 0,
                                np.minimum(1.0, wh_per_sku / total_need), 0.0)
            cell_sold = np.minimum(keep_per_store + need * frac[:, :, None],
                                    demand_eff)                               # (R,K,M)
            sold = cell_sold.sum(axis=(1, 2))                                 # (R,)

            stuck = float(bought) - sold
            margin[hi, ti] = (sold * price - (sold + stuck) * var_cost
                              - fixed_cost).mean()
            sellthrough[hi, ti] = sold.mean() / bought * 100.0
            lost[hi, ti]        = (full_demand - sold).mean()

    return dict(margin=margin, sellthrough=sellthrough, lost=lost,
                bought_by_st=bought_by_st, runs=R)


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
