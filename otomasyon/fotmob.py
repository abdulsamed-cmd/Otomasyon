"""No-key FotMob context source, isolated from coupon selection.

FotMob's web API is unofficial and unversioned. Missing xG/lineup fields are
normal for lower-coverage competitions and are represented as ``None``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime

import requests

from . import config
from .results.matcher import team_similarity

FOTMOB_BASE = "https://www.fotmob.com/api/data"


@dataclass
class FotMobFixture:
    match_id: int
    league_id: int | None
    league_name: str
    home_id: int | None
    home: str
    away_id: int | None
    away: str
    start_ts: int
    started: bool
    finished: bool
    cancelled: bool


@dataclass
class FotMobMatchContext:
    match_id: int
    captured_ts: int
    started: bool
    finished: bool
    coverage_level: str | None
    xg_home: float | None
    xg_away: float | None
    lineup_available: bool
    home_starters: int
    away_starters: int
    home_players: list["FotMobStarter"] = field(default_factory=list)
    away_players: list["FotMobStarter"] = field(default_factory=list)


@dataclass
class FotMobStarter:
    player_id: int
    name: str
    position_id: int | None
    market_value: float | None


class FotMobClient:
    def __init__(self, session=None, timeout: int = config.HTTP_TIMEOUT) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, params: dict) -> dict:
        error = None
        for attempt in range(config.HTTP_RETRIES):
            try:
                response = self.session.get(
                    f"{FOTMOB_BASE}/{path}",
                    params=params,
                    timeout=self.timeout,
                    headers={"User-Agent": config.USER_AGENT},
                )
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                error = exc
                if attempt + 1 < config.HTTP_RETRIES:
                    time.sleep(config.HTTP_BACKOFF**attempt)
        raise RuntimeError(f"FotMob {path} fetch failed: {error}") from error

    def fetch_date(self, day: date) -> list[FotMobFixture]:
        payload = self._get("matches", {"date": day.strftime("%Y%m%d")})
        fixtures = []
        for league in payload.get("leagues") or []:
            for raw in league.get("matches") or []:
                status = raw.get("status") or {}
                utc_time = status.get("utcTime")
                if not utc_time:
                    continue
                try:
                    start_ts = int(
                        datetime.fromisoformat(
                            utc_time.replace("Z", "+00:00")
                        ).timestamp()
                    )
                except ValueError:
                    continue
                home, away = raw.get("home") or {}, raw.get("away") or {}
                fixtures.append(
                    FotMobFixture(
                        match_id=int(raw["id"]),
                        league_id=raw.get("leagueId") or league.get("id"),
                        league_name=league.get("name") or "",
                        home_id=home.get("id"),
                        home=home.get("longName") or home.get("name") or "",
                        away_id=away.get("id"),
                        away=away.get("longName") or away.get("name") or "",
                        start_ts=start_ts,
                        started=bool(status.get("started")),
                        finished=bool(status.get("finished")),
                        cancelled=bool(status.get("cancelled")),
                    )
                )
        return fixtures

    def fetch_context(
        self, match_id: int, *, captured_ts: int | None = None
    ) -> FotMobMatchContext:
        payload = self._get("matchDetails", {"matchId": match_id})
        general = payload.get("general") or {}
        content = payload.get("content") or {}
        xg_home = xg_away = None
        periods = ((content.get("stats") or {}).get("Periods") or {})
        all_period = periods.get("All") or {}
        for group in all_period.get("stats") or []:
            for item in group.get("stats") or []:
                if item.get("key") != "expected_goals":
                    continue
                values = item.get("stats") or []
                if len(values) == 2 and all(value is not None for value in values):
                    try:
                        xg_home, xg_away = map(float, values)
                    except (TypeError, ValueError):
                        pass
                    break
            if xg_home is not None:
                break
        lineup = content.get("lineup") or {}
        home_players = self._starters(lineup.get("homeTeam") or {})
        away_players = self._starters(lineup.get("awayTeam") or {})
        home_starters = len(home_players)
        away_starters = len(away_players)
        return FotMobMatchContext(
            match_id=int(general.get("matchId") or match_id),
            captured_ts=captured_ts or int(time.time()),
            started=bool(general.get("started")),
            finished=bool(general.get("finished")),
            coverage_level=general.get("coverageLevel"),
            xg_home=xg_home,
            xg_away=xg_away,
            lineup_available=home_starters == 11 and away_starters == 11,
            home_starters=home_starters,
            away_starters=away_starters,
            home_players=home_players,
            away_players=away_players,
        )

    def fetch_team_fixtures(self, team_id: int) -> list[FotMobFixture]:
        payload = self._get("teams", {"id": team_id})
        raw_fixtures = (
            ((payload.get("fixtures") or {}).get("allFixtures") or {}).get(
                "fixtures"
            )
            or []
        )
        fixtures = []
        for raw in raw_fixtures:
            status = raw.get("status") or {}
            utc_time = status.get("utcTime")
            home, away = raw.get("home") or {}, raw.get("away") or {}
            if not utc_time or not home or not away:
                continue
            try:
                start_ts = int(
                    datetime.fromisoformat(
                        utc_time.replace("Z", "+00:00")
                    ).timestamp()
                )
            except ValueError:
                continue
            tournament = raw.get("tournament") or {}
            fixtures.append(
                FotMobFixture(
                    match_id=int(raw["id"]),
                    league_id=tournament.get("leagueId"),
                    league_name=tournament.get("name") or "",
                    home_id=home.get("id"),
                    home=home.get("name") or "",
                    away_id=away.get("id"),
                    away=away.get("name") or "",
                    start_ts=start_ts,
                    started=bool(status.get("started")),
                    finished=bool(status.get("finished")),
                    cancelled=bool(status.get("cancelled")),
                )
            )
        return fixtures

    @staticmethod
    def _starters(team: dict) -> list[FotMobStarter]:
        players = {}
        for raw in team.get("starters") or []:
            try:
                player_id = int(raw["id"])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                market_value = (
                    float(raw["marketValue"])
                    if raw.get("marketValue") is not None
                    else None
                )
            except (TypeError, ValueError):
                market_value = None
            players[player_id] = FotMobStarter(
                player_id=player_id,
                name=raw.get("name") or "",
                position_id=raw.get("usualPlayingPositionId")
                or raw.get("positionId"),
                market_value=market_value,
            )
        return list(players.values())


def match_fixtures(events, fixtures: list[FotMobFixture], *, threshold: float = 0.84):
    """Return unambiguous iddaa-event to FotMob-fixture links and diagnostics."""
    links = {}
    diagnostics = []
    claimed = set()
    for event in events:
        candidates = []
        for fixture in fixtures:
            hours = abs(fixture.start_ts - event.start_ts) / 3600
            if hours > 3:
                continue
            names = (
                team_similarity(event.home, fixture.home)
                + team_similarity(event.away, fixture.away)
            ) / 2
            score = 0.9 * names + 0.1 * max(0.0, 1.0 - hours / 6)
            if score >= threshold:
                candidates.append((score, fixture))
        candidates.sort(key=lambda item: item[0], reverse=True)
        if (
            candidates
            and candidates[0][1].match_id not in claimed
            and (len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.05)
        ):
            score, fixture = candidates[0]
            links[event.event_id] = fixture
            claimed.add(fixture.match_id)
            diagnostics.append(
                {
                    "event_id": event.event_id,
                    "match_id": fixture.match_id,
                    "method": "fuzzy_time",
                    "score": round(score, 3),
                }
            )
        else:
            diagnostics.append(
                {"event_id": event.event_id, "method": "unmatched", "score": 0.0}
            )
    return links, diagnostics
