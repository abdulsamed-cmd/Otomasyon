from datetime import datetime
import sqlite3

import pytest

from otomasyon import config, service
from otomasyon.storage import Database


class Telegram:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sent = []

    def send_message(self, chat_id, text):
        if self.fail:
            raise RuntimeError("telegram unavailable")
        self.sent.append((str(chat_id), text))
        return {
            "message_id": 700 + len(self.sent),
            "date": 1786258800,
            "chat": {"id": int(chat_id)},
        }


def _chat(path):
    with Database(path) as db:
        db.set_setting("telegram_chat_id", "123")


def _coupons(event_id: int = 1):
    """A real main/alt pair, with the main coupon's match swappable."""
    from dataclasses import replace

    from .test_engine import NOW, _events
    from otomasyon import engine

    built = engine.build_daily_coupons(_events(), now=NOW)
    main = built["main"]
    built["main"] = replace(
        main, legs=tuple(replace(leg, event_id=event_id) for leg in main.legs)
    )
    return built


COMPETITIONS = {1: {"name": "Test Lig", "country_code": "TR"}}


def _stub_build(monkeypatch, coupons, for_date="2026-08-09"):
    """Hand push_daily a prepared coupon set instead of the live bulletin."""
    from .test_engine import NOW, _events

    monkeypatch.setattr(service, "capture_shadow_predictions", lambda *a, **k: None)
    monkeypatch.setattr(
        service,
        "_build_daily",
        lambda _path: (coupons, _events(), COMPETITIONS, NOW, for_date),
    )


def test_daily_push_has_exact_local_time_gate_and_persists_receipt(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "daily.db")
    _chat(path)
    _stub_build(monkeypatch, _coupons())
    telegram = Telegram()

    before = datetime(2026, 8, 9, 9, 59, 59, tzinfo=config.TIMEZONE)
    assert service.push_daily(path, telegram, now=before) is None
    assert telegram.sent == []

    due = datetime(2026, 8, 9, 10, 0, 0, tzinfo=config.TIMEZONE)
    assert service.push_daily(path, telegram, now=due) == "123"
    with Database(path) as db:
        receipt = db.get_telegram_delivery_receipt("daily_push:2026-08-09")
        assert receipt == {
            "id": receipt["id"],
            "kind": "daily_push",
            "notification_date": "2026-08-09",
            "dedupe_key": "daily_push:2026-08-09",
            "chat_id": "123",
            "message_id": 701,
            "telegram_date": 1786258800,
            "sent_ts": int(due.timestamp()),
        }
        assert db.get_setting("last_push_date") == "2026-08-09"

    assert service.push_daily(path, telegram, now=due) is None
    assert len(telegram.sent) == 1


def _rebuild_stored_coupon(path, coupons, for_date="2026-08-09"):
    """Replace the day's coupon of record, as a rebuild does."""
    with Database(path) as db:
        db.supersede_daily_coupons(for_date, now=1)
        for coupon in coupons.values():
            if coupon:
                db.save_coupon(coupon, for_date)


def test_a_rebuilt_coupon_reaches_the_reader_who_is_holding_the_old_one(
    tmp_path, monkeypatch
):
    # The morning push is not the last word: a coupon rebuilt later in the day
    # retires the one already sent, and silence would leave it standing.
    path = str(tmp_path / "revised.db")
    _chat(path)
    telegram = Telegram()
    due = datetime(2026, 8, 9, 10, 0, tzinfo=config.TIMEZONE)

    _stub_build(monkeypatch, _coupons(event_id=1))
    assert service.push_daily(path, telegram, now=due) == "123"

    _rebuild_stored_coupon(path, _coupons(event_id=2))
    later = datetime(2026, 8, 9, 13, 0, tzinfo=config.TIMEZONE)
    assert service.push_daily(path, telegram, now=later) == "123"

    assert len(telegram.sent) == 2
    revision = telegram.sent[1][1]
    assert "KUPON GÜNCELLENDİ" in revision
    # It must describe the coupon now on record, not a fresh build.
    assert "PRIVATE" not in revision


def test_a_coupon_that_only_reprices_is_not_sent_again(tmp_path, monkeypatch):
    # The scheduler asks many times an hour and odds never stop moving, so
    # only a changed instruction is worth interrupting the reader for.
    path = str(tmp_path / "same.db")
    _chat(path)
    telegram = Telegram()
    due = datetime(2026, 8, 9, 10, 0, tzinfo=config.TIMEZONE)

    _stub_build(monkeypatch, _coupons(event_id=1))
    assert service.push_daily(path, telegram, now=due) == "123"

    # A rebuild at new prices, same selections.
    repriced = _coupons(event_id=1)
    for coupon in repriced.values():
        for leg in coupon.legs:
            leg.odd = round(leg.odd + 0.05, 2)
    _rebuild_stored_coupon(path, repriced)

    later = datetime(2026, 8, 9, 15, 30, tzinfo=config.TIMEZONE)
    _stub_build(monkeypatch, _coupons(event_id=77))
    assert service.push_daily(path, telegram, now=later) is None
    assert len(telegram.sent) == 1


def test_a_coupon_that_has_been_settled_is_not_announced_as_replaced(
    tmp_path, monkeypatch
):
    # Winning is not a way of ceasing to have been today's coupon. Dropping a
    # settled coupon from the record would leave the day looking half-empty,
    # and the reader would be told their winning slip had been retired.
    path = str(tmp_path / "settled.db")
    _chat(path)
    telegram = Telegram()
    due = datetime(2026, 8, 9, 10, 0, tzinfo=config.TIMEZONE)

    _stub_build(monkeypatch, _coupons(event_id=1))
    assert service.push_daily(path, telegram, now=due) == "123"

    with Database(path) as db:
        db.conn.execute(
            "UPDATE coupons SET status='won' WHERE kind='daily_main' AND for_date=?",
            ("2026-08-09",),
        )
        db.conn.commit()

    record = service.coupons_of_record(path, "2026-08-09")
    assert record["main"] is not None

    later = datetime(2026, 8, 9, 18, 30, tzinfo=config.TIMEZONE)
    assert service.push_daily(path, telegram, now=later) is None
    assert len(telegram.sent) == 1


def test_a_revision_keeps_its_own_receipt_alongside_the_morning_push(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "receipts.db")
    _chat(path)
    telegram = Telegram()
    due = datetime(2026, 8, 9, 10, 0, tzinfo=config.TIMEZONE)

    _stub_build(monkeypatch, _coupons(event_id=1))
    service.push_daily(path, telegram, now=due)
    _rebuild_stored_coupon(path, _coupons(event_id=2))
    service.push_daily(path, telegram, now=due)

    with Database(path) as db:
        rows = db.conn.execute(
            "SELECT dedupe_key FROM telegram_delivery_receipts "
            "WHERE kind='daily_push' ORDER BY id"
        ).fetchall()
    keys = [row["dedupe_key"] for row in rows]
    assert keys[0] == "daily_push:2026-08-09"
    assert len(keys) == 2 and keys[1] != keys[0]


def test_force_bypasses_daily_time_gate(tmp_path, monkeypatch):
    path = str(tmp_path / "forced.db")
    _chat(path)
    _stub_build(monkeypatch, _coupons())

    early = datetime(2026, 8, 9, 8, 0, tzinfo=config.TIMEZONE)
    assert service.push_daily(path, Telegram(), force=True, now=early) == "123"


def test_failed_daily_send_has_no_receipt_or_marker(tmp_path, monkeypatch):
    path = str(tmp_path / "failed.db")
    _chat(path)
    _stub_build(monkeypatch, _coupons())
    due = datetime(2026, 8, 9, 10, 0, tzinfo=config.TIMEZONE)

    with pytest.raises(RuntimeError, match="telegram unavailable"):
        service.push_daily(path, Telegram(fail=True), now=due)

    with Database(path) as db:
        assert db.get_telegram_delivery_receipt("daily_push:2026-08-09") is None
        assert db.get_setting("last_push_date") is None


def test_missing_send_acknowledgement_has_no_receipt_or_marker(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "missing-ack.db")
    _chat(path)
    _stub_build(monkeypatch, _coupons())

    class MissingAcknowledgement:
        def send_message(self, chat_id, text):
            return None

    due = datetime(2026, 8, 9, 10, 0, tzinfo=config.TIMEZONE)
    with pytest.raises(RuntimeError, match="no delivery receipt"):
        service.push_daily(path, MissingAcknowledgement(), now=due)

    with Database(path) as db:
        assert db.get_telegram_delivery_receipt("daily_push:2026-08-09") is None
        assert db.get_setting("last_push_date") is None


def test_model_and_archive_receipts_preserve_existing_dedupe(tmp_path):
    path = str(tmp_path / "scheduled.db")
    _chat(path)
    telegram = Telegram()
    due = datetime(2026, 8, 9, 9, 45, tzinfo=config.TIMEZONE)

    assert service.push_model_status(path, telegram, now=due) == "123"
    assert service.push_model_status(path, telegram, now=due) is None

    with Database(path) as db:
        db.set_setting("history_archive:2026-08-08", "4")
    archive_now = datetime(2026, 8, 9, 5, 0, tzinfo=config.TIMEZONE)
    assert service.notify_completed_history_archives(
        path, telegram, now=archive_now
    ) == ["2026-08-08"]
    assert service.notify_completed_history_archives(
        path, telegram, now=archive_now
    ) == []

    with Database(path) as db:
        model = db.get_telegram_delivery_receipt("model_status:2026-08-09")
        archive = db.get_telegram_delivery_receipt("history_archive:2026-08-08")
        assert model["kind"] == "model_status"
        assert model["notification_date"] == "2026-08-09"
        assert model["message_id"] == 701
        assert model["sent_ts"] == int(due.timestamp())
        assert archive["kind"] == "history_archive"
        assert archive["notification_date"] == "2026-08-08"
        assert archive["message_id"] == 702
        assert archive["telegram_date"] == 1786258800


def test_failed_model_status_send_has_no_receipt_or_marker(tmp_path):
    path = str(tmp_path / "failed-model.db")
    _chat(path)
    due = datetime(2026, 8, 9, 9, 45, tzinfo=config.TIMEZONE)

    with pytest.raises(RuntimeError, match="telegram unavailable"):
        service.push_model_status(path, Telegram(fail=True), now=due)

    with Database(path) as db:
        assert db.get_telegram_delivery_receipt("model_status:2026-08-09") is None
        assert db.get_setting("last_model_status_date") is None


def test_legacy_receipt_schema_is_migrated_and_remains_deduplicated(tmp_path):
    path = tmp_path / "legacy-receipts.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE telegram_delivery_receipts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                kind          TEXT NOT NULL,
                dedupe_key    TEXT NOT NULL UNIQUE,
                chat_id       TEXT NOT NULL,
                message_id    INTEGER NOT NULL,
                telegram_date INTEGER,
                sent_ts       INTEGER NOT NULL
            );
            INSERT INTO telegram_delivery_receipts
                (kind, dedupe_key, chat_id, message_id, telegram_date, sent_ts)
            VALUES
                ('daily_push', 'daily_push:2026-08-08', '123', 700,
                 1786172400, 1786172400);
            """
        )

    with Database(path) as db:
        existing = db.get_telegram_delivery_receipt(
            "daily_push:2026-08-08"
        )
        assert existing["notification_date"] == "2026-08-08"

        db.record_telegram_delivery(
            kind="daily_push",
            notification_dates=["2026-08-09"],
            chat_id="123",
            message_id=701,
            telegram_date=1786258800,
            sent_ts=1786258800,
            markers={},
        )
        recorded = db.get_telegram_delivery_receipt(
            "daily_push:2026-08-09"
        )
        assert recorded["notification_date"] == "2026-08-09"
        with pytest.raises(sqlite3.IntegrityError):
            db.record_telegram_delivery(
                kind="daily_push",
                notification_dates=["2026-08-09"],
                chat_id="123",
                message_id=702,
                telegram_date=1786258801,
                sent_ts=1786258801,
                markers={},
            )

    with Database(path) as db:
        assert (
            db.get_telegram_delivery_receipt(
                "daily_push:2026-08-08"
            )["notification_date"]
            == "2026-08-08"
        )
        assert (
            db.get_telegram_delivery_receipt(
                "daily_push:2026-08-09"
            )["message_id"]
            == 701
        )


def _bot_runtime(path, *, last_poll_ts, owner="watch"):
    with Database(path) as db:
        db.acquire_telegram_bot_lease(owner, last_poll_ts, 45)
        db.record_telegram_poll(owner, last_poll_ts, ok=True)


def test_watchdog_reports_a_silent_bot_once_and_then_its_recovery(tmp_path):
    """A dead bot cannot report itself, so the scheduler announces the silence."""
    path = str(tmp_path / "watchdog.db")
    _chat(path)
    _bot_runtime(path, last_poll_ts=1_000)
    telegram = Telegram()

    fresh = 1_000 + config.TELEGRAM_WATCHDOG_STALE_SECONDS
    assert service.check_bot_liveness(path, telegram, now=fresh) is None
    assert telegram.sent == []

    stale = fresh + 1
    assert service.check_bot_liveness(path, telegram, now=stale) == "alerted"
    assert "yanıt vermiyor" in telegram.sent[0][1]

    # The same outage must not be announced again on every scheduler cycle.
    assert service.check_bot_liveness(path, telegram, now=stale + 600) is None
    assert len(telegram.sent) == 1

    with Database(path) as db:
        db.record_telegram_poll("watch", stale + 700, ok=True)
    assert (
        service.check_bot_liveness(path, telegram, now=stale + 700) == "recovered"
    )
    assert "tekrar çalışıyor" in telegram.sent[1][1]
    assert service.check_bot_liveness(path, telegram, now=stale + 800) is None
    assert len(telegram.sent) == 2


def test_watchdog_is_silent_before_the_user_has_a_chat(tmp_path):
    path = str(tmp_path / "no-chat.db")
    _bot_runtime(path, last_poll_ts=1_000)
    telegram = Telegram()

    assert service.check_bot_liveness(path, telegram, now=999_999) is None
    assert telegram.sent == []
