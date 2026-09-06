"""Daily coupon engine.

Two of the day's coupons answer one question: **what is the likeliest thing we
can build that pays at least X, and can actually be played?** The main coupon
exists to land often and asks for a modest payout; the alternative asks for
2.00 or more. The mixed coupon asks the same question with the two sides
swapped - the payout is free and the chance of landing is what is fixed - so
it reaches the part of the board the other two cannot: past 2.53 no single
selection is allowed, so a coupon there has to span matches.

Within that, nothing is prescribed. Leg count is free from 1 to
``DAILY_MAX_LEGS``, there is no upper bound on the payout, and every size is
searched and compared rather than stopping at the first that fits. A coupon
still takes at most one selection per match, so no two legs move together, and
draws only from markets we can grade.

The binding rule is iddaa's own: every market carries a Minimum Bahis Sayısı,
and a coupon shorter than a leg's MBS is refused at the counter. So each leg
count is searched over only the legs that count admits - a market at ``mbs=2``
is invisible to the single-leg search, however likely it is.

Win probability comes from ``probability_provider`` when one is supplied - that
is where a model's opinion enters. With no provider the engine falls back to
the market's own margin-free prices, in which case it is a price-taker and the
search is only shopping for the best price.

No match is used twice in a day. The alternative reuses nothing from the main
coupon and the mixed coupon reuses nothing from either, so the three land or
fail on their own and the record can say which of them works.

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
    # What the market implies once its margin is removed.
    fair_prob: float
    # How many matches a coupon must hold before this leg may join it. The
    # archive never recorded it, so replayed history defaults to 1 and cannot
    # answer what iddaa would have accepted on the day.
    mbs: int = 1
    # What we believe. Ranking uses this; ``fair_prob`` stays as the market's
    # own number so the two can always be compared. Left unset it *is* the
    # market's number, because that is our belief until a model earns the right
    # to move it.
    win_prob: float | None = None

    def __post_init__(self) -> None:
        if self.win_prob is None:
            self.win_prob = self.fair_prob

    @property
    def edge(self) -> float:
        return self.win_prob - self.fair_prob

    @property
    def label(self) -> str:
        return f"{self.home} - {self.away} | {self.market_name}: {self.outcome_name} @ {self.odd}"


@dataclass
class Coupon:
    kind: str  # daily_main | daily_alt | daily_mix
    legs: list[Leg] = field(default_factory=list)

    @property
    def total_odds(self) -> float:
        return probability.total_odds(leg.odd for leg in self.legs)

    @property
    def combined_prob(self) -> float:
        """Our own probability that the coupon lands."""
        return probability.combined_probability(leg.win_prob for leg in self.legs)

    @property
    def market_prob(self) -> float:
        """The same, priced by the market rather than by us."""
        return probability.combined_probability(leg.fair_prob for leg in self.legs)

    @property
    def edge(self) -> float:
        return self.combined_prob - self.market_prob

    @property
    def cumulative_margin(self) -> float:
        """Margin the whole coupon carries, compounded over its legs.

        This is the gap between what the coupon pays and what a fair price
        would pay, and it is the single number the leg count moves.
        """
        priced = self.market_prob * self.total_odds
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
        fair_probs = probability.fair_probs(odds, coverage)
        beliefs = (
            probability_provider(event, market)
            if probability_provider
            else None
        ) or fair_probs
        if len(beliefs) != len(market.selections):
            beliefs = fair_probs
        for selection, odd, fair, belief in zip(
            market.selections, odds, fair_probs, beliefs
        ):
            if not (config.LEG_MIN_ODD <= odd <= config.LEG_MAX_ODD):
                continue
            if belief < config.LEG_MIN_FAIR_PROB:
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
                    mbs=market.mbs,
                    fair_prob=fair,
                    win_prob=belief,
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


def _acceptable(prob: float, total: float, min_expected_value: float | None) -> bool:
    return (
        min_expected_value is None
        or prob * total - 1.0 >= min_expected_value
    )


def _best_single(legs, min_odds, min_expected_value):
    best = None
    for leg in legs:
        if leg.odd < min_odds:
            continue
        if not _acceptable(leg.fair_prob, leg.odd, min_expected_value):
            continue
        if best is None or leg.win_prob > best[0]:
            best = (leg.win_prob, [leg])
    return best


def _suffix_best(ordered: list[Leg], width: int) -> list[list[Leg]]:
    """For every start index, the likeliest few legs at or after it.

    Sorted by odd, the legs that can complete a partner form a suffix, so the
    best partner is a lookup rather than a scan. The list is kept wide enough
    to survive discarding every leg of a single match.
    """
    suffix: list[list[Leg]] = [[] for _ in range(len(ordered) + 1)]
    for index in range(len(ordered) - 1, -1, -1):
        merged = suffix[index + 1] + [ordered[index]]
        merged.sort(key=lambda leg: leg.win_prob, reverse=True)
        suffix[index] = merged[:width]
    return suffix


def _best_pair(legs, min_odds, min_expected_value):
    """Best two-leg build, over the full pool."""
    ordered = sorted(legs, key=lambda leg: leg.odd)
    odds = [leg.odd for leg in ordered]
    suffix = _suffix_best(ordered, config.PAIR_SUFFIX_WIDTH)
    best = None
    for first in ordered:
        start = bisect_left(odds, min_odds / first.odd)
        for second in suffix[start]:
            if second.event_id == first.event_id:
                continue
            total = first.odd * second.odd
            prob = first.win_prob * second.win_prob
            if not _acceptable(
                first.fair_prob * second.fair_prob, total, min_expected_value
            ):
                continue
            if best is None or prob > best[0]:
                best = (prob, [first, second])
    return best


def _best_combo(legs, size, min_odds, min_expected_value):
    """Best build of ``size`` legs, over the likeliest legs available.

    Enumerating every combination of the whole pool is not affordable past two
    legs, so the search runs over the safest legs only. That is where the
    answer lives: the objective is joint win probability, and a leg outside the
    top slice cannot be in the likeliest build of its size.
    """
    cap = config.COMBO_CAPS.get(size, config.COMBO_CAPS[max(config.COMBO_CAPS)])
    top = sorted(legs, key=lambda leg: leg.win_prob, reverse=True)[:cap]
    best = None
    for combo in combinations(top, size):
        if len({leg.event_id for leg in combo}) != size:
            continue
        total = probability.total_odds(leg.odd for leg in combo)
        if total < min_odds:
            continue
        prob = probability.combined_probability(leg.win_prob for leg in combo)
        if not _acceptable(
            probability.combined_probability(leg.fair_prob for leg in combo),
            total,
            min_expected_value,
        ):
            continue
        if best is None or prob > best[0]:
            best = (prob, list(combo))
    return best


def _best_coupon(
    pool: list[Leg],
    exclude_events: set[int],
    min_odds: float,
    min_expected_value: float | None = None,
) -> Coupon | None:
    """Likeliest coupon that pays at least ``min_odds``.

    Every leg count is searched and the best of them wins. Leg count is not
    prescribed in either direction: extra legs each multiply another margin
    into the coupon, which usually makes them lose, but that is left to the
    measurement rather than assumed.
    """
    available = [leg for leg in pool if leg.event_id not in exclude_events]
    best = None
    for size in range(config.DAILY_MIN_LEGS, config.DAILY_MAX_LEGS + 1):
        # A leg iddaa will not accept in a coupon this short is not a leg here.
        legs = [leg for leg in available if leg.mbs <= size]
        if len(legs) < size:
            continue
        if size == 1:
            candidate = _best_single(legs, min_odds, min_expected_value)
        elif size == 2:
            candidate = _best_pair(legs, min_odds, min_expected_value)
        else:
            candidate = _best_combo(legs, size, min_odds, min_expected_value)
        if candidate is not None and (best is None or candidate[0] > best[0]):
            best = candidate
    if best is None:
        return None
    chosen = best[1]
    # Present legs in kick-off order.
    chosen.sort(key=lambda leg: leg.start_ts)
    return Coupon(kind="daily", legs=chosen)


# How close the payout search has to get before it stops splitting the gap, and
# how many splits it is allowed. The gap halves each time, so the cap is only
# there to bound the work if a bulletin ever makes the search behave oddly.
_PAYOUT_PRECISION = 0.01
_PAYOUT_BISECTIONS = 24


def _richest_coupon(
    pool: list[Leg],
    exclude_events: set[int],
    min_prob: float,
    min_expected_value: float | None = None,
) -> Coupon | None:
    """Best-paying coupon that still lands at least ``min_prob`` of the time.

    The other two coupons pin the payout and ask for the likeliest build that
    reaches it. This one pins the chance of landing and asks for the biggest
    payout that survives it - the same frontier, approached from the other
    end, which is why nothing about the shape of the coupon is prescribed
    here either.

    Leg count in particular is not chosen. ``_best_coupon`` returns the
    likeliest build at whatever payout it is given, and the likeliest build at
    a payout is the one carrying the fewest margins, so how many matches the
    coupon spans falls out of the arithmetic rather than out of a rule.

    Raising the payout floor can only shrink what the search may pick from, so
    the best probability it can reach never rises with it. That makes the
    answer bracketable: the floor is doubled until it is out of reach, then
    the gap is halved until it closes.
    """

    def reaching(floor: float) -> Coupon | None:
        coupon = _best_coupon(pool, exclude_events, floor, min_expected_value)
        if coupon is None or coupon.combined_prob < min_prob:
            return None
        return coupon

    best = reaching(1.0)
    if best is None:
        return None
    # Every payout up to ``low`` is known to be reachable; ``high`` is the
    # first one found not to be.
    low = best.total_odds
    ceiling = config.LEG_MAX_ODD**config.DAILY_MAX_LEGS
    high = None
    probe = low * 2
    while probe <= ceiling:
        found = reaching(probe)
        if found is None:
            high = probe
            break
        best = found
        low = max(probe, found.total_odds)
        probe = low * 2
    if high is None:
        return best
    for _ in range(_PAYOUT_BISECTIONS):
        if high - low < _PAYOUT_PRECISION:
            break
        middle = (low + high) / 2
        found = reaching(middle)
        if found is None:
            high = middle
        else:
            best = found
            low = max(middle, found.total_odds)
    return best


def build_daily_coupons(
    events: list[NormalizedEvent],
    now: datetime | None = None,
    probability_provider=None,
    min_expected_value: float | None = None,
    main_min_odds: float | None = None,
    alt_min_odds: float | None = None,
    mix_min_prob: float | None = None,
    with_mix: bool = True,
) -> dict[str, Coupon | None]:
    """Return {'main': ..., 'alt': ..., 'mix': ...} for the given bulletin."""
    main_floor = (
        config.DAILY_MAIN_MIN_ODDS if main_min_odds is None else main_min_odds
    )
    alt_floor = config.DAILY_ALT_MIN_ODDS if alt_min_odds is None else alt_min_odds
    mix_floor = (
        config.DAILY_MIX_MIN_PROB if mix_min_prob is None else mix_min_prob
    )
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
        pool,
        exclude_events=set(),
        min_odds=main_floor,
        min_expected_value=min_expected_value,
    )
    if main:
        main.kind = "daily_main"
    alt = _best_coupon(
        pool,
        exclude_events=main.event_ids if main else set(),
        min_odds=alt_floor,
        min_expected_value=min_expected_value,
    )
    if alt:
        alt.kind = "daily_alt"
    spoken_for = (main.event_ids if main else set()) | (
        alt.event_ids if alt else set()
    )
    mix = (
        _richest_coupon(
            pool,
            exclude_events=spoken_for,
            min_prob=mix_floor,
            min_expected_value=min_expected_value,
        )
        if with_mix
        else None
    )
    if mix:
        mix.kind = "daily_mix"
    return {"main": main, "alt": alt, "mix": mix}
