"""Low-risk daily coupon engine.

Given a bulletin of normalized events, build a **main** and an **alternative**
coupon that each:

- use 2-4 legs, one selection per match (no in-match correlation);
- have combined decimal odds within [2.00, 3.00];
- prefer low-risk markets (double chance, over/under, both-teams-to-score, and
  match result only for a strong favourite);
- maximise the combined *fair* (margin-free) win probability.

The alternative coupon reuses no match from the main coupon.

Nothing here places bets - coupons are informational only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import combinations

from . import config, probability
from .eligibility import is_daily_eligible
from .iddaa.normalize import NormalizedEvent, NormalizedMarket

# Markets eligible for the daily low-risk coupon.
_PRIORITY_MARKETS = (
    config.MARKET_DOUBLE_CHANCE,
    config.MARKET_OVER_UNDER,
    config.MARKET_BTTS,
    config.MARKET_MATCH_RESULT,  # favourite-only, enforced below
)


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
    def event_ids(self) -> set[int]:
        return {leg.event_id for leg in self.legs}


def _best_selection(
    market: NormalizedMarket, estimated_probs: list[float] | None = None
) -> tuple[int, float] | None:
    """Index and probability of the most likely outcome of a market."""
    odds = [o for o in market.odds if o and o > 1.0]
    if len(odds) != len(market.selections) or not odds:
        return None
    probs = estimated_probs or probability.fair_probs(odds)
    if len(probs) != len(market.selections):
        return None
    best_idx = max(range(len(probs)), key=lambda i: probs[i])
    return best_idx, probs[best_idx]


def candidate_legs_for_event(event: NormalizedEvent, probability_provider=None) -> list[Leg]:
    """All qualifying low-risk legs an event offers (may be several)."""
    legs: list[Leg] = []
    for market in event.markets:
        if market.status != 1:
            continue
        if market.code not in _PRIORITY_MARKETS:
            continue
        estimated = probability_provider(event, market) if probability_provider else None
        best = _best_selection(market, estimated)
        if best is None:
            continue
        idx, fair = best

        threshold = config.LEG_MIN_FAIR_PROB
        if market.code == config.MARKET_MATCH_RESULT:
            threshold = config.FAVORITE_MIN_FAIR_PROB
        if fair < threshold:
            continue

        sel = market.selections[idx]
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
                outcome_no=sel.outcome_no,
                outcome_name=sel.name,
                odd=sel.odd,
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
    """All qualifying legs (odds within the useful band) for in-window events.

    Keeps every band leg (there may be several per event); the search enforces
    the one-selection-per-match rule via distinct event ids.
    """
    pool: list[Leg] = []
    for ev in events:
        if not (now_ts < ev.start_ts <= until_ts):
            continue
        if not is_daily_eligible(ev.competition_name):
            continue
        for leg in candidate_legs_for_event(ev, probability_provider):
            if config.LEG_MIN_ODD <= leg.odd <= config.LEG_MAX_ODD:
                pool.append(leg)
    return pool


def _in_window(total: float) -> bool:
    return config.DAILY_MIN_TOTAL_ODDS <= total <= config.DAILY_MAX_TOTAL_ODDS


def _best_coupon(
    pool: list[Leg],
    exclude_events: set[int],
    min_expected_value: float | None = None,
) -> Coupon | None:
    """Max combined-probability coupon (2-4 legs, distinct matches, odds in range).

    Pairs are searched exhaustively over the full pool (cheap, and guarantees we
    never miss a strong higher-odd pair). Triples and quads are searched over the
    top ``COMBO_CAP`` safest legs to bound the combinatorics.
    """
    legs = [leg for leg in pool if leg.event_id not in exclude_events]
    best_prob = -1.0
    best_legs: list[Leg] | None = None

    # -- pairs: full O(n^2) scan --
    n = len(legs)
    for i in range(n):
        a = legs[i]
        for j in range(i + 1, n):
            b = legs[j]
            if a.event_id == b.event_id:
                continue
            total = a.odd * b.odd
            if _in_window(total):
                prob = a.fair_prob * b.fair_prob
                if (
                    min_expected_value is not None
                    and prob * total - 1.0 < min_expected_value
                ):
                    continue
                if prob > best_prob:
                    best_prob = prob
                    best_legs = [a, b]

    # -- triples & quads: over the safest COMBO_CAP legs --
    top = sorted(legs, key=lambda leg: leg.fair_prob, reverse=True)[: config.COMBO_CAP]
    for size in (3, config.DAILY_MAX_LEGS):
        if size < 3:
            continue
        for combo in combinations(top, size):
            if len({leg.event_id for leg in combo}) != size:
                continue
            total = probability.total_odds(leg.odd for leg in combo)
            if not _in_window(total):
                continue
            prob = probability.combined_probability(leg.fair_prob for leg in combo)
            if (
                min_expected_value is not None
                and prob * total - 1.0 < min_expected_value
            ):
                continue
            if prob > best_prob:
                best_prob = prob
                best_legs = list(combo)

    if best_legs is None:
        return None
    # Present legs in kick-off order.
    best_legs.sort(key=lambda leg: leg.start_ts)
    return Coupon(kind="daily", legs=best_legs)


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
