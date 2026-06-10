"""Shared, UI-free helpers used by both the main app and the Regional page.

This module must NEVER import streamlit or execute UI code: pages import it,
and importing a Streamlit script executes its entire UI into the calling page.
"""
from math import gamma as _gamma_fn, exp as _math_exp

# Store-tier structure (share of store count) and sell-rate weights
TIER_SHARE   = {"small": 0.60, "medium": 0.30, "high": 0.10}
TIER_WEIGHTS = {"small": 0.5,  "medium": 1.0,  "high": 4.0}

# Seasonal sub-profile parameters: (peak_position_ratio, shape_k)
# theta is computed from ratio × weeks / (k - 1); peak position scales with sim length
SEASONAL_PARAMS = {
    "Very Steep": (3.0 / 26.0, 2.5),   # peak W3 in 26wk, ~4× avg, fast decay
    "Steep":      (6.0 / 26.0, 3.0),   # peak W6 in 26wk, ~2.4× avg
    "~Flat":      (6.0 / 26.0, 1.8),   # peak W6, ~1.6× avg, gentle dome (tail ~35%)
}


def gamma_pdf(x: float, k: float, theta: float) -> float:
    """Gamma PDF value at x (shape k, scale theta). Returns 0 for x <= 0."""
    if x <= 0:
        return 0.0
    return (x ** (k - 1)) * _math_exp(-x / theta) / ((theta ** k) * _gamma_fn(k))


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
