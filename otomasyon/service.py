"""High-level services shared by the CLI and the Telegram bot.

Fetches the bulletin (with a short cache so repeated bot commands don't hammer
the API), builds coupons / surprise reports, persists coupons, and returns
ready-to-send text.
"""

from __future__ import annotations

import os
import time
import math
import hashlib
import pickle
import statistics
from collections.abc import Iterable
from itertools import combinations
from datetime import date, datetime, timedelta

from . import config, engine, formatting, probability, settlement, surprise
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

    if save:
        with Database(db_path) as db:
            db.upsert_competitions(competitions)
            db.save_events(events)
            for c in (coupons["main"], coupons["alt"]):
                if c:
                    db.save_coupon(c, for_date)
        capture_shadow_predictions(db_path, events=events, now=now)
    return formatting.format_daily(coupons, for_date)


def surprise_text(db_path: str = config.DB_PATH, *, save: bool = True) -> str:
    events, competitions = get_live_events()
    now = datetime.now(tz=config.TIMEZONE)
    report = surprise.build_surprise(events, now=now)
    if save and report.has_candidates:
        iso_year, iso_week, _ = now.isocalendar()
        period_key = f"{iso_year}-W{iso_week:02d}"
        with Database(db_path) as db:
            db.upsert_competitions(competitions)
            db.save_events(events, now=int(now.timestamp()))
            db.save_surprise_report(
                report, period_key, now=int(now.timestamp())
            )
    return formatting.format_surprise(report)


def _telegram_delivery(response: object, chat_id: int | str) -> tuple[str, int, int | None]:
    """Validate Telegram's successful sendMessage result."""
    if not isinstance(response, dict) or response.get("message_id") is None:
        raise RuntimeError("Telegram sendMessage returned no delivery receipt")
    response_chat = response.get("chat")
    delivered_chat_id = (
        response_chat.get("id")
        if isinstance(response_chat, dict) and response_chat.get("id") is not None
        else chat_id
    )
    telegram_date = response.get("date")
    return (
        str(delivered_chat_id),
        int(response["message_id"]),
        int(telegram_date) if telegram_date is not None else None,
    )


def push_daily(
    db_path: str,
    client,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> str | None:
    """Proactively send today's daily coupon to the stored chat.

    De-duplicated per day via the ``last_push_date`` setting, so it is safe to
    call repeatedly. Returns the chat id sent to, or None if skipped.
    """
    now = now or datetime.now(tz=config.TIMEZONE)
    scheduled = now.replace(
        hour=config.DAILY_PUSH_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    if not force and now < scheduled:
        return None
    today = now.strftime("%Y-%m-%d")
    with Database(db_path) as db:
        chat_id = db.get_setting("telegram_chat_id")
        if not chat_id:
            return None
        if not force and db.get_setting("last_push_date") == today:
            return None

    text = daily_text(db_path)
    response = client.send_message(chat_id, text)
    delivered_chat_id, message_id, telegram_date = _telegram_delivery(
        response, chat_id
    )
    with Database(db_path) as db:
        db.record_telegram_delivery(
            kind="daily_push",
            notification_dates=[today],
            chat_id=delivered_chat_id,
            message_id=message_id,
            telegram_date=telegram_date,
            sent_ts=int(now.timestamp()),
            markers={"last_push_date": today},
        )
    return chat_id


def check_bot_liveness(
    db_path: str, client, *, now: float | None = None
) -> str | None:
    """Announce when the bot stops polling Telegram, and when it recovers.

    A stalled or dead bot cannot report its own silence, so the scheduler
    watches the poll heartbeat instead. Transitions are reported once each, so
    an outage produces one warning and one all-clear rather than a stream.
    """
    now = int(now if now is not None else time.time())
    with Database(db_path) as db:
        db.prune_telegram_poll_log(
            config.TELEGRAM_POLL_LOG_RETENTION_SECONDS, now
        )
        runtime = db.telegram_bot_runtime()
        chat_id = db.get_setting("telegram_chat_id")
        if runtime is None or not chat_id:
            return None
        last_poll = runtime.get("last_poll_ts") or runtime.get("heartbeat_ts") or 0
        silent_for = now - int(last_poll)
        stale = silent_for > config.TELEGRAM_WATCHDOG_STALE_SECONDS
        previous = db.get_setting("telegram_watchdog_status") or "ok"

    if stale and previous != "stale":
        minutes = silent_for // 60
        text = (
            "UYARI: Telegram botu yanıt vermiyor.\n"
            f"Son başarılı bağlantı {minutes} dakika önce.\n"
            "Komutlarınız kaybolmaz; bot döndüğünde sırayla yanıtlanır."
        )
        transition = "alerted"
    elif not stale and previous == "stale":
        text = (
            "Telegram botu tekrar çalışıyor. "
            "Bekleyen komutlarınız işlendi."
        )
        transition = "recovered"
    else:
        return None

    response = client.send_message(chat_id, text)
    delivered_chat_id, message_id, telegram_date = _telegram_delivery(
        response, chat_id
    )
    with Database(db_path) as db:
        # The status marker is written with the receipt, so a crash between the
        # two can never suppress a warning that was never delivered.
        db.record_telegram_delivery(
            kind="bot_watchdog",
            notification_dates=[f"{transition}:{now}"],
            chat_id=delivered_chat_id,
            message_id=message_id,
            telegram_date=telegram_date,
            sent_ts=now,
            markers={"telegram_watchdog_status": "stale" if stale else "ok"},
        )
    return transition


def record_result(db_path: str, result: settlement.MatchResult) -> None:
    with Database(db_path) as db:
        db.save_result(result)


roi_interval = probability.roi_interval
roi_text = probability.roi_text


def settlement_dedupe_key(coupon_id: int) -> str:
    return f"coupon_settled:{int(coupon_id)}"


def settle_pending(db_path: str, client=None, *, notify: bool = True) -> list[dict]:
    """Settle any pending coupon whose results are in; queue notifications.

    The notification is queued rather than sent inline. A coupon is settled
    only once, so a send that failed inline could never be retried and the
    result silently disappeared. Queuing separates "decided" from "delivered".

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
            if notify and chat_id:
                db.queue_notification(
                    dedupe_key=settlement_dedupe_key(coupon["id"]),
                    kind="coupon_settled",
                    chat_id=chat_id,
                    body=formatting.format_settlement(coupon, st),
                    now=int(time.time()),
                )

    if notify and client:
        drain_notifications(db_path, client)
    return decided


def queue_missing_settlement_notifications(
    db_path: str, *, for_dates: Iterable[str] | None = None
) -> int:
    """Queue results that were decided but never announced.

    Settlement used to write the coupon status before attempting delivery, so a
    failed send left a decided coupon with no notification and no way to retry.
    This reconciles those, bounded to the given dates so old history is not
    replayed into the chat.
    """
    today = datetime.now(tz=config.TIMEZONE).strftime("%Y-%m-%d")
    dates = set(for_dates) if for_dates is not None else {today}
    queued = 0
    now = int(time.time())
    with Database(db_path) as db:
        chat_id = db.get_setting("telegram_chat_id")
        if not chat_id:
            return 0
        for coupon in db.decided_coupons_for_dates(dates):
            key = settlement_dedupe_key(coupon["id"])
            if db.notification(key) is not None:
                continue
            st = settlement.settlement_from_coupon(coupon)
            if db.queue_notification(
                dedupe_key=key,
                kind="coupon_settled",
                chat_id=chat_id,
                body=formatting.format_settlement(coupon, st),
                now=now,
            ):
                queued += 1
    return queued


def drain_notifications(db_path: str, client, *, now: int | None = None) -> dict:
    """Deliver queued notifications, retrying transient failures.

    A notification is marked sent only after Telegram acknowledges it, so a
    crash or network failure delays delivery instead of dropping it.
    """
    now = int(now if now is not None else time.time())
    sent = 0
    retried = 0
    failed = 0
    with Database(db_path) as db:
        db.recover_notifications(now)
        due = db.due_notifications(now)

    for candidate in due:
        with Database(db_path) as db:
            row = db.begin_notification_send(candidate["id"], now)
        if row is None:
            continue
        try:
            response = client.send_message(row["chat_id"], row["body"])
            _, message_id, _ = _telegram_delivery(response, row["chat_id"])
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            with Database(db_path) as db:
                if row["attempts"] >= config.NOTIFICATION_MAX_ATTEMPTS:
                    db.fail_notification_send(row["id"], detail)
                    failed += 1
                else:
                    delay = min(
                        config.NOTIFICATION_RETRY_BASE_SECONDS
                        * (2 ** max(0, row["attempts"] - 1)),
                        config.NOTIFICATION_RETRY_MAX_SECONDS,
                    )
                    db.retry_notification_send(row["id"], detail, now + delay)
                    retried += 1
            continue
        with Database(db_path) as db:
            db.complete_notification_send(
                row["id"], message_id=message_id, now=now
            )
        sent += 1
    return {"sent": sent, "retried": retried, "failed": failed}


def deliver_pending_notifications(db_path: str, client) -> dict:
    """Reconcile missed results, then deliver everything still queued."""
    queue_missing_settlement_notifications(db_path)
    return drain_notifications(db_path, client)


def metrics(db_path: str) -> dict:
    """Aggregate performance over decided coupons (flat 1-unit stake)."""
    with Database(db_path) as db:
        coupons = db.settled_coupons()

    coupons = [
        coupon
        for coupon in coupons
        if coupon.get("notes") != "legacy_ineligible"
    ]
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


def metrics_by_kind(db_path: str) -> dict[str, dict]:
    """Independent performance and evidence gates for each coupon process."""
    with Database(db_path) as db:
        coupons = db.settled_coupons()
    coupons = [
        coupon
        for coupon in coupons
        if coupon.get("notes") != "legacy_ineligible"
    ]
    output = {}
    kinds = set(config.PERFORMANCE_GATE_MIN_COUPONS) | {
        coupon["kind"] for coupon in coupons
    }
    for kind in sorted(kinds):
        selected = [
            coupon
            for coupon in coupons
            if coupon["kind"] == kind and coupon["status"] in ("won", "lost")
        ]
        profits = []
        odds = []
        clv_values = []
        for coupon in selected:
            effective = 1.0
            for leg in coupon["legs"]:
                if leg["result"] == "win":
                    effective *= leg["odd"]
                if leg.get("closing_odd") and leg["closing_odd"] > 1.0:
                    clv_values.append(
                        leg["odd"] / leg["closing_odd"] - 1.0
                    )
            odds.append(effective)
            profits.append(effective - 1.0 if coupon["status"] == "won" else -1.0)
        roi, interval = roi_interval(profits)
        minimum = config.PERFORMANCE_GATE_MIN_COUPONS.get(kind, 200)
        output[kind] = {
            "coupons": len(selected),
            "won": sum(coupon["status"] == "won" for coupon in selected),
            "lost": sum(coupon["status"] == "lost" for coupon in selected),
            "hit_rate": (
                sum(coupon["status"] == "won" for coupon in selected)
                / len(selected)
                if selected
                else 0.0
            ),
            "avg_odds": statistics.mean(odds) if odds else 0.0,
            "avg_clv": statistics.mean(clv_values) if clv_values else None,
            "clv_samples": len(clv_values),
            "roi": roi,
            "roi_ci95": interval,
            "minimum_coupons": minimum,
            "gate_passed": (
                len(selected) >= minimum
                and interval is not None
                and interval[0] > 0.0
            ),
        }
    with Database(db_path) as db:
        rows = db.conn.execute(
            """
            SELECT ss.roi, ss.profit, ss.columns, ss.unit_stake
            FROM surprise_scenarios ss
            JOIN surprise_reports sr ON sr.id=ss.report_id
            WHERE sr.status='settled' AND ss.system_size=?
              AND ss.roi IS NOT NULL
            """,
            (config.SURPRISE_TRACK_SYSTEM_SIZE,),
        ).fetchall()
    report_rois = [row["roi"] for row in rows]
    total_stake = sum(row["columns"] * row["unit_stake"] for row in rows)
    total_profit = sum(row["profit"] for row in rows)
    surprise_roi = total_profit / total_stake if total_stake else 0.0
    surprise_margin = (
        1.96 * statistics.stdev(report_rois) / math.sqrt(len(report_rois))
        if len(report_rois) > 1
        else 0.0
    )
    minimum = config.PERFORMANCE_GATE_MIN_COUPONS["surprise"]
    output["surprise"] = {
        "coupons": len(rows),
        "won": sum(row["profit"] > 0 for row in rows),
        "hit_rate": (
            sum(row["profit"] > 0 for row in rows) / len(rows) if rows else 0.0
        ),
        "avg_odds": 0.0,
        "avg_clv": None,
        "clv_samples": 0,
        "roi": surprise_roi,
        "roi_ci95": (
            surprise_roi - surprise_margin,
            surprise_roi + surprise_margin,
        ),
        "minimum_coupons": minimum,
        "gate_passed": (
            len(rows) >= minimum and surprise_roi - surprise_margin > 0.0
        ),
        "system_size": config.SURPRISE_TRACK_SYSTEM_SIZE,
    }
    return output


def surprise_category_metrics(db_path: str) -> dict[str, dict]:
    """Track 4.5 Over and 6+ candidates independently as flat single picks."""
    with Database(db_path) as db:
        rows = db.conn.execute(
            """
            SELECT category, result, odd
            FROM surprise_candidates
            WHERE result IN ('win','lose')
            """
        ).fetchall()
    output = {}
    for category in config.SURPRISE_CATEGORIES:
        selected = [row for row in rows if row["category"] == category]
        profits = [
            row["odd"] - 1.0 if row["result"] == "win" else -1.0
            for row in selected
        ]
        output[category] = {
            "candidates": len(selected),
            "wins": sum(row["result"] == "win" for row in selected),
            "hit_rate": (
                sum(row["result"] == "win" for row in selected) / len(selected)
                if selected
                else 0.0
            ),
            "avg_odds": (
                statistics.mean(row["odd"] for row in selected)
                if selected
                else 0.0
            ),
            "roi": statistics.mean(profits) if profits else 0.0,
        }
    return output


def load_cached_model(
    db_path: str,
    model_version: str,
    *,
    run_date: str | None = None,
):
    """Load and verify a trusted internally-produced model artifact."""
    with Database(db_path) as db:
        artifact = db.load_model_artifact(
            model_version, run_date=run_date
        )
    if artifact is None:
        return None
    payload = bytes(artifact["payload"])
    if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
        raise RuntimeError(f"model artifact checksum failed: {model_version}")
    return pickle.loads(payload)


def capture_shadow_predictions(
    db_path: str,
    *,
    events=None,
    now: datetime | None = None,
) -> dict:
    """Persist separate goal/Elo and xG O/U shadow predictions."""
    from .eligibility import is_event_eligible
    from .model import GoalModel

    now = now or datetime.now(tz=config.TIMEZONE)
    now_ts = int(now.timestamp())
    if events is None:
        events, _ = get_live_events()
    today = now.strftime("%Y-%m-%d")
    models = {
        config.MODEL_GOAL_SHADOW_VERSION: load_cached_model(
            db_path, config.MODEL_GOAL_SHADOW_VERSION, run_date=today
        ),
        config.MODEL_SHADOW_VERSION: load_cached_model(
            db_path, config.MODEL_SHADOW_VERSION, run_date=today
        ),
    }
    if any(model is None for model in models.values()):
        with Database(db_path) as db:
            history = db.load_historical_matches(before_ts=now_ts)
        if models[config.MODEL_GOAL_SHADOW_VERSION] is None:
            models[config.MODEL_GOAL_SHADOW_VERSION] = GoalModel(
                history, now_ts, use_xg=False
            )
        if models[config.MODEL_SHADOW_VERSION] is None:
            models[config.MODEL_SHADOW_VERSION] = GoalModel(
                history, now_ts, use_xg=True
            )
    predictions = []
    counts = {
        config.MODEL_GOAL_SHADOW_VERSION: 0,
        config.MODEL_SHADOW_VERSION: 0,
    }
    for event in events:
        if not (now_ts < event.start_ts <= now_ts + 24 * 3600):
            continue
        if not is_event_eligible(
            event.competition_name, event.home, event.away
        ):
            continue
        market = next(
            (
                item
                for item in event.markets
                if item.code == config.MARKET_OVER_UNDER
                and str(item.sov).replace(",", ".") == "2.5"
                and item.status == 1
            ),
            None,
        )
        if market is None or len(market.selections) != 2:
            continue
        odds = market.odds
        if not all(odd is not None and odd > 1.0 for odd in odds):
            continue
        fair = probability.fair_probs(odds)
        for model_version, model in models.items():
            prediction = model.predict(
                event.home, event.away, event.competition_name
            )
            if prediction.confidence < 0.35:
                continue
            if (
                model_version == config.MODEL_SHADOW_VERSION
                and (
                    prediction.xg_samples_home < 3
                    or prediction.xg_samples_away < 3
                )
            ):
                continue
            choices = []
            for index, selection in enumerate(market.selections):
                key = (
                    "Alt 2.5"
                    if selection.name == "Alt"
                    else "Üst 2.5"
                    if selection.name == "Üst"
                    else None
                )
                if key:
                    choices.append(
                        (
                            prediction.probs[key] - fair[index],
                            index,
                            selection,
                            key,
                        )
                    )
            if not choices:
                continue
            edge, index, selection, key = max(choices)
            predictions.append(
                {
                    "model_version": model_version,
                    "for_date": today,
                    "event_id": event.event_id,
                    "captured_ts": now_ts,
                    "market": "OU25",
                    "outcome_name": selection.name,
                    "odd": selection.odd,
                    "predicted_prob": prediction.probs[key],
                    "market_fair": fair[index],
                    "edge": edge,
                }
            )
            counts[model_version] += 1
    with Database(db_path) as db:
        saved = db.save_model_predictions(predictions)
    return {
        "eligible": len(predictions),
        "saved": saved,
        "by_model": counts,
        "live_enabled": False,
    }


def settle_shadow_predictions(db_path: str) -> int:
    settled = 0
    with Database(db_path) as db:
        rows = db.conn.execute(
            """
            SELECT mp.id, mp.outcome_name, r.home_score, r.away_score,
                   r.status
            FROM model_predictions mp
            JOIN results r ON r.event_id=mp.event_id
            WHERE mp.result='pending'
            """
        ).fetchall()
        for row in rows:
            if row["status"] in ("postponed", "cancelled"):
                outcome = "void"
            elif row["home_score"] is None or row["away_score"] is None:
                continue
            else:
                total = row["home_score"] + row["away_score"]
                won = (
                    row["outcome_name"] == "Alt" and total < 2.5
                ) or (
                    row["outcome_name"] == "Üst" and total > 2.5
                )
                outcome = "win" if won else "lose"
            db.conn.execute(
                "UPDATE model_predictions SET result=? WHERE id=?",
                (outcome, row["id"]),
            )
            settled += 1
        db.conn.commit()
    return settled


def shadow_model_metrics(
    db_path: str, model_version: str = config.MODEL_SHADOW_VERSION
) -> dict:
    with Database(db_path) as db:
        rows = db.conn.execute(
            """
            SELECT * FROM model_predictions
            WHERE model_version=? AND result IN ('win','lose')
              AND edge>=?
            """,
            (model_version, config.MODEL_MIN_EDGE),
        ).fetchall()
    profits = [
        row["odd"] - 1.0 if row["result"] == "win" else -1.0
        for row in rows
    ]
    brier = [
        (row["predicted_prob"] - (1.0 if row["result"] == "win" else 0.0))
        ** 2
        for row in rows
    ]
    roi, interval = roi_interval(profits)
    return {
        "model_version": model_version,
        "predictions": len(rows),
        "wins": sum(row["result"] == "win" for row in rows),
        "roi": roi,
        "roi_ci95": interval,
        "brier": statistics.mean(brier) if brier else None,
        "gate_passed": (
            len(rows) >= config.MODEL_GATE_MIN_BETS
            and interval is not None
            and interval[0] > 0.0
        ),
        "live_enabled": False,
    }


def build_walk_forward_archive(
    db_path: str,
    *,
    start_date: str,
    end_date: str,
    model_version: str = config.MODEL_WALK_FORWARD_VERSION,
) -> dict:
    """Persist one leakage-safe O/U prediction per xG-covered historical match."""
    from .eligibility import is_event_eligible
    from .model import GoalModel

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    with Database(db_path) as db:
        history = db.load_historical_matches()
        existing = {
            row["historical_source_id"]
            for row in db.conn.execute(
                """
                SELECT historical_source_id FROM walk_forward_predictions
                WHERE model_version=? AND prediction_date BETWEEN ? AND ?
                """,
                (model_version, start_date, end_date),
            ).fetchall()
        }
    targets_by_date: dict[str, list[dict]] = {}
    for row in history:
        if not (start_date <= row["match_date"] <= end_date):
            continue
        if row["source_id"] in existing:
            continue
        if row.get("xg_home") is None or row.get("xg_away") is None:
            continue
        if not all((row.get("odds_under25"), row.get("odds_over25"))):
            continue
        if not is_event_eligible(
            row.get("competition") or "", row["home"], row["away"]
        ):
            continue
        targets_by_date.setdefault(row["match_date"], []).append(row)

    saved = eligible = 0
    trained_days = 0
    created_ts = int(time.time())
    for day in sorted(targets_by_date):
        day_value = date.fromisoformat(day)
        cutoff = int(
            datetime.combine(
                day_value, datetime.min.time(), tzinfo=config.TIMEZONE
            ).timestamp()
        )
        model = GoalModel(history, cutoff, use_xg=True)
        trained_days += 1
        predictions = []
        for row in targets_by_date[day]:
            prediction = model.predict(
                row["home"], row["away"], row.get("competition")
            )
            if (
                prediction.xg_samples_home < 3
                or prediction.xg_samples_away < 3
            ):
                continue
            odds = [row["odds_under25"], row["odds_over25"]]
            fair = probability.fair_probs(odds)
            options = [
                (
                    prediction.probs["Alt 2.5"] - fair[0],
                    "Alt",
                    odds[0],
                    prediction.probs["Alt 2.5"],
                    fair[0],
                ),
                (
                    prediction.probs["Üst 2.5"] - fair[1],
                    "Üst",
                    odds[1],
                    prediction.probs["Üst 2.5"],
                    fair[1],
                ),
            ]
            edge, outcome, odd, predicted_prob, market_fair = max(options)
            actual = (
                "Alt"
                if row["ft_home"] + row["ft_away"] < 2.5
                else "Üst"
            )
            won = outcome == actual
            predictions.append(
                {
                    "model_version": model_version,
                    "historical_source_id": row["source_id"],
                    "prediction_date": day,
                    "cutoff_ts": cutoff,
                    "market": "OU25",
                    "outcome_name": outcome,
                    "odd": odd,
                    "predicted_prob": predicted_prob,
                    "market_fair": market_fair,
                    "edge": edge,
                    "actual_result": actual,
                    "won": won,
                    "profit": odd - 1.0 if won else -1.0,
                    "xg_samples_home": prediction.xg_samples_home,
                    "xg_samples_away": prediction.xg_samples_away,
                    "created_ts": created_ts,
                }
            )
        eligible += len(predictions)
        if predictions:
            with Database(db_path) as db:
                saved += db.save_walk_forward_predictions(predictions)
    return {
        "model_version": model_version,
        "start_date": start_date,
        "end_date": end_date,
        "target_days": len(targets_by_date),
        "trained_days": trained_days,
        "eligible_predictions": eligible,
        "saved": saved,
        "already_present": len(existing),
    }


def walk_forward_metrics(
    db_path: str,
    *,
    model_version: str = config.MODEL_WALK_FORWARD_VERSION,
    min_edge: float = config.MODEL_MIN_EDGE,
) -> dict:
    with Database(db_path) as db:
        all_rows = db.load_walk_forward_predictions(model_version)
    rows = [row for row in all_rows if row["edge"] >= min_edge]
    profits = [row["profit"] for row in rows]
    roi, interval = roi_interval(profits)
    model_brier = [
        (row["predicted_prob"] - row["won"]) ** 2 for row in rows
    ]
    market_brier = [
        (row["market_fair"] - row["won"]) ** 2 for row in rows
    ]
    outcomes = {}
    for outcome in ("Alt", "Üst"):
        selected = [row for row in rows if row["outcome_name"] == outcome]
        outcome_profits = [row["profit"] for row in selected]
        outcomes[outcome] = {
            "predictions": len(selected),
            "wins": sum(row["won"] for row in selected),
            "roi": (
                statistics.mean(outcome_profits) if outcome_profits else 0.0
            ),
        }
    calibration = []
    for lower in (0.5, 0.6, 0.7, 0.8, 0.9):
        upper = lower + 0.1
        selected = [
            row
            for row in rows
            if lower <= row["predicted_prob"] < upper
        ]
        if selected:
            calibration.append(
                {
                    "lower": lower,
                    "upper": upper,
                    "predictions": len(selected),
                    "avg_probability": statistics.mean(
                        row["predicted_prob"] for row in selected
                    ),
                    "actual_rate": statistics.mean(
                        row["won"] for row in selected
                    ),
                }
            )
    return {
        "model_version": model_version,
        "all_predictions": len(all_rows),
        "qualified_predictions": len(rows),
        "wins": sum(row["won"] for row in rows),
        "hit_rate": (
            sum(row["won"] for row in rows) / len(rows) if rows else 0.0
        ),
        "avg_odds": (
            statistics.mean(row["odd"] for row in rows) if rows else 0.0
        ),
        "roi": roi,
        "roi_ci95": interval,
        "model_brier": (
            statistics.mean(model_brier) if model_brier else None
        ),
        "market_brier": (
            statistics.mean(market_brier) if market_brier else None
        ),
        "outcomes": outcomes,
        "calibration": calibration,
        "min_edge": min_edge,
    }


def settle_surprise_reports(db_path: str) -> int:
    """Settle candidate outcomes and theoretical system returns."""
    settled_reports = 0
    with Database(db_path) as db:
        reports = db.conn.execute(
            "SELECT id FROM surprise_reports WHERE status='pending'"
        ).fetchall()
        for report in reports:
            candidates = db.conn.execute(
                "SELECT * FROM surprise_candidates WHERE report_id=?",
                (report["id"],),
            ).fetchall()
            for candidate in candidates:
                if candidate["result"] != "pending":
                    continue
                result = db.get_result(candidate["event_id"])
                if result is None:
                    continue
                leg_result = settlement.settle_leg(
                    candidate["market_t"],
                    candidate["market_st"],
                    candidate["market_sov"],
                    candidate["outcome_name"],
                    result,
                )
                db.conn.execute(
                    "UPDATE surprise_candidates SET result=? WHERE id=?",
                    (leg_result, candidate["id"]),
                )
            remaining = db.conn.execute(
                """
                SELECT COUNT(*) AS n FROM surprise_candidates
                WHERE report_id=? AND result='pending'
                """,
                (report["id"],),
            ).fetchone()["n"]
            if remaining:
                db.conn.commit()
                continue
            system_candidates = [
                dict(row)
                for row in db.conn.execute(
                    """
                    SELECT * FROM surprise_candidates
                    WHERE report_id=? AND in_system=1
                    """,
                    (report["id"],),
                ).fetchall()
            ]
            scenarios = db.conn.execute(
                "SELECT * FROM surprise_scenarios WHERE report_id=?",
                (report["id"],),
            ).fetchall()
            for scenario in scenarios:
                total_return = 0.0
                for column in combinations(
                    system_candidates, scenario["system_size"]
                ):
                    if any(item["result"] == "lose" for item in column):
                        continue
                    effective = 1.0
                    for item in column:
                        if item["result"] == "win":
                            effective *= item["odd"]
                    total_return += effective * scenario["unit_stake"]
                stake = scenario["columns"] * scenario["unit_stake"]
                profit = total_return - stake
                roi = profit / stake if stake else 0.0
                db.conn.execute(
                    """
                    UPDATE surprise_scenarios SET profit=?, roi=?
                    WHERE report_id=? AND system_size=?
                    """,
                    (
                        profit,
                        roi,
                        report["id"],
                        scenario["system_size"],
                    ),
                )
            db.conn.execute(
                "UPDATE surprise_reports SET status='settled' WHERE id=?",
                (report["id"],),
            )
            db.conn.commit()
            settled_reports += 1
    return settled_reports


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
        pending_surprise = db.pending_surprise_events()
        pending_predictions = db.pending_model_prediction_events()

    targets_by_id: dict[int, dict] = {}
    for coupon in pending:
        for leg in coupon["legs"]:
            targets_by_id[leg["event_id"]] = {
                "event_id": leg["event_id"],
                "home": leg["home"],
                "away": leg["away"],
                "start_ts": leg["start_ts"],
            }
    for event in pending_surprise:
        targets_by_id[event["event_id"]] = {
            "event_id": event["event_id"],
            "home": event["home"],
            "away": event["away"],
            "start_ts": event["start_ts"],
        }
    for event in pending_predictions:
        targets_by_id[event["event_id"]] = {
            "event_id": event["event_id"],
            "home": event["home"],
            "away": event["away"],
            "start_ts": event["start_ts"],
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
    surprise_settled = settle_surprise_reports(db_path)
    predictions_settled = settle_shadow_predictions(db_path)
    return {
        "skipped": False,
        "matched": len(matched),
        "settled": len(decided),
        "surprise_settled": surprise_settled,
        "predictions_settled": predictions_settled,
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


def auto_history_archive(
    db_path: str,
    *,
    now: datetime | None = None,
    force: bool = False,
    result_client=None,
) -> dict:
    """Archive every completed match from recent days, once per date."""
    from .results import MackolikClient

    now = now or datetime.now(tz=config.TIMEZONE)
    now_ts = int(now.timestamp())
    if not force and now.hour < config.HISTORY_ARCHIVE_HOUR:
        return {
            "skipped": True,
            "reason": "before_archive_hour",
            "days": [],
            "rows": 0,
            "errors": [],
        }
    with Database(db_path) as db:
        last = int(db.get_setting("last_history_archive_poll_ts") or 0)
        if (
            not force
            and now_ts - last < config.HISTORY_ARCHIVE_POLL_INTERVAL_SECONDS
        ):
            return {
                "skipped": True,
                "reason": "rate_limited",
                "days": [],
                "rows": 0,
                "errors": [],
            }
        pending_days = []
        for offset in range(1, config.HISTORY_ARCHIVE_RETRY_DAYS + 1):
            day = now.date() - timedelta(days=offset)
            if db.get_setting(f"history_archive:{day.isoformat()}") is None:
                pending_days.append(day)
        db.set_setting("last_history_archive_poll_ts", str(now_ts))

    client = result_client or MackolikClient()
    archived_days = []
    saved_rows = 0
    errors = []
    for day in reversed(pending_days):
        try:
            matches = [
                match
                for match in client.fetch_archive_date(day)
                if datetime.fromtimestamp(
                    match.start_ts, tz=config.TIMEZONE
                ).date()
                == day
            ]
            with Database(db_path) as db:
                saved_rows += db.save_historical_matches(matches)
                db.set_setting(
                    f"history_archive:{day.isoformat()}", str(len(matches))
                )
            archived_days.append(day.isoformat())
        except Exception as exc:
            errors.append({"date": day.isoformat(), "error": str(exc)})
    return {
        "skipped": False,
        "days": archived_days,
        "rows": saved_rows,
        "errors": errors,
    }


def notify_completed_history_archives(
    db_path: str,
    telegram_client,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Notify once per successfully archived date; retry sends on failure."""
    now = now or datetime.now(tz=config.TIMEZONE)
    with Database(db_path) as db:
        chat_id = db.get_setting("telegram_chat_id")
        if not chat_id:
            return []
        archived = db.conn.execute(
            """
            SELECT key, value FROM app_settings
            WHERE key GLOB 'history_archive:????-??-??'
            ORDER BY key
            """
        ).fetchall()
        pending = [
            (row["key"].split(":", 1)[1], int(row["value"]))
            for row in archived
            if db.get_setting(
                f"history_archive_notified:{row['key'].split(':', 1)[1]}"
            )
            is None
        ]
        if not pending:
            return []
        missing = 0
        for offset in range(1, config.HISTORY_ARCHIVE_RETRY_DAYS + 1):
            day = now.date() - timedelta(days=offset)
            if db.get_setting(f"history_archive:{day.isoformat()}") is None:
                missing += 1
        historical_total = db.count("historical_matches")
    dates = [day for day, _ in pending]
    rows = sum(count for _, count in pending)
    text = "\n".join(
        [
            "GECE VERİ ARŞİVİ TAMAMLANDI",
            f"Arşivlenen tarih: {', '.join(dates)}",
            f"Kaynak maç kaydı: {rows}",
            f"Toplam tarihsel veri: {historical_total} maç",
            f"Yeniden denenecek gün: {missing}",
            "Yeni veriler bir sonraki model eğitiminde kullanılacak.",
        ]
    )
    response = telegram_client.send_message(chat_id, text)
    delivered_chat_id, message_id, telegram_date = _telegram_delivery(
        response, chat_id
    )
    sent_ts = int(now.timestamp())
    with Database(db_path) as db:
        db.record_telegram_delivery(
            kind="history_archive",
            notification_dates=dates,
            chat_id=delivered_chat_id,
            message_id=message_id,
            telegram_date=telegram_date,
            sent_ts=sent_ts,
            markers={
                f"history_archive_notified:{day}": str(sent_ts)
                for day in dates
            },
        )
    return dates


def archive_and_notify(
    db_path: str,
    telegram_client,
    *,
    now: datetime | None = None,
    force: bool = False,
    result_client=None,
) -> dict:
    now = now or datetime.now(tz=config.TIMEZONE)
    report = auto_history_archive(
        db_path,
        now=now,
        force=force,
        result_client=result_client,
    )
    report["notified_days"] = notify_completed_history_archives(
        db_path, telegram_client, now=now
    )
    return report


def pending_coupon_summary(db_path: str) -> dict:
    """Coupons still awaiting results.

    Performance figures only move when a coupon settles, so an unchanged
    report is expected while today's coupons are still running. Saying so
    removes the ambiguity between "nothing happened" and "nothing updated".
    """
    with Database(db_path) as db:
        rows = db.conn.execute(
            """
            SELECT for_date, COUNT(*) AS total FROM coupons
            WHERE status='pending'
              AND (notes IS NULL OR notes <> 'legacy_ineligible')
            GROUP BY for_date ORDER BY for_date
            """
        ).fetchall()
    return {
        "coupons": sum(row["total"] for row in rows),
        "dates": [row["for_date"] for row in rows],
    }


def host_downtime(db_path: str, *, since_ts: int | None = None) -> dict:
    """Windows in which the machine itself stopped running the automation.

    The bot polls every ~15s and the scheduler wakes every ~25s as independent
    processes. When both stop for the identical stretch, nothing inside the
    application can be responsible: the host was suspended. Detecting this
    matters because during such a window a Telegram message stays unanswered no
    matter how robust the code is.
    """
    since_ts = since_ts or int(time.time()) - 24 * 60 * 60
    with Database(db_path) as db:
        polls = [
            row["ended_ts"]
            for row in db.conn.execute(
                """
                SELECT ended_ts FROM telegram_poll_log
                WHERE outcome='ok' AND ended_ts>=? ORDER BY ended_ts
                """,
                (since_ts,),
            )
        ]
        cycles = [
            row["started_ts"]
            for row in db.conn.execute(
                """
                SELECT DISTINCT started_ts FROM scheduler_callback_runs
                WHERE started_ts>=? ORDER BY started_ts
                """,
                (since_ts,),
            )
        ]

    def _gaps(stamps, normal):
        return [
            {"from_ts": first, "seconds": second - first}
            for first, second in zip(stamps, stamps[1:])
            if second - first > normal
        ]

    poll_gaps = _gaps(polls, config.HOST_STALL_SECONDS)
    cycle_gaps = _gaps(cycles, config.HOST_STALL_SECONDS)

    # Only count a window as host downtime when both processes lost the same
    # stretch; a gap in one alone is an application problem worth keeping
    # separate.
    windows = []
    for gap in poll_gaps:
        match = next(
            (
                other
                for other in cycle_gaps
                if abs(other["from_ts"] - gap["from_ts"]) < config.HOST_STALL_SECONDS
            ),
            None,
        )
        if match is not None:
            windows.append(
                {
                    "from_ts": gap["from_ts"],
                    "seconds": max(gap["seconds"], match["seconds"]),
                }
            )

    total = sum(window["seconds"] for window in windows)
    return {
        "windows": windows,
        "count": len(windows),
        "total_seconds": total,
        "longest_seconds": max((w["seconds"] for w in windows), default=0),
        "scheduler_gaps": len(cycle_gaps),
        "poll_gaps": len(poll_gaps),
    }


def host_downtime_text(db_path: str, *, since_ts: int | None = None) -> str:
    """A plain statement of whether the machine was actually up."""
    report = host_downtime(db_path, since_ts=since_ts)
    if not report["count"]:
        return "Sunucu: son 24 saatte kesinti görünmüyor"
    longest = report["longest_seconds"] / 60
    total = report["total_seconds"] / 60
    last = datetime.fromtimestamp(
        report["windows"][-1]["from_ts"], tz=config.TIMEZONE
    ).strftime("%d.%m %H:%M")
    return (
        f"Sunucu: son 24 saatte {report['count']} kez durdu · "
        f"toplam {total:.0f} dk · en uzunu {longest:.0f} dk · son {last}\n"
        "  (Bu sürelerde makine çalışmadığı için mesajlara cevap verilemez.)"
    )


def model_status_text(db_path: str) -> str:
    processes = metrics_by_kind(db_path)
    shadow = shadow_model_metrics(db_path)
    goal_shadow = shadow_model_metrics(
        db_path, config.MODEL_GOAL_SHADOW_VERSION
    )
    walk_forward = walk_forward_metrics(db_path)
    goals = surprise_category_metrics(db_path)["goals_6plus"]
    training = latest_model_training(db_path)
    lines = ["09:45 MODEL / PERFORMANS DURUMU", host_downtime_text(db_path)]
    if training:
        trained = datetime.fromtimestamp(
            training["trained_ts"], tz=config.TIMEZONE
        ).strftime("%d.%m %H:%M")
        lines.extend(
            [
                (
                    f"Son eğitim: {trained} · "
                    f"{training['model_version']} · {training['status']}"
                ),
                (
                    f"Eğitim verisi: {training['history_matches']} maç · "
                    f"{training['xg_matches']} xG maçı"
                ),
            ]
        )
    else:
        lines.append("Son eğitim: henüz kayıt yok")
    labels = {
        "daily_main": "Ana kupon",
        "daily_alt": "Alternatif",
    }
    for kind in ("daily_main", "daily_alt"):
        item = processes[kind]
        clv = (
            f"{item['avg_clv']*100:+.1f}% ({item['clv_samples']})"
            if item["avg_clv"] is not None
            else "veri yok"
        )
        lines.extend(
            [
                "",
                (
                    f"{labels[kind]} — {item['won']} tuttu / "
                    f"{item['lost']} tutmadı "
                    f"({item['coupons']} sonuçlandı, hedef "
                    f"{item['minimum_coupons']})"
                ),
                roi_text(item["roi"], item["roi_ci95"]),
                f"CLV: {clv} | Kapı: {'GEÇTİ' if item['gate_passed'] else 'BEKLİYOR'}",
            ]
        )
    pending = pending_coupon_summary(db_path)
    if pending["coupons"]:
        lines.append("")
        lines.append(
            f"Bekleyen: {pending['coupons']} kupon henüz sonuçlanmadı "
            f"({', '.join(pending['dates'])}). Rakamlar bunlar "
            "sonuçlanınca değişir."
        )
    lines.extend(
        [
            "",
            (
                f"Walk-forward ({walk_forward['model_version']}) — "
                f"{walk_forward['qualified_predictions']}/"
                f"{walk_forward['all_predictions']} edge uygun"
            ),
            roi_text(walk_forward["roi"], walk_forward["roi_ci95"]),
            (
                f"Brier model/piyasa: {walk_forward['model_brier']:.4f}/"
                f"{walk_forward['market_brier']:.4f}"
                if walk_forward["model_brier"] is not None
                else "Brier model/piyasa: veri yok"
            ),
            "",
            f"xG gölge ({shadow['model_version']}) — {shadow['predictions']}/"
            f"{config.MODEL_GATE_MIN_BETS} sonuç",
            roi_text(shadow["roi"], shadow["roi_ci95"]),
            (
                f"Brier: {shadow['brier']:.3f}"
                if shadow["brier"] is not None
                else "Brier: veri yok"
            ),
            f"Kapı: {'GEÇTİ' if shadow['gate_passed'] else 'BEKLİYOR'}",
            "",
            f"Gol+Elo gölge ({goal_shadow['model_version']}) — "
            f"{goal_shadow['predictions']}/{config.MODEL_GATE_MIN_BETS} sonuç",
            roi_text(goal_shadow["roi"], goal_shadow["roi_ci95"]),
            (
                f"Brier: {goal_shadow['brier']:.3f}"
                if goal_shadow["brier"] is not None
                else "Brier: veri yok"
            ),
            f"Kapı: {'GEÇTİ' if goal_shadow['gate_passed'] else 'BEKLİYOR'}",
            "",
            (
                f"6+ Gol — {goals['candidates']} sonuç, "
                f"isabet %{goals['hit_rate']*100:.1f}, "
                f"ROI {goals['roi']*100:+.1f}%"
            ),
            "",
            "10:00 kuponları yalnız kendi mevcut kurallarıyla üretilecektir.",
        ]
    )
    return "\n".join(lines)


def push_model_status(
    db_path: str,
    telegram_client,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> str | None:
    """Send one model-health report before the daily coupon push."""
    now = now or datetime.now(tz=config.TIMEZONE)
    scheduled = now.replace(
        hour=config.MODEL_STATUS_PUSH_HOUR,
        minute=config.MODEL_STATUS_PUSH_MINUTE,
        second=0,
        microsecond=0,
    )
    deadline = now.replace(
        hour=config.DAILY_PUSH_HOUR + 1,
        minute=0,
        second=0,
        microsecond=0,
    )
    if not force and (now < scheduled or now >= deadline):
        return None
    today = now.strftime("%Y-%m-%d")
    with Database(db_path) as db:
        chat_id = db.get_setting("telegram_chat_id")
        if not chat_id:
            return None
        if not force and db.get_setting("last_model_status_date") == today:
            return None
    response = telegram_client.send_message(chat_id, model_status_text(db_path))
    delivered_chat_id, message_id, telegram_date = _telegram_delivery(
        response, chat_id
    )
    with Database(db_path) as db:
        db.record_telegram_delivery(
            kind="model_status",
            notification_dates=[today],
            chat_id=delivered_chat_id,
            message_id=message_id,
            telegram_date=telegram_date,
            sent_ts=int(now.timestamp()),
            markers={"last_model_status_date": today},
        )
    return chat_id


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
        last_lineup_backfill = db.get_setting("last_lineup_backfill_date")
    prematch_lineups = sum(
        item.lineup_available and not item.started for item in contexts
    )
    lineup_backfill = None
    today = now.strftime("%Y-%m-%d")
    if prematch_lineups and last_lineup_backfill != today:
        lineup_backfill = backfill_prior_lineups(
            db_path, client=client, now=now
        )
        with Database(db_path) as db:
            db.set_setting("last_lineup_backfill_date", today)
    return {
        "skipped": False,
        "events": len(events),
        "fixtures": len(fixtures),
        "matched": len(links),
        "match_rate": len(links) / len(events) if events else 0.0,
        "details": len(contexts),
        "lineups": sum(item.lineup_available for item in contexts),
        "prematch_lineups": prematch_lineups,
        "xg": sum(item.xg_home is not None for item in contexts),
        "lineup_backfill": lineup_backfill,
        "live_enabled": False,
        "errors": errors,
    }


def backfill_prior_lineups(
    db_path: str,
    *,
    client=None,
    now: datetime | None = None,
    per_team: int = 1,
    team_limit: int = 30,
) -> dict:
    """Fetch prior official lineups for teams with a current prematch lineup."""
    from .eligibility import is_daily_eligible
    from .fotmob import FotMobClient

    client = client or FotMobClient()
    now = now or datetime.now(tz=config.TIMEZONE)
    captured_ts = int(now.timestamp())
    with Database(db_path) as db:
        rows = db.conn.execute(
            """
            SELECT DISTINCT f.match_id, f.start_ts, f.home_id, f.away_id,
                COALESCE(c.name, '') AS competition
            FROM fotmob_fixtures f
            JOIN iddaa_fotmob_links l ON l.match_id=f.match_id
            JOIN events e ON e.id=l.event_id
            LEFT JOIN competitions c ON c.id=e.competition_id
            JOIN fotmob_context_captures x ON x.match_id=f.match_id
            WHERE x.lineup_available=1 AND x.started=0 AND f.start_ts>?
            ORDER BY f.start_ts
            """,
            (captured_ts,),
        ).fetchall()
    targets = []
    seen_teams = set()
    for row in rows:
        if not is_daily_eligible(row["competition"]):
            continue
        for team_id in (row["home_id"], row["away_id"]):
            if team_id and team_id not in seen_teams:
                targets.append((team_id, row["start_ts"]))
                seen_teams.add(team_id)
    targets = targets[:team_limit]

    prior_fixtures = {}
    contexts = []
    errors = []
    for team_id, before_ts in targets:
        try:
            candidates = [
                fixture
                for fixture in client.fetch_team_fixtures(team_id)
                if fixture.finished
                and fixture.start_ts < before_ts
                and is_daily_eligible(fixture.league_name)
            ]
            candidates.sort(key=lambda fixture: fixture.start_ts, reverse=True)
            for fixture in candidates[:per_team]:
                prior_fixtures[fixture.match_id] = fixture
        except Exception as exc:
            errors.append({"scope": f"team:{team_id}", "error": str(exc)})
    for fixture in prior_fixtures.values():
        try:
            contexts.append(
                client.fetch_context(fixture.match_id, captured_ts=captured_ts)
            )
        except Exception as exc:
            errors.append(
                {"scope": f"match:{fixture.match_id}", "error": str(exc)}
            )
    with Database(db_path) as db:
        db.save_fotmob_fixtures(prior_fixtures.values(), now=captured_ts)
        db.save_fotmob_contexts(contexts)
    return {
        "teams": len(targets),
        "fixtures": len(prior_fixtures),
        "lineups": sum(item.lineup_available for item in contexts),
        "xg": sum(item.xg_home is not None for item in contexts),
        "errors": errors,
    }


def backfill_understat_xg(
    db_path: str,
    *,
    seasons: list[int],
    leagues: tuple[str, ...] | None = None,
    client=None,
) -> dict:
    """Fetch bulk league xG and link it to existing historical results."""
    from .understat import (
        UNDERSTAT_LEAGUES,
        UnderstatClient,
        match_understat_history,
    )

    client = client or UnderstatClient()
    leagues = leagues or UNDERSTAT_LEAGUES
    fetched = []
    errors = []
    for season in seasons:
        for league in leagues:
            try:
                fetched.extend(client.fetch_league(league, season))
            except Exception as exc:
                errors.append(
                    {
                        "scope": f"{league}/{season}",
                        "error": str(exc),
                    }
                )
    with Database(db_path) as db:
        saved = db.save_understat_matches(fetched)
        xg_rows = db.load_understat_matches()
        history = db.load_historical_matches()
        links, diagnostics = match_understat_history(xg_rows, history)
        linked = db.save_historical_xg_links(links)
    return {
        "fetched": len(fetched),
        "saved": saved,
        "linked": linked,
        "coverage": linked / len(fetched) if fetched else 0.0,
        "unmatched": sum(
            item["method"] == "unmatched" for item in diagnostics
        ),
        "errors": errors,
    }


def auto_xg_sync(
    db_path: str,
    *,
    now: datetime | None = None,
    force: bool = False,
    client=None,
) -> dict:
    """Sync recent Understat seasons after the previous day is archived."""
    now = now or datetime.now(tz=config.TIMEZONE)
    now_ts = int(now.timestamp())
    today = now.strftime("%Y-%m-%d")
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    with Database(db_path) as db:
        if db.get_setting(f"history_archive:{yesterday}") is None:
            return {"skipped": True, "reason": "archive_not_ready", "errors": []}
        if not force and db.get_setting("last_xg_sync_date") == today:
            return {"skipped": True, "reason": "already_synced", "errors": []}
        last_poll = int(db.get_setting("last_xg_sync_poll_ts") or 0)
        if (
            not force
            and now_ts - last_poll < config.XG_SYNC_POLL_INTERVAL_SECONDS
        ):
            return {"skipped": True, "reason": "rate_limited", "errors": []}
        db.set_setting("last_xg_sync_poll_ts", str(now_ts))
    report = backfill_understat_xg(
        db_path,
        seasons=[now.year - 1, now.year],
        client=client,
    )
    report["skipped"] = False
    if not report["errors"]:
        with Database(db_path) as db:
            db.set_setting("last_xg_sync_date", today)
            db.set_setting("last_xg_sync_linked", str(report["linked"]))
    return report


def auto_model_refresh(
    db_path: str,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict:
    """Fit/cache daily goal and xG models after archive+xG synchronization."""
    from .model import GoalModel

    now = now or datetime.now(tz=config.TIMEZONE)
    now_ts = int(now.timestamp())
    today = now.strftime("%Y-%m-%d")
    with Database(db_path) as db:
        if not force and db.get_setting("last_xg_sync_date") != today:
            return {"skipped": True, "reason": "xg_not_ready"}
        existing = db.conn.execute(
            """
            SELECT model_version FROM model_training_runs
            WHERE run_date=? AND model_version IN (?, ?)
            """,
            (
                today,
                config.MODEL_SHADOW_VERSION,
                config.MODEL_GOAL_SHADOW_VERSION,
            ),
        ).fetchall()
        if len(existing) == 2 and not force:
            return {
                "skipped": True,
                "reason": "already_trained",
                "runs": [dict(row) for row in existing],
            }
        history = db.load_historical_matches(before_ts=now_ts)
    runs = []
    definitions = (
        (config.MODEL_GOAL_SHADOW_VERSION, False),
        (config.MODEL_SHADOW_VERSION, True),
    )
    for model_version, use_xg in definitions:
        model = GoalModel(history, now_ts, use_xg=use_xg)
        xg_matches = sum(
            row.get("xg_home") is not None
            and row.get("xg_away") is not None
            for row in model.history
        )
        payload = pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
        digest = hashlib.sha256(payload).hexdigest()
        features = [
            (
                today,
                model_version,
                team_key,
                venue,
                item["weight"],
                item["scored"],
                item["conceded"],
                item["n"],
                item["xg_n"],
                model.elo.get(team_key),
            )
            for (team_key, venue), item in model.stats.items()
        ]
        values = (
            today,
            model_version,
            now_ts,
            now_ts,
            len(model.history),
            xg_matches,
            len(model.stats),
            "ready",
        )
        with Database(db_path) as db:
            db.save_model_artifact(
                run_date=today,
                model_version=model_version,
                trained_ts=now_ts,
                cutoff_ts=now_ts,
                sha256=digest,
                payload=payload,
                features=features,
            )
            db.conn.execute(
                """
                INSERT INTO model_training_runs
                    (run_date, model_version, trained_ts, cutoff_ts,
                     history_matches, xg_matches, team_scopes, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_date, model_version) DO UPDATE SET
                    trained_ts=excluded.trained_ts,
                    cutoff_ts=excluded.cutoff_ts,
                    history_matches=excluded.history_matches,
                    xg_matches=excluded.xg_matches,
                    team_scopes=excluded.team_scopes,
                    status=excluded.status
                """,
                values,
            )
            db.set_setting("last_model_training_date", today)
            db.conn.commit()
        runs.append(
            {
                "run_date": today,
                "model_version": model_version,
                "trained_ts": now_ts,
                "history_matches": len(model.history),
                "xg_matches": xg_matches,
                "team_scopes": len(model.stats),
                "feature_rows": len(features),
                "artifact_sha256": digest,
                "status": "ready",
            }
        )
    xg_run = next(
        run for run in runs if run["model_version"] == config.MODEL_SHADOW_VERSION
    )
    return {
        "skipped": False,
        "run": xg_run,
        "runs": runs,
    }


def latest_model_training(db_path: str) -> dict | None:
    with Database(db_path) as db:
        row = db.conn.execute(
            """
            SELECT * FROM model_training_runs
            WHERE model_version=?
            ORDER BY trained_ts DESC LIMIT 1
            """,
            (config.MODEL_SHADOW_VERSION,),
        ).fetchone()
    return dict(row) if row else None


def lineup_risk_report(
    db_path: str,
    *,
    now: datetime | None = None,
    high_rotation_changes: int = 5,
) -> dict:
    """Report current lineup rotation without changing coupon selection."""
    from .eligibility import is_daily_eligible

    now = now or datetime.now(tz=config.TIMEZONE)
    now_ts = int(now.timestamp())
    items = []
    with Database(db_path) as db:
        rows = db.conn.execute(
            """
            SELECT f.match_id, f.home, f.away, f.start_ts,
                x.captured_ts, COALESCE(c.name, '') AS competition
            FROM fotmob_fixtures f
            JOIN iddaa_fotmob_links l ON l.match_id=f.match_id
            JOIN events e ON e.id=l.event_id
            LEFT JOIN competitions c ON c.id=e.competition_id
            JOIN fotmob_context_captures x ON x.match_id=f.match_id
            WHERE x.lineup_available=1 AND x.started=0 AND f.start_ts>?
              AND x.captured_ts=(
                  SELECT MAX(x2.captured_ts)
                  FROM fotmob_context_captures x2
                  WHERE x2.match_id=f.match_id AND x2.lineup_available=1
              )
            ORDER BY f.start_ts
            """,
            (now_ts,),
        ).fetchall()
        for row in rows:
            if not is_daily_eligible(row["competition"]):
                continue
            features = db.lineup_features(row["match_id"], row["captured_ts"])
            home_changes = features.get("home", {}).get("changes")
            away_changes = features.get("away", {}).get("changes")
            comparable = home_changes is not None and away_changes is not None
            items.append(
                {
                    "match_id": row["match_id"],
                    "home": row["home"],
                    "away": row["away"],
                    "start_ts": row["start_ts"],
                    "home_changes": home_changes,
                    "away_changes": away_changes,
                    "comparable": comparable,
                    "high_rotation": (
                        comparable
                        and max(home_changes, away_changes)
                        >= high_rotation_changes
                    ),
                }
            )
    return {
        "matches": len(items),
        "comparable": sum(item["comparable"] for item in items),
        "high_rotation": [item for item in items if item["high_rotation"]],
        "selection_enabled": False,
    }


def lineup_risk_text(db_path: str = config.DB_PATH) -> str:
    report = lineup_risk_report(db_path)
    if not report["matches"]:
        return "Henüz doğrulanmış maç önü kadrosu yok."
    lines = [
        "Kadro risk raporu",
        (
            f"{report['matches']} kadro · {report['comparable']} karşılaştırma · "
            f"{len(report['high_rotation'])} yüksek rotasyon"
        ),
    ]
    if not report["high_rotation"]:
        lines.append("Yüksek rotasyon tespit edilmedi.")
    for item in report["high_rotation"]:
        when = datetime.fromtimestamp(
            item["start_ts"], tz=config.TIMEZONE
        ).strftime("%H:%M")
        lines.append(
            f"⚠️ {when} {item['home']} - {item['away']}: "
            f"ev {item['home_changes']}, dep {item['away_changes']} değişiklik"
        )
    lines.append("Rapor modudur; kupon seçimini henüz değiştirmez.")
    return "\n".join(lines)
