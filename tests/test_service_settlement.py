from otomasyon import config, service
from otomasyon.engine import Coupon, Leg
from otomasyon.iddaa.normalize import NormalizedEvent
from otomasyon.settlement import MatchResult
from otomasyon.storage import Database
from otomasyon.results.mackolik import SourceMatch


def _event(eid, home, away):
    return NormalizedEvent(
        event_id=eid, home=home, away=away, competition_id=-1,
        competition_name="Lig", country_code="TR", sport_id=1,
        start_ts=1786197600, status=0, markets=[],
    )


def _leg(eid, home, away, code, outcome_no, outcome, odd, sov=None):
    return Leg(
        event_id=eid, home=home, away=away, competition="Lig", start_ts=1786197600,
        market_code=code, market_name="M", sov=sov, outcome_no=outcome_no,
        outcome_name=outcome, odd=odd, fair_prob=0.6,
    )


def _seed(path):
    db = Database(path)
    db.save_events([_event(1, "A", "B"), _event(2, "C", "D")])
    coupon = Coupon(
        kind="daily_main",
        legs=[
            _leg(1, "A", "B", config.MARKET_OVER_UNDER, 1, "Alt", 1.50, "3.5"),
            _leg(2, "C", "D", config.MARKET_DOUBLE_CHANCE, 1, "1 ve 0", 1.40),
        ],
    )
    db.save_coupon(coupon, "2026-08-08")
    db.close()


def test_settle_pending_marks_won_and_metrics(tmp_path):
    path = str(tmp_path / "t.db")
    _seed(path)

    # Leg 1: total 2 < 3.5 -> Alt wins. Leg 2: A wins 2-0 -> 1X wins.
    service.record_result(path, MatchResult(1, 1, 1))
    service.record_result(path, MatchResult(2, 2, 0))

    decided = service.settle_pending(path, client=None, notify=False)
    assert len(decided) == 1
    assert decided[0]["settlement"].status == "won"

    # No longer pending on a second pass.
    assert service.settle_pending(path, notify=False) == []

    m = service.metrics(path)
    assert m["coupons_played"] == 1
    assert m["won"] == 1
    assert m["hit_rate"] == 1.0
    assert round(m["avg_odds"], 2) == 2.10
    split = service.metrics_by_kind(path)
    assert split["daily_main"]["coupons"] == 1
    assert split["daily_main"]["gate_passed"] is False
    assert split["daily_alt"]["coupons"] == 0
    assert round(m["roi"], 2) == 1.10


def test_settle_pending_marks_lost(tmp_path):
    path = str(tmp_path / "t.db")
    _seed(path)
    service.record_result(path, MatchResult(1, 3, 3))  # total 6 -> Alt 3.5 loses
    service.record_result(path, MatchResult(2, 2, 0))
    decided = service.settle_pending(path, notify=False)
    assert decided[0]["settlement"].status == "lost"
    m = service.metrics(path)
    assert m["won"] == 0 and m["roi"] == -1.0


def test_legacy_ineligible_coupons_are_excluded_from_metrics(tmp_path):
    path = str(tmp_path / "t.db")
    _seed(path)
    service.record_result(path, MatchResult(1, 1, 1))
    service.record_result(path, MatchResult(2, 2, 0))
    service.settle_pending(path, notify=False)
    with Database(path) as db:
        db.conn.execute(
            "UPDATE coupons SET notes='legacy_ineligible'"
        )
        db.conn.commit()
    assert service.metrics(path)["coupons_played"] == 0
    assert service.metrics_by_kind(path)["daily_main"]["coupons"] == 0


class FakeResultClient:
    def __init__(self):
        self.days = []

    def fetch_date(self, day):
        self.days.append(day)
        return [
            SourceMatch(
                "mk1", 1, "A", "B", 1786197600, "post", "fullTime",
                1, 1, 0, 0,
            ),
            SourceMatch(
                "mk2", 2, "C", "D", 1786197600, "post", "fullTime",
                2, 0, 1, 0,
            ),
        ]


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((str(chat_id), text))
        return {
            "message_id": 300 + len(self.sent),
            "date": 1786258800,
            "chat": {"id": int(chat_id)},
        }


def test_auto_results_fetches_exact_ids_settles_and_notifies(tmp_path):
    path = str(tmp_path / "t.db")
    _seed(path)
    with Database(path) as db:
        db.set_setting("telegram_chat_id", "42")

    source = FakeResultClient()
    telegram = FakeTelegram()
    report = service.auto_results(
        path, telegram, force=True, result_client=source
    )

    assert report["matched"] == 2
    assert report["settled"] == 1
    assert all(d["method"] == "iddaa_code" for d in report["diagnostics"])
    assert len(telegram.sent) == 1
    assert telegram.sent[0][0] == "42"
    assert "KAZANDI" in telegram.sent[0][1]
    with Database(path) as db:
        receipt = db.get_telegram_delivery_receipt("coupon_result:1")
        assert receipt["kind"] == "coupon_result"
        assert receipt["chat_id"] == "42"
        assert receipt["message_id"] == 301
        assert receipt["telegram_date"] == 1786258800
        assert db.get_setting("coupon_result_notified:1") is not None

    # Persistent rate limit prevents another network request.
    report2 = service.auto_results(path, telegram, result_client=source)
    assert report2["skipped"] and report2["reason"] == "rate_limited"


def test_failed_coupon_notification_has_no_receipt_or_marker(tmp_path):
    path = str(tmp_path / "failed-notification.db")
    _seed(path)
    service.record_result(path, MatchResult(1, 1, 1))
    service.record_result(path, MatchResult(2, 2, 0))
    with Database(path) as db:
        db.set_setting("telegram_chat_id", "42")

    class FailingTelegram:
        def send_message(self, chat_id, text):
            raise RuntimeError("telegram unavailable")

    import pytest

    with pytest.raises(RuntimeError, match="telegram unavailable"):
        service.settle_pending(path, FailingTelegram())
    with Database(path) as db:
        assert db.get_telegram_delivery_receipt("coupon_result:1") is None
        assert db.get_setting("coupon_result_notified:1") is None


def test_failed_result_fetch_does_not_start_rate_limit(tmp_path):
    path = str(tmp_path / "t.db")
    _seed(path)

    class Failing:
        def fetch_date(self, day):
            raise RuntimeError("temporary failure")

    import pytest

    with pytest.raises(RuntimeError, match="temporary"):
        service.auto_results(path, force=True, result_client=Failing())
    with Database(path) as db:
        assert db.get_setting("last_result_poll_ts") is None
