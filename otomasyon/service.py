"""High-level services shared by the CLI and the Telegram bot.

Fetches the bulletin (with a short cache so repeated bot commands don't hammer
the API), builds coupons / surprise reports, persists coupons, and returns
ready-to-send text.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from . import config, engine, formatting, settlement, surprise
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


def push_daily(db_path: str, client, *, force: bool = False) -> str | None:
    """Proactively send today's daily coupon to the stored chat.

    De-duplicated per day via the ``last_push_date`` setting, so it is safe to
    call repeatedly. Returns the chat id sent to, or None if skipped.
    """
    today = datetime.now(tz=config.TIMEZONE).strftime("%Y-%m-%d")
    with Database(db_path) as db:
        chat_id = db.get_setting("telegram_chat_id")
        if not chat_id:
            return None
        if not force and db.get_setting("last_push_date") == today:
            return None

    text = daily_text(db_path)
    client.send_message(chat_id, text)
    with Database(db_path) as db:
        db.set_setting("last_push_date", today)
    return chat_id


def record_result(db_path: str, result: settlement.MatchResult) -> None:
    with Database(db_path) as db:
        db.save_result(result)


def settle_pending(db_path: str, client=None, *, notify: bool = True) -> list[dict]:
    """Settle any pending coupon whose results are in; optionally notify.

    Returns a list of {coupon, settlement} for the coupons that got decided.
    """
    decided: list[dict] = []
    with Database(db_path) as db:
        chat_id = db.get_setting("telegram_chat_id")
        for coupon in db.get_pending_coupons():
            results = {}
            for leg in coupon["legs"]:
                res = db.get_result(leg["event_id"])
                if res is not None:
                    results[leg["event_id"]] = res
            st = settlement.settle_coupon(coupon["legs"], results)
            if st.status == settlement.PENDING:
                continue
            leg_ids = [leg["id"] for leg in coupon["legs"]]
            db.apply_settlement(coupon["id"], leg_ids, st)
            decided.append({"coupon": coupon, "settlement": st})

    if notify and client and chat_id:
        for item in decided:
            text = formatting.format_settlement(item["coupon"], item["settlement"])
            client.send_message(chat_id, text)
    return decided


def metrics(db_path: str) -> dict:
    """Aggregate performance over decided coupons (flat 1-unit stake)."""
    with Database(db_path) as db:
        coupons = db.settled_coupons()

    played = [c for c in coupons if c["status"] in ("won", "lost")]
    won = [c for c in played if c["status"] == "won"]
    staked = float(len(played))
    returns = 0.0
    odds_sum = 0.0
    for c in played:
        eff = 1.0
        for leg in c["legs"]:
            if leg["result"] == "win":
                eff *= leg["odd"]
        odds_sum += eff
        if c["status"] == "won":
            returns += eff  # 1-unit stake returns eff on a win
    profit = returns - staked
    return {
        "coupons_total": len(coupons),
        "coupons_played": len(played),
        "won": len(won),
        "hit_rate": (len(won) / len(played)) if played else 0.0,
        "avg_odds": (odds_sum / len(played)) if played else 0.0,
        "staked": staked,
        "returns": returns,
        "profit": profit,
        "roi": (profit / staked) if staked else 0.0,
    }


def auto_results(
    db_path: str,
    telegram_client=None,
    *,
    force: bool = False,
    result_client=None,
) -> dict:
    """Fetch Mackolik results for pending coupons, match, settle and notify.

    Calls are rate-limited persistently so invoking this on every bot poll is
    safe. Exact Mackolik ``iddaaCode`` matching is preferred; strict fuzzy
    matching is only a fallback for records without that field.
    """
    from .results import MackolikClient, match_source_results

    now_ts = int(time.time())
    with Database(db_path) as db:
        last_raw = db.get_setting("last_result_poll_ts")
        last = int(last_raw) if last_raw else 0
        if not force and now_ts - last < config.RESULT_POLL_INTERVAL_SECONDS:
            return {"skipped": True, "reason": "rate_limited", "matched": 0, "settled": 0}
        pending = db.get_pending_coupons()

    targets_by_id: dict[int, dict] = {}
    for coupon in pending:
        for leg in coupon["legs"]:
            targets_by_id[leg["event_id"]] = {
                "event_id": leg["event_id"],
                "home": leg["home"],
                "away": leg["away"],
                "start_ts": leg["start_ts"],
            }
    if not targets_by_id:
        with Database(db_path) as db:
            db.set_setting("last_result_poll_ts", str(now_ts))
        return {"skipped": False, "matched": 0, "settled": 0, "dates": []}

    days = sorted(
        {
            datetime.fromtimestamp(event["start_ts"], tz=config.TIMEZONE).date()
            for event in targets_by_id.values()
        }
    )
    result_client = result_client or MackolikClient()
    source_matches = []
    for day in days:
        source_matches.extend(result_client.fetch_date(day))

    matched, diagnostics = match_source_results(
        list(targets_by_id.values()), source_matches
    )
    with Database(db_path) as db:
        # Mark the poll only after every required date fetched successfully.
        # A transient failure can therefore retry on the next bot tick.
        db.set_setting("last_result_poll_ts", str(now_ts))
        for result in matched.values():
            db.save_result(result)

    decided = settle_pending(
        db_path, telegram_client, notify=telegram_client is not None
    )
    return {
        "skipped": False,
        "matched": len(matched),
        "settled": len(decided),
        "dates": [day.isoformat() for day in days],
        "diagnostics": diagnostics,
    }


def backfill_history(
    db_path: str,
    *,
    days: int,
    end_date: date | None = None,
    result_client=None,
    pause_seconds: float = 0.05,
) -> dict:
    """Backfill completed football results/basic odds from the archive feed."""
    from .results import MackolikClient

    end_date = end_date or datetime.now(tz=config.TIMEZONE).date()
    result_client = result_client or MackolikClient()
    saved = 0
    fetched_days = 0
    skipped_days = 0
    errors = []
    with Database(db_path) as db:
        existing_dates = db.historical_dates()
    for offset in range(days, 0, -1):
        day = end_date - timedelta(days=offset)
        if day.isoformat() in existing_dates:
            skipped_days += 1
            continue
        try:
            matches = result_client.fetch_archive_date(day)
            # Archive pages may carry a postponed fixture whose displayed date
            # has moved into the future. Keep only records belonging to the
            # requested calendar day; otherwise a single rescheduled fixture
            # can leak future data into a chronological backtest.
            matches = [
                match
                for match in matches
                if datetime.fromtimestamp(
                    match.start_ts, tz=config.TIMEZONE
                ).date()
                == day
            ]
            with Database(db_path) as db:
                saved += db.save_historical_matches(matches)
            fetched_days += 1
        except Exception as exc:
            errors.append({"date": day.isoformat(), "error": str(exc)})
        if pause_seconds:
            time.sleep(pause_seconds)
    with Database(db_path) as db:
        total = db.count("historical_matches")
    return {
        "days": fetched_days,
        "skipped_days": skipped_days,
        "rows_processed": saved,
        "total": total,
        "errors": errors,
    }


def backfill_clubelo(
    db_path: str,
    *,
    start_date: date,
    end_date: date,
    client=None,
    pause_seconds: float = 0.1,
) -> dict:
    from .clubelo import ClubEloClient

    client = client or ClubEloClient()
    with Database(db_path) as db:
        existing = db.clubelo_dates()
    day = start_date
    fetched = skipped = rows = 0
    errors = []
    while day <= end_date:
        if day.isoformat() in existing:
            skipped += 1
        else:
            try:
                ratings = client.fetch_ratings(day)
                with Database(db_path) as db:
                    rows += db.save_clubelo_ratings(ratings)
                fetched += 1
            except Exception as exc:
                errors.append({"date": day.isoformat(), "error": str(exc)})
            if pause_seconds:
                time.sleep(pause_seconds)
        day += timedelta(days=1)
    return {
        "fetched": fetched,
        "skipped": skipped,
        "rows": rows,
        "errors": errors,
    }


def capture_fotmob_context(
    db_path: str,
    *,
    client=None,
    now: datetime | None = None,
    detail_window_hours: float = 2.0,
    detail_limit: int = 30,
    force: bool = False,
) -> dict:
    """Capture report-only FotMob fixture links and near-kickoff context."""
    from .fotmob import FotMobClient, match_fixtures

    client = client or FotMobClient()
    now = now or datetime.now(tz=config.TIMEZONE)
    captured_ts = int(now.timestamp())
    with Database(db_path) as db:
        last = int(db.get_setting("last_fotmob_context_ts") or 0)
    if (
        not force
        and captured_ts - last < config.CONTEXT_POLL_INTERVAL_SECONDS
    ):
        return {"skipped": True, "live_enabled": False}
    events, competitions = get_live_events()
    relevant_days = {
        datetime.fromtimestamp(event.start_ts, tz=config.TIMEZONE).date()
        for event in events
        if -6 * 3600 <= event.start_ts - int(now.timestamp()) <= 30 * 3600
    }
    fixtures = []
    errors = []
    for day in sorted(relevant_days):
        try:
            fixtures.extend(client.fetch_date(day))
        except Exception as exc:
            errors.append({"scope": day.isoformat(), "error": str(exc)})
    links, diagnostics = match_fixtures(events, fixtures)

    contexts = []
    detail_candidates = sorted(
        (
            fixture
            for fixture in links.values()
            if abs(fixture.start_ts - captured_ts) <= detail_window_hours * 3600
            and not fixture.cancelled
        ),
        key=lambda fixture: abs(fixture.start_ts - captured_ts),
    )[:detail_limit]
    for fixture in detail_candidates:
        try:
            contexts.append(
                client.fetch_context(fixture.match_id, captured_ts=captured_ts)
            )
        except Exception as exc:
            errors.append(
                {"scope": f"match:{fixture.match_id}", "error": str(exc)}
            )

    with Database(db_path) as db:
        db.upsert_competitions(competitions)
        db.save_events(events, now=captured_ts)
        db.save_fotmob_fixtures(fixtures, now=captured_ts)
        db.save_fotmob_links(links, diagnostics, now=captured_ts)
        db.save_fotmob_contexts(contexts)
        db.set_setting("last_fotmob_context_ts", str(captured_ts))
    return {
        "skipped": False,
        "events": len(events),
        "fixtures": len(fixtures),
        "matched": len(links),
        "match_rate": len(links) / len(events) if events else 0.0,
        "details": len(contexts),
        "lineups": sum(item.lineup_available for item in contexts),
        "prematch_lineups": sum(
            item.lineup_available and not item.started for item in contexts
        ),
        "xg": sum(item.xg_home is not None for item in contexts),
        "live_enabled": False,
        "errors": errors,
    }
