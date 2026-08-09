"""Durability of result notifications.

A coupon can only be settled once. When delivery was attempted inline, a failed
send left the coupon decided in the database with its result never announced
and no way to retry. These tests pin the queue that separates "decided" from
"delivered".
"""

import pytest

from otomasyon import config, service
from otomasyon.engine import Coupon, Leg
from otomasyon.iddaa.normalize import NormalizedEvent
from otomasyon.settlement import MatchResult
from otomasyon.storage import Database

TODAY = "2026-08-09"


class Telegram:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((str(chat_id), text))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
        return {
            "message_id": 800 + len(self.sent),
            "date": 1_786_258_800,
            "chat": {"id": int(chat_id)},
        }


def _status(db, coupon_id):
    return db.conn.execute(
        "SELECT status FROM coupons WHERE id=?", (coupon_id,)
    ).fetchone()["status"]


def _event(eid=901):
    return NormalizedEvent(
        event_id=eid,
        home="Ev Sahibi",
        away="Deplasman",
        competition_id=-1,
        competition_name="Lig",
        country_code="TR",
        sport_id=1,
        start_ts=1_786_197_600,
        status=0,
        markets=[],
    )


def _coupon_with_result(path, for_date=TODAY):
    """A decided-ready coupon: one leg whose match result is already stored."""
    with Database(path) as db:
        db.save_events([_event()])
        db.set_setting("telegram_chat_id", "555")
        coupon_id = db.save_coupon(
            Coupon(
                kind="daily_alt",
                legs=[
                    Leg(
                        event_id=901,
                        home="Ev Sahibi",
                        away="Deplasman",
                        competition="Lig",
                        start_ts=1_786_197_600,
                        market_code=config.MARKET_OVER_UNDER,
                        market_name="Alt/Üst",
                        sov="3.5",
                        outcome_no=1,
                        outcome_name="Alt",
                        odd=1.5,
                        fair_prob=0.66,
                    )
                ],
            ),
            for_date,
        )
        # Total 1 goal is under 3.5, so the coupon wins.
        db.save_result(MatchResult(901, 1, 0))
    return coupon_id


def test_failed_delivery_keeps_the_result_queued_for_retry(tmp_path):
    """The bug: a send failure used to lose the result permanently."""
    path = str(tmp_path / "settle.db")
    coupon_id = _coupon_with_result(path)
    telegram = Telegram([RuntimeError("connection aborted")])

    decided = service.settle_pending(path, telegram)
    assert len(decided) == 1

    with Database(path) as db:
        assert _status(db, coupon_id) == "won"
        queued = db.notification(service.settlement_dedupe_key(coupon_id))
        assert queued["status"] == "retry_wait"
        assert queued["sent_ts"] is None

    # The result is still owed, and the next drain delivers it.
    healthy = Telegram()
    report = service.drain_notifications(
        path, healthy, now=queued["next_attempt_ts"]
    )
    assert report["sent"] == 1
    assert "KUPON SONUCU" in healthy.sent[0][1]
    with Database(path) as db:
        final = db.notification(service.settlement_dedupe_key(coupon_id))
        assert final["status"] == "sent"
        assert final["message_id"] == 801


def test_a_delivered_result_is_never_announced_twice(tmp_path):
    path = str(tmp_path / "once.db")
    coupon_id = _coupon_with_result(path)
    telegram = Telegram()

    service.settle_pending(path, telegram)
    assert len(telegram.sent) == 1

    service.deliver_pending_notifications(path, telegram)
    service.drain_notifications(path, telegram)
    assert len(telegram.sent) == 1

    with Database(path) as db:
        assert db.notification(
            service.settlement_dedupe_key(coupon_id)
        )["status"] == "sent"


def test_results_decided_before_the_queue_existed_are_reconciled(tmp_path):
    """The alternative coupon that won without any notification must be recovered."""
    path = str(tmp_path / "reconcile.db")
    coupon_id = _coupon_with_result(path)

    # Settle without a client, exactly as a delivery-less settlement leaves it.
    service.settle_pending(path, None, notify=False)
    with Database(path) as db:
        assert _status(db, coupon_id) == "won"
        assert db.notification(service.settlement_dedupe_key(coupon_id)) is None

    telegram = Telegram()
    report = service.deliver_pending_notifications(path, telegram)

    assert report["sent"] == 1
    assert "KUPON SONUCU" in telegram.sent[0][1]


def test_reconciliation_does_not_replay_older_days(tmp_path):
    """Recovering a missed result must not dump history into the chat."""
    path = str(tmp_path / "bounded.db")
    _coupon_with_result(path)
    service.settle_pending(path, None, notify=False)

    telegram = Telegram()
    queued = service.queue_missing_settlement_notifications(
        path, for_dates=["2020-01-01"]
    )

    assert queued == 0
    assert service.drain_notifications(path, telegram)["sent"] == 0
    assert telegram.sent == []


def test_delivery_gives_up_after_a_bounded_number_of_attempts(tmp_path):
    path = str(tmp_path / "giveup.db")
    coupon_id = _coupon_with_result(path)
    service.settle_pending(path, None, notify=False)
    service.queue_missing_settlement_notifications(path)

    telegram = Telegram([RuntimeError("down")] * 50)
    now = 1_786_300_000
    for _ in range(config.NOTIFICATION_MAX_ATTEMPTS + 1):
        now += config.NOTIFICATION_RETRY_MAX_SECONDS
        service.drain_notifications(path, telegram, now=now)

    with Database(path) as db:
        row = db.notification(service.settlement_dedupe_key(coupon_id))
    assert row["status"] == "failed"
    assert row["attempts"] == config.NOTIFICATION_MAX_ATTEMPTS


def test_a_restart_mid_send_requeues_the_notification(tmp_path):
    path = str(tmp_path / "restart.db")
    coupon_id = _coupon_with_result(path)
    service.settle_pending(path, None, notify=False)
    service.queue_missing_settlement_notifications(path)

    later = 2_000_000_000
    with Database(path) as db:
        row = db.due_notifications(later)[0]
        db.begin_notification_send(row["id"], later)
        assert db.notification(
            service.settlement_dedupe_key(coupon_id)
        )["status"] == "sending"

    telegram = Telegram()
    assert service.drain_notifications(path, telegram)["sent"] == 1
