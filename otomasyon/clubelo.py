"""Independent ClubElo fixture probabilities (report-only).

Official API: http://api.clubelo.com/Fixtures (CSV, no authentication).
The service currently has no HTTPS endpoint, but no credentials or private
data are transmitted. Results are never allowed to affect live coupons without
separate historical validation.
"""

from __future__ import annotations

import csv
import io
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

from . import config, probability
from .results.matcher import team_similarity
from .eligibility import is_daily_eligible

CLUBELO_FIXTURES_URL = "http://api.clubelo.com/Fixtures"
CLUBELO_RANKING_ROOT = "http://api.clubelo.com"


@dataclass
class ClubEloFixture:
    date: str
    home: str
    away: str
    p_home: float
    p_draw: float
    p_away: float


@dataclass
class ClubEloRating:
    date: str
    club: str
    country: str
    level: int | None
    elo: float


class ClubEloClient:
    def __init__(self, session=None, timeout: int = config.HTTP_TIMEOUT) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_fixtures(self) -> list[ClubEloFixture]:
        response = self.session.get(CLUBELO_FIXTURES_URL, timeout=self.timeout)
        response.raise_for_status()
        rows = csv.DictReader(io.StringIO(response.text))
        fixtures = []
        for row in rows:
            try:
                p_away = sum(float(row[f"GD={n}"]) for n in range(-5, 0))
                p_away += float(row["GD<-5"])
                p_draw = float(row["GD=0"])
                p_home = sum(float(row[f"GD={n}"]) for n in range(1, 6))
                p_home += float(row["GD>5"])
            except (KeyError, TypeError, ValueError):
                continue
            total = p_home + p_draw + p_away
            if total <= 0:
                continue
            fixtures.append(
                ClubEloFixture(
                    date=row["Date"],
                    home=row["Home"],
                    away=row["Away"],
                    p_home=p_home / total,
                    p_draw=p_draw / total,
                    p_away=p_away / total,
                )
            )
        return fixtures

    def fetch_ratings(self, day: date) -> list[ClubEloRating]:
        response = self.session.get(
            f"{CLUBELO_RANKING_ROOT}/{day.isoformat()}", timeout=self.timeout
        )
        response.raise_for_status()
        rows = csv.DictReader(io.StringIO(response.text))
        ratings = []
        for row in rows:
            try:
                elo = float(row["Elo"])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                level = int(row["Level"]) if row.get("Level") else None
            except ValueError:
                level = None
            ratings.append(
                ClubEloRating(
                    date=day.isoformat(),
                    club=row.get("Club") or "",
                    country=row.get("Country") or "",
                    level=level,
                    elo=elo,
                )
            )
        return ratings


def compare_to_iddaa(events, fixtures: list[ClubEloFixture]) -> dict:
    """Match current events and report ClubElo-vs-iddaa 1X2 differences."""
    reports = []
    for event in events:
        market = event.market(config.MARKET_MATCH_RESULT)
        if market is None or len(market.selections) != 3:
            continue
        event_date = event.start_dt_local.strftime("%Y-%m-%d")
        candidates = [fixture for fixture in fixtures if fixture.date == event_date]
        best = None
        best_score = 0.0
        for fixture in candidates:
            home_score = team_similarity(event.home, fixture.home)
            away_score = team_similarity(event.away, fixture.away)
            score = (home_score + away_score) / 2
            if score > best_score:
                best, best_score = fixture, score
        if best is None or best_score < config.RESULT_FUZZY_THRESHOLD:
            continue
        fair = probability.fair_probs(market.odds)
        external = [best.p_home, best.p_draw, best.p_away]
        differences = [external[i] - fair[i] for i in range(3)]
        index = max(range(3), key=lambda i: differences[i])
        reports.append(
            {
                "event_id": event.event_id,
                "home": event.home,
                "away": event.away,
                "outcome": market.selections[index].name,
                "odd": market.selections[index].odd,
                "iddaa_fair": fair[index],
                "clubelo_prob": external[index],
                "difference": differences[index],
                "match_score": best_score,
            }
        )
    reports.sort(key=lambda item: item["difference"], reverse=True)
    return {
        "events": len(events),
        "clubelo_fixtures": len(fixtures),
        "matched": len(reports),
        "reports": reports,
        "live_enabled": False,
    }


def backtest_ratings(
    history: list[dict],
    ratings: list[dict],
    *,
    start_date: str,
    end_date: str,
    min_edge: float = config.MODEL_MIN_EDGE,
) -> dict:
    """Backtest independent daily ClubElo ratings on a fixed date interval."""
    from .model import GoalModel

    start_ts = int(
        datetime.strptime(start_date, "%Y-%m-%d")
        .replace(tzinfo=config.TIMEZONE)
        .timestamp()
    )
    end_ts = int(
        (
            datetime.strptime(end_date, "%Y-%m-%d")
            .replace(tzinfo=config.TIMEZONE)
            + timedelta(days=1)
        ).timestamp()
    )
    train = [row for row in history if row["start_ts"] < start_ts]
    test = [
        row
        for row in history
        if start_ts <= row["start_ts"] < end_ts
        and is_daily_eligible(row.get("competition") or "")
    ]
    goal_model = GoalModel(train, start_ts)
    rating_index = {
        (row["rating_date"], row["club_key"]): row["elo"] for row in ratings
    }
    bets = []
    covered = 0
    for row in test:
        home_elo = rating_index.get((row["match_date"], row["home_key"]))
        away_elo = rating_index.get((row["match_date"], row["away_key"]))
        odds = [row.get("odds_home"), row.get("odds_draw"), row.get("odds_away")]
        if home_elo is None or away_elo is None or not all(odds):
            continue
        covered += 1
        expected_score = 1.0 / (
            1.0
            + 10
            ** (
                -(
                    home_elo
                    + config.MODEL_ELO_HOME_ADVANTAGE
                    - away_elo
                )
                / 400.0
            )
        )
        draw = goal_model.predict(
            row["home"], row["away"], row.get("competition")
        ).probs["0"]
        p_home = min(1.0 - draw, max(0.0, expected_score - 0.5 * draw))
        external = [p_home, draw, 1.0 - draw - p_home]
        fair = probability.fair_probs(odds)
        choices = [
            (index, odd, external[index] - fair[index])
            for index, odd in enumerate(odds)
            if config.LEG_MIN_ODD <= odd <= config.LEG_MAX_ODD
            and external[index] - fair[index] >= min_edge
        ]
        if not choices:
            continue
        index, odd, edge = max(choices, key=lambda item: item[2])
        actual = (
            0
            if row["ft_home"] > row["ft_away"]
            else 1
            if row["ft_home"] == row["ft_away"]
            else 2
        )
        won = index == actual
        bets.append(
            {"won": won, "odd": odd, "profit": odd - 1 if won else -1, "edge": edge}
        )
    profits = [bet["profit"] for bet in bets]
    roi = statistics.mean(profits) if profits else 0.0
    margin = (
        1.96 * statistics.stdev(profits) / math.sqrt(len(profits))
        if len(profits) > 1
        else 0.0
    )
    return {
        "test_matches": len(test),
        "covered": covered,
        "bets": len(bets),
        "wins": sum(bet["won"] for bet in bets),
        "roi": roi,
        "roi_ci95": (roi - margin, roi + margin),
        "avg_odds": statistics.mean(bet["odd"] for bet in bets) if bets else 0.0,
        "avg_edge": statistics.mean(bet["edge"] for bet in bets) if bets else 0.0,
        "gate_passed": (
            len(bets) >= config.MODEL_GATE_MIN_BETS
            and roi - margin > config.MODEL_GATE_MIN_ROI_CI_LOW
        ),
    }
