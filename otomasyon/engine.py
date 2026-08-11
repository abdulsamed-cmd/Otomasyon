"""Daily coupon engine.

The engine builds a **main** and an **alternative** coupon that each:

- land inside the target odds band (``DAILY_MIN_TOTAL_ODDS`` ..
  ``DAILY_MAX_TOTAL_ODDS``, a ~2.00 return);
- use as few legs as can reach that band, from 1 to ``DAILY_MAX_LEGS``;
- take at most one selection per match, so no two legs move together;
- draw only from markets we can grade and that price at a low margin;
- maximise the joint *fair* (margin-free) win probability, i.e. the chance the
  coupon actually lands.

Leg count is an outcome of that objective rather than a setting. Every extra
leg multiplies another market margin into the coupon, so at a fixed payout a
shorter coupon wins strictly more often; the search therefore stops at the
smallest leg count that can reach the band.

The alternative coupon reuses no match from the main coupon.

Nothing here places bets - coupons are informational only.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import combinations

from . import config, probability
from .eligibility import is_event_eligible
from .iddaa.normalize import NormalizedEvent, NormalizedMarket


@dataclass
class Leg:
    event_id: int
    home: str
    away: str
    competition: str
    start_ts: int
    market_code: tuple[int, int]
    market_name: str
    sov: str | None
    outcome_no: int
    outcome_name: str
    odd: float
    fair_prob: float

    @property
    def label(self) -> str:
        return f"{self.home} - {self.away} | {self.market_name}: {self.outcome_name} @ {self.odd}"


@dataclass
class Coupon:
    kind: str  # daily_main | daily_alt
    legs: list[Leg] = field(default_factory=list)

    @property
    def total_odds(self) -> float:
        return probability.total_odds(leg.odd for leg in self.legs)

    @property
    def combined_prob(self) -> float:
        return probability.combined_probability(leg.fair_prob for leg in self.legs)

    @property
    def cumulative_margin(self) -> float:
        """Margin the whole coupon carries, compounded over its legs.

        This is the gap between what the coupon pays and what a fair price
        would pay, and it is the single number the leg count moves.
        """
        priced = self.combined_prob * self.total_odds
        if priced <= 0:
            return 0.0
        return 1.0 / priced - 1.0

    @property
    def event_ids(self) -> set[int]:
        return {leg.event_id for leg in self.legs}


def _market_coverage(market: NormalizedMarket) -> int:
    return config.MARKET_OUTCOME_COVERAGE.get(market.code, 1)


def candidate_legs_for_event(event: NormalizedEvent, probability_provider=None) -> list[Leg]:
    """Every leg an event offers that the coupon is allowed to use.

    All selections of an allowed market are returned, not just its favourite:
    the search needs the selection that lands on the target price, which is
    often not the most likely one.
    """
    legs: list[Leg] = []
    for market in event.markets:
        if market.status != 1:
            continue
        if market.code not in config.DAILY_COUPON_MARKETS:
            continue
        odds = [o for o in market.odds if o and o > 1.0]
        if len(odds) != len(market.selections) or len(odds) < 2:
            continue
        coverage = _market_coverage(market)
        if probability.market_margin(odds, coverage) > config.MARKET_MAX_MARGIN:
            continue
        probs = (
            probability_provider(event, market)
            if probability_provider
            else None
        ) or probability.fair_probs(odds, coverage)
        if len(probs) != len(market.selections):
            continue
        for selection, odd, fair in zip(market.selections, odds, probs):
            if not (config.LEG_MIN_ODD <= odd <= config.LEG_MAX_ODD):
                continue
            if fair < config.LEG_MIN_FAIR_PROB:
                continue
            legs.append(
                Leg(
                    event_id=event.event_id,
                    home=event.home,
                    away=event.away,
                    competition=event.competition_name,
                    start_ts=event.start_ts,
                    market_code=market.code,
                    market_name=market.name,
                    sov=market.sov,
                    outcome_no=selection.outcome_no,
                    outcome_name=selection.name,
                    odd=odd,
                    fair_prob=fair,
                )
            )
    return legs


def _select_pool(
    events: list[NormalizedEvent],
    now_ts: int,
    until_ts: int,
    probability_provider=None,
) -> list[Leg]:
    """All usable legs for events kicking off inside the window."""
    pool: list[Leg] = []
    for ev in events:
        if not (now_ts < ev.start_ts <= until_ts):
            continue
        if not is_event_eligible(ev.competition_name, ev.home, ev.away):
            continue
        pool.extend(candidate_legs_for_event(ev, probability_provider))
    return pool


def _in_window(total: float) -> bool:
    return config.DAILY_MIN_TOTAL_ODDS <= total <= config.DAILY_MAX_TOTAL_ODDS


def _acceptable(prob: float, total: float, min_expected_value: float | None) -> bool:
    return (
        min_expected_value is None
        or prob * total - 1.0 >= min_expected_value
    )


def _best_single(legs, min_expected_value):
    best = None
    for leg in legs:
        if not _in_window(leg.odd):
            continue
        if not _acceptable(leg.fair_prob, leg.odd, min_expected_value):
            continue
        if best is None or leg.fair_prob > best[0]:
            best = (leg.fair_prob, [leg])
    return best


def _best_pair(legs, min_expected_value):
    """Best two-leg build, over the full pool.

    The partners of a leg form a contiguous slice once the pool is sorted by
    odd, so each leg only looks at the legs that can actually complete it.
    """
    ordered = sorted(legs, key=lambda leg: leg.odd)
    odds = [leg.odd for leg in ordered]
    best = None
    for index, first in enumerate(ordered):
        low = config.DAILY_MIN_TOTAL_ODDS / first.odd
        high = config.DAILY_MAX_TOTAL_ODDS / first.odd
        start = max(index + 1, bisect_left(odds, low))
        for second in ordered[start : bisect_right(odds, high)]:
            if second.event_id == first.event_id:
                continue
            total = first.odd * second.odd
            prob = first.fair_prob * second.fair_prob
            if not _acceptable(prob, total, min_expected_value):
                continue
            if best is None or prob > best[0]:
                best = (prob, [first, second])
    return best


def _best_combo(legs, size, min_expected_value):
    """Best build of ``size`` legs, over the safest ``COMBO_CAP`` legs."""
    top = sorted(legs, key=lambda leg: leg.fair_prob, reverse=True)[: config.COMBO_CAP]
    best = None
    for combo in combinations(top, size):
        if len({leg.event_id for leg in combo}) != size:
            continue
        total = probability.total_odds(leg.odd for leg in combo)
        if not _in_window(total):
            continue
        prob = probability.combined_probability(leg.fair_prob for leg in combo)
        if not _acceptable(prob, total, min_expected_value):
            continue
        if best is None or prob > best[0]:
            best = (prob, list(combo))
    return best


def _best_coupon(
    pool: list[Leg],
    exclude_events: set[int],
    min_expected_value: float | None = None,
) -> Coupon | None:
    """Highest joint fair probability that lands inside the odds band.

    Leg counts are tried shortest first and the search stops at the first count
    that can reach the band, because a longer coupon at the same price carries
    another market margin and therefore always wins less often.
    """
    legs = [leg for leg in pool if leg.event_id not in exclude_events]
    for size in range(config.DAILY_MIN_LEGS, config.DAILY_MAX_LEGS + 1):
        if size == 1:
            best = _best_single(legs, min_expected_value)
        elif size == 2:
            best = _best_pair(legs, min_expected_value)
        else:
            best = _best_combo(legs, size, min_expected_value)
        if best is None:
            continue
        chosen = best[1]
        # Present legs in kick-off order.
        chosen.sort(key=lambda leg: leg.start_ts)
        return Coupon(kind="daily", legs=chosen)
    return None


def build_daily_coupons(
    events: list[NormalizedEvent],
    now: datetime | None = None,
    probability_provider=None,
    min_expected_value: float | None = None,
) -> dict[str, Coupon | None]:
    """Return {'main': Coupon|None, 'alt': Coupon|None} for the given bulletin."""
    now = now or datetime.now(tz=config.TIMEZONE)
    now_ts = int(now.timestamp())
    end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=0)
    until_ts = int(end_of_today.timestamp())

    pool = _select_pool(events, now_ts, until_ts, probability_provider)
    # Fallback: if today is thin, widen the window to the next 24h.
    if len({leg.event_id for leg in pool}) < 6:
        until_ts = int((now + timedelta(hours=24)).timestamp())
        pool = _select_pool(events, now_ts, until_ts, probability_provider)

    main = _best_coupon(
        pool, exclude_events=set(), min_expected_value=min_expected_value
    )
    if main:
        main.kind = "daily_main"
    alt = None
    if main:
        alt = _best_coupon(
            pool,
            exclude_events=main.event_ids,
            min_expected_value=min_expected_value,
        )
        if alt:
            alt.kind = "daily_alt"
    return {"main": main, "alt": alt}
