"""Tiny 'where should the stock sit?' engine for the teaching page.

Two policies, same buy, same demand, only difference = where the stock
starts (in shops on day 1 vs partly held central). The lesson is that
splitting evenly across shops with unequal demand leaves stock stuck in
the small shop AND missing in the big one, even when you bought the
right total quantity.

Returns two state-vector lists (one per policy), each of length weeks+1,
indexable as states[w] = end of week w. State at index 0 is the W0
allocation, before any sales have happened.
"""
from __future__ import annotations
from dataclasses import dataclass

# Fixed parameters for the teaching demo. Demand levels AND the Big/Small
# split are passed into simulate() so the page can drive them from sliders.
WEEKS              = 26
WAREHOUSE_LT       = 1    # weeks
COVER_TARGET_WEEKS = 2    # warehouse refills shops up to 2 wks of cover
PRICE              = 10.0
VAR_COST           = 5.0


@dataclass
class WeekState:
    """End-of-week snapshot used by the front-end animation."""
    wh: int            # warehouse stock
    big: int           # big-shop stock
    small: int         # small-shop stock
    sold_big: int      # this week's sales — big
    sold_small: int    # this week's sales — small
    lost_big: int      # this week's missed demand — big
    lost_small: int    # this week's missed demand — small
    in_transit_big: int    # units warehouse-> big shop, arriving NEXT week
    in_transit_small: int  # same, for small


def _step(wh, big, small, in_big, in_small, can_replenish: bool,
          big_rate: int, small_rate: int):
    """Run one week. Returns (new state values, sales/lost split)."""
    # 1. Arrivals from last week's order (1-wk LT)
    big   += in_big
    small += in_small

    # 2. Sales
    sold_big   = min(big,   big_rate)
    sold_small = min(small, small_rate)
    lost_big   = big_rate   - sold_big
    lost_small = small_rate - sold_small
    big   -= sold_big
    small -= sold_small

    # 3. Replenishment from warehouse (observes sales, refills to target).
    # In the 'dump-everything' policy the warehouse is empty and there
    # is nothing to send — we just leave the orders at 0.
    new_in_big = new_in_small = 0
    if can_replenish and wh > 0:
        target_big   = big_rate   * COVER_TARGET_WEEKS
        target_small = small_rate * COVER_TARGET_WEEKS
        need_big   = max(0, target_big   - big   - in_big)
        need_small = max(0, target_small - small - in_small)
        # Big shop is the more urgent one (higher rate); serve it first
        # if the warehouse can't cover both. Simple, predictable, and
        # what a sensible operator would do.
        if need_big + need_small <= wh:
            new_in_big, new_in_small = need_big, need_small
        else:
            new_in_big   = min(need_big, wh)
            new_in_small = wh - new_in_big
        wh -= (new_in_big + new_in_small)

    return wh, big, small, sold_big, sold_small, lost_big, lost_small, new_in_big, new_in_small


def simulate(bought: int, hold_pct: float,
             big_rate: int, small_rate: int) -> list[WeekState]:
    """Run one policy for 26 weeks.

    bought: total units bought (= forecast × stock%)
    hold_pct: 0..1, fraction kept in the warehouse at week 0. The rest is
        split 50/50 between the two shops on day 1.
    big_rate / small_rate: flat actual demand per week, per shop.
    """
    wh        = int(round(bought * hold_pct))
    to_shops  = bought - wh
    # 50/50 because the operator can't tell the shops apart at W0
    big   = to_shops // 2
    small = to_shops - big
    in_big = in_small = 0

    can_replenish = hold_pct > 0
    states: list[WeekState] = [
        WeekState(wh, big, small, 0, 0, 0, 0, 0, 0)  # W0 = pre-sales snapshot
    ]
    for _ in range(WEEKS):
        wh, big, small, sb, ss, lb, ls, in_big, in_small = _step(
            wh, big, small, in_big, in_small, can_replenish,
            big_rate, small_rate)
        states.append(WeekState(wh, big, small, sb, ss, lb, ls, in_big, in_small))
    return states


def totals(states: list[WeekState]) -> dict:
    """Cumulative metrics for the P&L line."""
    sold  = sum(s.sold_big + s.sold_small for s in states)
    lost  = sum(s.lost_big + s.lost_small for s in states)
    stuck = states[-1].wh + states[-1].big + states[-1].small
    return {
        "sold":  sold,
        "lost":  lost,
        "stuck": stuck,
        "turnover": sold * PRICE,
        "margin":   sold * PRICE - (sold + stuck) * VAR_COST,
        # Lost sales valued at MARGIN, not turnover — the "what you could have earned"
        "lost_value": lost * (PRICE - VAR_COST),
    }
