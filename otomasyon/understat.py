"""Bulk historical xG ingestion from Understat's unversioned web API."""

from __future__ import annotations

import time
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from . import config
from .results.matcher import team_similarity

UNDERSTAT_BASE = "https://understat.com"
UNDERSTAT_LEAGUES = ("EPL", "La_liga", "Bundesliga", "Serie_A", "Ligue_1", "RFPL")


@dataclass
class UnderstatMatch:
    source_id: str
    league: str
    season: int
    start_ts: int
    home: str
    away: str
    ft_home: int
    ft_away: int
    xg_home: float
    xg_away: float


class UnderstatClient:
    def __init__(self, session=None, timeout: int = config.HTTP_TIMEOUT) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_league(self, league: str, season: int) -> list[UnderstatMatch]:
        if league not in UNDERSTAT_LEAGUES:
            raise ValueError(f"unsupported Understat league: {league}")
        page_url = f"{UNDERSTAT_BASE}/league/{league}/{season}"
        headers = {"User-Agent": config.USER_AGENT}
        self._request(page_url, headers=headers)
        payload = self._request(
            f"{UNDERSTAT_BASE}/getLeagueData/{league}/{season}",
            headers={
                **headers,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": page_url,
            },
        ).json()
        matches = []
        for raw in payload.get("dates") or []:
            if not raw.get("isResult"):
                continue
            try:
                start_ts = int(
                    datetime.strptime(
                        raw["datetime"], "%Y-%m-%d %H:%M:%S"
                    )
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                )
                matches.append(
                    UnderstatMatch(
                        source_id=f"understat:{raw['id']}",
                        league=league,
                        season=season,
                        start_ts=start_ts,
                        home=raw["h"]["title"],
                        away=raw["a"]["title"],
                        ft_home=int(raw["goals"]["h"]),
                        ft_away=int(raw["goals"]["a"]),
                        xg_home=float(raw["xG"]["h"]),
                        xg_away=float(raw["xG"]["a"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return matches

    def _request(self, url: str, *, headers: dict):
        error = None
        for attempt in range(config.HTTP_RETRIES):
            try:
                response = self.session.get(
                    url, headers=headers, timeout=self.timeout
                )
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                error = exc
                if attempt + 1 < config.HTTP_RETRIES:
                    time.sleep(config.HTTP_BACKOFF**attempt)
        raise RuntimeError(f"Understat fetch failed: {error}") from error


def match_understat_history(
    xg_matches: list[dict],
    history: list[dict],
    *,
    threshold: float = 0.88,
) -> tuple[list[dict], list[dict]]:
    """Create unambiguous xG-to-history links using time and both team names."""
    ordered = sorted(history, key=lambda row: row["start_ts"])
    timestamps = [row["start_ts"] for row in ordered]
    claimed = set()
    links = []
    diagnostics = []
    for source in xg_matches:
        left = bisect_left(timestamps, source["start_ts"] - 3 * 3600)
        right = bisect_right(timestamps, source["start_ts"] + 3 * 3600)
        candidates = []
        for target in ordered[left:right]:
            if target["source_id"] in claimed:
                continue
            home = team_similarity(source["home"], target["home"])
            away = team_similarity(source["away"], target["away"])
            score = (home + away) / 2
            if (
                score >= threshold
                and source["ft_home"] == target["ft_home"]
                and source["ft_away"] == target["ft_away"]
            ):
                candidates.append((score, target))
        candidates.sort(key=lambda item: item[0], reverse=True)
        if candidates and (
            len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.05
        ):
            score, target = candidates[0]
            claimed.add(target["source_id"])
            links.append(
                {
                    "understat_source_id": source["source_id"],
                    "historical_source_id": target["source_id"],
                    "match_score": score,
                }
            )
            diagnostics.append(
                {
                    "source_id": source["source_id"],
                    "method": "team_time_score",
                    "score": round(score, 3),
                }
            )
        else:
            diagnostics.append(
                {
                    "source_id": source["source_id"],
                    "method": "unmatched",
                    "score": 0.0,
                }
            )
    return links, diagnostics
