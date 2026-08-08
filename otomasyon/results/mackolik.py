"""Mackolik daily football-result client.

Mackolik's public livescore page exposes the same JSON feed used by its web
worker. Crucially, betting matches include ``iddaaCode``, which is the exact
iddaa event id used by our bulletin. This makes result matching deterministic;
team-name similarity is only a fallback for records without that field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import time
from typing import Any

import requests

from .. import config

MACKOLIK_URL = (
    "https://www.mackolik.com/perform/p0/ajax/components/"
    "competition/livescores/json"
)
MACKOLIK_ARCHIVE_URL = "https://vd.mackolik.com/livedata"


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
    competition_id: str | None = None
    competition_name: str | None = None
    odds_home: float | None = None
    odds_draw: float | None = None
    odds_away: float | None = None
    odds_under25: float | None = None
    odds_over25: float | None = None

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


def _float_or_none(value: Any) -> float | None:
    if value in (None, "", "0", "0.0"):
        return None
    try:
        parsed = float(value)
        return parsed if parsed > 1.0 else None
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
        """Fetch one day, with archive fallback.

        The modern feed is preferred for named fields. For extra-time/penalty
        games we also consult the archive because its indices 29/30 carry the
        90-minute score required by standard football-market settlement.
        """
        try:
            matches = self._fetch_modern(day)
        except MackolikError:
            return self._fetch_archive(day)

        if any(
            match.substate in ("afterExtraTime", "penalties") for match in matches
        ):
            archive = self._fetch_archive(day)
            regulation = {
                match.iddaa_code: match
                for match in archive
                if match.iddaa_code is not None
            }
            matches = [
                regulation.get(match.iddaa_code, match)
                if match.substate in ("afterExtraTime", "penalties")
                else match
                for match in matches
            ]
        return matches

    def fetch_archive_date(self, day: date) -> list[SourceMatch]:
        """Public archive reader for historical model backfills."""
        return self._fetch_archive(day)

    def _fetch_modern(self, day: date) -> list[SourceMatch]:
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
        data = payload.get("data") or {}
        raw_matches = data.get("matches") or {}
        competitions = data.get("competitions") or {}
        values = raw_matches.values() if isinstance(raw_matches, dict) else raw_matches
        return [self._parse(raw, competitions) for raw in values]

    def _fetch_archive(self, day: date) -> list[SourceMatch]:
        last_error: Exception | None = None
        payload = None
        for attempt in range(config.HTTP_RETRIES):
            try:
                response = self.session.get(
                    MACKOLIK_ARCHIVE_URL,
                    params={"date": day.strftime("%d/%m/%Y")},
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
            raise MackolikError(
                f"Mackolik archive fetch failed for {day}: {last_error}"
            )
        rows = payload.get("m") or []
        # row[23] == 1 is football.
        return [self._parse_archive(row) for row in rows if len(row) > 36 and row[23] == 1]

    @staticmethod
    def _parse(raw: dict, competitions: dict | None = None) -> SourceMatch:
        score = raw.get("score") or {}
        ht = score.get("ht") or {}
        iddaa_code = raw.get("iddaaCode")
        try:
            iddaa_code = int(iddaa_code) if iddaa_code is not None else None
        except (TypeError, ValueError):
            iddaa_code = None
        competition_id = raw.get("competitionId")
        competition = (competitions or {}).get(competition_id, {})
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
            competition_id=str(competition_id) if competition_id else None,
            competition_name=competition.get("name"),
        )

    @staticmethod
    def _parse_archive(row: list) -> SourceMatch:
        status = str(row[6] or "")
        status_key = status.casefold()
        if status in ("MS", "UZ", "Pen"):
            state, substate = "post", {
                "MS": "fullTime",
                "UZ": "afterExtraTime",
                "Pen": "penalties",
            }[status]
        elif status_key.startswith("ert"):
            state, substate = "pre", "postponed"
        elif status_key.startswith(("ipt", "cancel")):
            state, substate = "pre", "cancelled"
        else:
            state, substate = "pre", ""

        iddaa_code = _score_int(row[14])
        if iddaa_code == 0:
            iddaa_code = None
        # The legacy feed explicitly separates 90-minute score (29/30) from
        # displayed final after extra time (12/13). Betting settlement uses 90'.
        ft_home = _score_int(row[29])
        ft_away = _score_int(row[30])
        if ft_home is None or ft_away is None:
            ft_home, ft_away = _score_int(row[12]), _score_int(row[13])
        try:
            local_dt = datetime.strptime(
                f"{row[35]} {row[16]}", "%d/%m/%Y %H:%M"
            ).replace(tzinfo=config.TIMEZONE)
            start_ts = int(local_dt.timestamp())
        except (TypeError, ValueError):
            start_ts = 0
        return SourceMatch(
            source_id=f"legacy:{row[0]}",
            iddaa_code=iddaa_code,
            home=str(row[2] or ""),
            away=str(row[4] or ""),
            start_ts=start_ts,
            state=state,
            substate=substate,
            ft_home=ft_home,
            ft_away=ft_away,
            ht_home=_score_int(row[31]),
            ht_away=_score_int(row[32]),
            competition_id=str(row[36][2]) if row[36] and len(row[36]) > 2 else None,
            competition_name=str(row[36][3]) if row[36] and len(row[36]) > 3 else None,
            odds_home=_float_or_none(row[18]),
            odds_draw=_float_or_none(row[19]),
            odds_away=_float_or_none(row[20]),
            odds_under25=_float_or_none(row[21]),
            odds_over25=_float_or_none(row[22]),
        )
