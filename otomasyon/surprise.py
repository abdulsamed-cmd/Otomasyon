"""Weekly "surprise lab" - high-risk longshot candidates + system scenarios.

Completely separate from the safe daily coupon. We shortlist candidate matches
for three longshot categories:

- ``htft_12``   : HT/FT 1/2 (home led at half, away won - away comeback)
- ``htft_21``   : HT/FT 2/1 (away led at half, home won - home comeback)
- ``goals_6plus``: 6+ total goals

Ranking uses the market's fair (margin-free) probability of the target outcome:
a higher implied chance means the market itself rates the longshot as more
plausible. No bets are placed - we only show candidates, system combinations,
and their theoretical minimum cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import combinations
from math import comb

from . import config, probability
from .eligibility import is_event_eligible
from .iddaa.normalize import NormalizedEvent


@dataclass
class SurpriseCandidate:
    category: str
    event_id: int
    home: str
    away: str
    competition: str
    start_ts: int
    outcome_name: str
    odd: float
    fair_prob: float

    @property
    def match(self) -> str:
        return f"{self.home} - {self.away}"


@dataclass
class SystemScenario:
    size: int  # r in "r / k"
    total: int  # k candidates
    columns: int  # number of r-combinations
    unit_stake: float

    @property
    def min_cost(self) -> float:
        return self.columns * self.unit_stake


@dataclass
class SurpriseReport:
    by_category: dict[str, list[SurpriseCandidate]] = field(default_factory=dict)
    system_set: list[SurpriseCandidate] = field(default_factory=list)
    scenarios: list[SystemScenario] = field(default_factory=list)

    @property
    def has_candidates(self) -> bool:
        return any(self.by_category.values())


def _fair_for_outcome(market, outcome_name: str) -> tuple[float, float] | None:
    """(odd, fair_prob) for a named outcome within its market, else None."""
    odds = [o for o in market.odds if o and o > 1.0]
    if len(odds) != len(market.selections) or not odds:
        return None
    fair = probability.fair_probs(odds)
    for sel, fp in zip(market.selections, fair):
        if sel.name == outcome_name:
            return sel.odd, fp
    return None


def find_candidates(
    events: list[NormalizedEvent],
    category: str,
    *,
    now_ts: int,
    until_ts: int,
) -> list[SurpriseCandidate]:
    market_code, outcome_name = config.SURPRISE_CATEGORIES[category]
    out: list[SurpriseCandidate] = []
    for ev in events:
        if not (now_ts < ev.start_ts <= until_ts):
            continue
        if not is_event_eligible(ev.competition_name, ev.home, ev.away):
            continue
        market = ev.market(market_code)
        if market is None or market.status != 1:
            continue
        res = _fair_for_outcome(market, outcome_name)
        if res is None:
            continue
        odd, fair = res
        out.append(
            SurpriseCandidate(
                category=category,
                event_id=ev.event_id,
                home=ev.home,
                away=ev.away,
                competition=ev.competition_name,
                start_ts=ev.start_ts,
                outcome_name=outcome_name,
                odd=odd,
                fair_prob=fair,
            )
        )
    # Most plausible longshots first.
    out.sort(key=lambda c: c.fair_prob, reverse=True)
    return out


def build_surprise(
    events: list[NormalizedEvent],
    now: datetime | None = None,
) -> SurpriseReport:
    now = now or datetime.now(tz=config.TIMEZONE)
    now_ts = int(now.timestamp())
    until_ts = int((now + timedelta(hours=config.SURPRISE_WINDOW_HOURS)).timestamp())

    by_cat: dict[str, list[SurpriseCandidate]] = {}
    for category in config.SURPRISE_CATEGORIES:
        found = find_candidates(events, category, now_ts=now_ts, until_ts=until_ts)
        by_cat[category] = found[: config.SURPRISE_PER_CATEGORY]

    # System set: pick the best distinct-match candidates across all categories.
    pool: list[SurpriseCandidate] = [c for cs in by_cat.values() for c in cs]
    pool.sort(key=lambda c: c.fair_prob, reverse=True)
    system_set: list[SurpriseCandidate] = []
    used: set[int] = set()
    for c in pool:
        if c.event_id in used:
            continue
        used.add(c.event_id)
        system_set.append(c)
        if len(system_set) >= config.SURPRISE_SYSTEM_SIZE:
            break

    scenarios = system_scenarios(len(system_set))
    return SurpriseReport(by_category=by_cat, system_set=system_set, scenarios=scenarios)


def system_scenarios(k: int, unit_stake: float | None = None) -> list[SystemScenario]:
    """Columns and theoretical min cost for each system size r (2..k)."""
    unit_stake = config.SURPRISE_UNIT_STAKE if unit_stake is None else unit_stake
    if k < 2:
        return []
    return [
        SystemScenario(size=r, total=k, columns=comb(k, r), unit_stake=unit_stake)
        for r in range(2, k + 1)
    ]


def enumerate_columns(candidates: list[SurpriseCandidate], r: int) -> list[tuple]:
    """All r-sized combinations of candidates (by match), for display/analysis."""
    return list(combinations(candidates, r))
