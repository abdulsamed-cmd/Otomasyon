"""Match-day weather, from the stadium a team actually plays in.

Weather is a property of a place and a day, so this works in two steps: resolve
a team's venue coordinates once from FotMob, then ask Open-Meteo what the sky
did (or will do) there. Open-Meteo needs no key and serves both the historical
archive and the forecast, which is what lets the same feature be measured on
past matches and stated about tomorrow's.

Nothing here decides anything. It collects a signal so the calibration layer
can weigh it; whether the weather is worth knowing is a measurement, not an
assumption.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from . import config

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY_FIELDS = "precipitation_sum,wind_speed_10m_max"


@dataclass(frozen=True)
class Venue:
    team_key: str
    latitude: float
    longitude: float
    stadium: str | None = None
    city: str | None = None
    country: str | None = None
    source_match: int | None = None


@dataclass(frozen=True)
class DayWeather:
    team_key: str
    weather_date: str
    precipitation: float | None
    wind_speed: float | None


def venue_from_match_details(team_key: str, payload: dict, match_id: int) -> Venue | None:
    """Pull stadium coordinates out of a FotMob match details payload."""
    info = ((payload.get("content") or {}).get("matchFacts") or {}).get("infoBox") or {}
    stadium = info.get("Stadium") or {}
    latitude, longitude = stadium.get("lat"), stadium.get("long")
    if latitude is None or longitude is None:
        return None
    return Venue(
        team_key=team_key,
        latitude=float(latitude),
        longitude=float(longitude),
        stadium=stadium.get("name"),
        city=stadium.get("city"),
        country=stadium.get("country"),
        source_match=match_id,
    )


class OpenMeteoClient:
    """Daily weather for a venue, historical or forecast.

    The archive endpoint charges by how much data a request returns and answers
    a 429 when pushed, so the client backs off rather than dropping the venue.
    """

    def __init__(self, session=None, timeout: int = 90) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def _daily(self, url: str, params: dict) -> dict:
        error: Exception | None = None
        for attempt in range(config.WEATHER_RETRIES):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 429:
                    raise RuntimeError("Open-Meteo rate limited")
                response.raise_for_status()
                return response.json().get("daily") or {}
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                error = exc
                if attempt + 1 < config.WEATHER_RETRIES:
                    time.sleep(min(90.0, config.WEATHER_BACKOFF * (2**attempt)))
        raise RuntimeError(f"Open-Meteo fetch failed: {error}") from error

    @staticmethod
    def _rows(venue: Venue, daily: dict) -> list[DayWeather]:
        return [
            DayWeather(venue.team_key, day, rain, wind)
            for day, rain, wind in zip(
                daily.get("time") or [],
                daily.get("precipitation_sum") or [],
                daily.get("wind_speed_10m_max") or [],
            )
        ]

    def archive(self, venue: Venue, start_date: str, end_date: str) -> list[DayWeather]:
        return self._rows(
            venue,
            self._daily(
                ARCHIVE_URL,
                {
                    "latitude": venue.latitude,
                    "longitude": venue.longitude,
                    "start_date": start_date,
                    "end_date": end_date,
                    "daily": DAILY_FIELDS,
                    "timezone": "GMT",
                },
            ),
        )

    def forecast(self, venue: Venue, *, days: int = 3) -> list[DayWeather]:
        return self._rows(
            venue,
            self._daily(
                FORECAST_URL,
                {
                    "latitude": venue.latitude,
                    "longitude": venue.longitude,
                    "daily": DAILY_FIELDS,
                    "forecast_days": days,
                    "timezone": "GMT",
                },
            ),
        )
