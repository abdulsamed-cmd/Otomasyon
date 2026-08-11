"""Turn raw iddaa JSON into typed, human-readable domain objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .. import config
from .markets import MarketResolver


@dataclass
class NormalizedSelection:
    outcome_no: int
    name: str
    odd: float
    web_odd: float | None = None


@dataclass
class NormalizedMarket:
    market_id: int
    t: int
    st: int
    name: str
    sov: str | None
    status: int
    selections: list[NormalizedSelection] = field(default_factory=list)
    # Minimum Bahis Sayısı: how many matches a coupon must hold before this
    # market may be part of it. iddaa refuses the slip below it, so a market
    # priced at ``mbs=2`` simply cannot be played on its own.
    mbs: int = 1

    @property
    def code(self) -> tuple[int, int]:
        return (self.t, self.st)

    @property
    def odds(self) -> list[float]:
        return [s.odd for s in self.selections]


@dataclass
class NormalizedEvent:
    event_id: int
    home: str
    away: str
    competition_id: int
    competition_name: str
    country_code: str | None
    sport_id: int
    start_ts: int  # unix seconds (UTC)
    status: int
    markets: list[NormalizedMarket] = field(default_factory=list)

    @property
    def start_dt_local(self) -> datetime:
        return datetime.fromtimestamp(self.start_ts, tz=config.TIMEZONE)

    def market(self, code: tuple[int, int]) -> NormalizedMarket | None:
        for m in self.markets:
            if m.code == code:
                return m
        return None


def build_competitions_map(raw_competitions: list[dict]) -> dict[int, dict]:
    """Map competition id -> {name, country_code}."""
    out: dict[int, dict] = {}
    for c in raw_competitions or []:
        cid = c.get("i")
        if cid is None:
            continue
        out[cid] = {"name": c.get("n") or "", "country_code": c.get("cid")}
    return out


def normalize_event(
    raw: dict, resolver: MarketResolver, competitions: dict[int, dict]
) -> NormalizedEvent:
    ci = raw.get("ci")
    comp = competitions.get(ci, {}) if ci is not None else {}
    markets: list[NormalizedMarket] = []
    for m in raw.get("m", []) or []:
        t, st = m.get("t"), m.get("st")
        sov = m.get("sov")
        selections = [
            NormalizedSelection(
                outcome_no=o.get("no"),
                name=o.get("n") or "",
                odd=o.get("odd"),
                web_odd=o.get("wodd"),
            )
            for o in m.get("o", []) or []
        ]
        markets.append(
            NormalizedMarket(
                market_id=m.get("i"),
                t=t,
                st=st,
                name=resolver.name(t, st, sov),
                sov=sov,
                status=m.get("s", 0),
                selections=selections,
                # A market carries its own limit and it is not always the
                # event's, so the market's own value is the one that binds.
                mbs=int(m.get("mbc") or raw.get("mbc") or 1),
            )
        )
    return NormalizedEvent(
        event_id=raw.get("i"),
        home=raw.get("hn") or "",
        away=raw.get("an") or "",
        competition_id=ci if ci is not None else -1,
        competition_name=comp.get("name", ""),
        country_code=comp.get("country_code"),
        sport_id=raw.get("sid", config.SPORT_FOOTBALL),
        start_ts=raw.get("d", 0),
        status=raw.get("s", 0),
        markets=markets,
    )


def normalize_events(
    raw_events: list[dict],
    resolver: MarketResolver,
    competitions: dict[int, dict],
) -> list[NormalizedEvent]:
    return [normalize_event(e, resolver, competitions) for e in raw_events or []]
