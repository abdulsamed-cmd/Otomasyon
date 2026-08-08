"""High-level services shared by the CLI and the Telegram bot.

Fetches the bulletin (with a short cache so repeated bot commands don't hammer
the API), builds coupons / surprise reports, persists coupons, and returns
ready-to-send text.
"""

from __future__ import annotations

import time
from datetime import datetime

from . import config, engine, formatting, surprise
from .iddaa import IddaaClient, MarketResolver, normalize_events
from .iddaa.normalize import build_competitions_map
from .storage import Database

_CACHE_TTL = 300  # seconds
_cache: dict = {"ts": 0.0, "events": None, "competitions": None}


def fetch_normalized_events(client: IddaaClient | None = None):
    """Fetch competitions + bulletin and return (events, competitions)."""
    client = client or IddaaClient()
    resolver = MarketResolver.from_client(client)
    competitions = build_competitions_map(client.get_competitions())
    raw_events = client.get_events()
    events = normalize_events(raw_events, resolver, competitions)
    return events, competitions


def get_live_events(force: bool = False):
    """Cached (events, competitions), refreshed every _CACHE_TTL seconds."""
    now = time.time()
    if force or _cache["events"] is None or (now - _cache["ts"]) > _CACHE_TTL:
        events, competitions = fetch_normalized_events()
        _cache.update(ts=now, events=events, competitions=competitions)
    return _cache["events"], _cache["competitions"]


def daily_text(db_path: str = config.DB_PATH, *, save: bool = True) -> str:
    events, competitions = get_live_events()
    now = datetime.now(tz=config.TIMEZONE)
    coupons = engine.build_daily_coupons(events, now=now)
    for_date = now.strftime("%Y-%m-%d")

    if save and (coupons["main"] or coupons["alt"]):
        with Database(db_path) as db:
            db.upsert_competitions(competitions)
            db.save_events(events)
            for c in (coupons["main"], coupons["alt"]):
                if c:
                    db.save_coupon(c, for_date)
    return formatting.format_daily(coupons, for_date)


def surprise_text() -> str:
    events, _ = get_live_events()
    report = surprise.build_surprise(events, now=datetime.now(tz=config.TIMEZONE))
    return formatting.format_surprise(report)
