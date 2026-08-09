"""Thin client over the public iddaa sportsbook JSON API.

All endpoints return an envelope: ``{"isSuccess": bool, "data": ..., "message"}``.
This client unwraps ``data`` and raises :class:`IddaaError` on failure, with a
small retry/backoff loop for transient network errors.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from .. import config


class IddaaError(RuntimeError):
    """Raised when an iddaa API call fails or returns an error envelope."""


class IddaaClient:
    def __init__(
        self,
        base_url: str = config.SPORTSBOOK_BASE,
        *,
        session: requests.Session | None = None,
        timeout: int = config.HTTP_TIMEOUT,
        retries: int = config.HTTP_RETRIES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.session = session or requests.Session()
        self.session.headers.update(
            {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
        )

    # -- low level ----------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                payload = resp.json()
                if not payload.get("isSuccess", False):
                    raise IddaaError(
                        f"iddaa error for {url}: {payload.get('message')!r}"
                    )
                return payload.get("data")
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt < self.retries - 1:
                    time.sleep(config.HTTP_BACKOFF ** (attempt + 1))
        raise IddaaError(f"failed to GET {url}: {last_exc}") from last_exc

    # -- endpoints ----------------------------------------------------------
    def get_market_config(self) -> dict[str, Any]:
        """Market dictionary keyed by ``f"{t}_{st}"`` under ``data['m']``."""
        return self._get("get_market_config")

    def get_competitions(self, sport_id: int = config.SPORT_FOOTBALL) -> list[dict]:
        """Leagues/competitions for a sport (maps event ``ci`` -> league)."""
        return self._get("competitions", params={"st": sport_id})

    def get_events(
        self,
        sport_id: int = config.SPORT_FOOTBALL,
        *,
        event_type: int = 0,  # 0 = pre-match bulletin, 1 = live
        version: int = 0,
    ) -> list[dict]:
        """Bulletin events (each with nested markets/odds) under ``data['events']``."""
        data = self._get(
            "events",
            params={"st": sport_id, "type": event_type, "version": version},
        )
        return data.get("events", []) if isinstance(data, dict) else []

    def get_event(self, event_id: int) -> dict:
        """Single event with all markets/odds."""
        return self._get(f"event/{event_id}")
