"""Mackolik daily football-result client.

Mackolik's public livescore page exposes the same JSON feed used by its web
worker. Crucially, betting matches include ``iddaaCode``, which is the exact
iddaa event id used by our bulletin. This makes result matching deterministic;
team-name similarity is only a fallback for records without that field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import time
from typing import Any

import requests

from .. import config

MACKOLIK_URL = (
    "https://www.mackolik.com/perform/p0/ajax/components/"
    "competition/livescores/json"
)


class MackolikError(RuntimeError):
    pass


@dataclass
class SourceMatch:
    source_id: str
    iddaa_code: int | None
    home: str
    away: str
    start_ts: int
    state: str
    substate: str
    ft_home: int | None
    ft_away: int | None
    ht_home: int | None
    ht_away: int | None

    @property
    def is_decided(self) -> bool:
        return self.state == "post" or self.substate in (
            "postponed",
            "cancelled",
            "canceled",
        )


def _score_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class MackolikClient:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: int = config.HTTP_TIMEOUT,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.mackolik.com/canli-sonuclar",
            }
        )

    def fetch_date(self, day: date) -> list[SourceMatch]:
        last_error: Exception | None = None
        payload = None
        for attempt in range(config.HTTP_RETRIES):
            try:
                response = self.session.get(
                    MACKOLIK_URL,
                    params=[("sports[]", "Soccer"), ("matchDate", day.isoformat())],
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
                break
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < config.HTTP_RETRIES - 1:
                    time.sleep(config.HTTP_BACKOFF ** (attempt + 1))
        if payload is None:
            raise MackolikError(f"Mackolik fetch failed for {day}: {last_error}")
        if payload.get("status") != "success":
            raise MackolikError(
                f"Mackolik returned {payload.get('status')!r} for {day}"
            )
        raw_matches = (payload.get("data") or {}).get("matches") or {}
        values = raw_matches.values() if isinstance(raw_matches, dict) else raw_matches
        return [self._parse(raw) for raw in values]

    @staticmethod
    def _parse(raw: dict) -> SourceMatch:
        score = raw.get("score") or {}
        ht = score.get("ht") or {}
        iddaa_code = raw.get("iddaaCode")
        try:
            iddaa_code = int(iddaa_code) if iddaa_code is not None else None
        except (TypeError, ValueError):
            iddaa_code = None
        return SourceMatch(
            source_id=str(raw.get("id") or ""),
            iddaa_code=iddaa_code,
            home=(raw.get("homeTeam") or {}).get("name") or "",
            away=(raw.get("awayTeam") or {}).get("name") or "",
            start_ts=int((raw.get("mstUtc") or 0) / 1000),
            state=raw.get("state") or "",
            substate=raw.get("substate") or "",
            ft_home=_score_int(score.get("home")),
            ft_away=_score_int(score.get("away")),
            ht_home=_score_int(ht.get("home")),
            ht_away=_score_int(ht.get("away")),
        )
