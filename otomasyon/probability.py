"""Probability helpers.

The daily engine ranks coupons by *fair* win probability. Decimal odds embed
the bookmaker margin (overround), so the naive implied probability ``1/odd``
sums to more than 1 across a market's outcomes. We normalise per market to
remove that margin and get a fair probability estimate.

IMPORTANT: fair probabilities derived purely from odds let us maximise *hit
rate*, but they do not by themselves create positive expected value - the
margin makes pure-odds EV slightly negative. Beating the market (positive ROI /
CLV) requires our own model, layered on top later.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence


def implied_prob(odd: float) -> float:
    """Raw implied probability of a single decimal odd (includes margin)."""
    if odd is None or odd <= 1.0:
        raise ValueError(f"decimal odd must be > 1.0, got {odd!r}")
    return 1.0 / odd


def market_overround(odds: Sequence[float]) -> float:
    """Sum of implied probabilities across a market's outcomes (>= 1.0)."""
    return sum(implied_prob(o) for o in odds)


def fair_probs(odds: Sequence[float]) -> list[float]:
    """Margin-free fair probabilities for all outcomes of one market.

    Normalises ``1/odd`` by the market overround so the result sums to 1.0.
    """
    if not odds:
        return []
    overround = market_overround(odds)
    if overround <= 0:
        raise ValueError("overround must be positive")
    return [implied_prob(o) / overround for o in odds]


def fair_prob_for(odds: Sequence[float], index: int) -> float:
    """Fair probability of a single outcome given its market's full odds."""
    return fair_probs(odds)[index]


def combined_probability(probs: Iterable[float]) -> float:
    """Probability that all independent legs win (product of probabilities)."""
    result = 1.0
    for p in probs:
        result *= p
    return result


def total_odds(odds: Iterable[float]) -> float:
    """Combined decimal odds of a multi-leg coupon (product of odds)."""
    result = 1.0
    for o in odds:
        result *= o
    return result


def roi_interval(profits) -> tuple[float, tuple[float, float] | None]:
    """Mean profit with its 95% interval, or no interval when unmeasurable.

    A single settled bet has no spread, so the usual formula collapses to a
    zero-width interval and claims a certainty the data cannot support. Below
    two samples the interval is reported as unknown instead.
    """
    profits = list(profits)
    if not profits:
        return 0.0, None
    roi = statistics.mean(profits)
    if len(profits) < 2:
        return roi, None
    margin = 1.96 * statistics.stdev(profits) / math.sqrt(len(profits))
    if margin == 0.0:
        # Every result identical: there is no observed spread to estimate the
        # uncertainty from, and printing a zero-width range would present that
        # absence as precision.
        return roi, None
    return roi, (roi - margin, roi + margin)


def roi_text(roi: float, interval: tuple[float, float] | None) -> str:
    """Render a ROI figure without implying precision that is not there."""
    if interval is None:
        return f"ROI {roi*100:+.1f}% (aralık için veri yetersiz)"
    return (
        f"ROI {roi*100:+.1f}% "
        f"[95% {interval[0]*100:+.1f}%..{interval[1]*100:+.1f}%]"
    )


def interval_text(interval: tuple[float, float] | None) -> str:
    """Just the interval, for lines that print ROI separately."""
    if interval is None:
        return "%95 veri yetersiz"
    return f"%95 %{interval[0]*100:+.1f}..%{interval[1]*100:+.1f}"
