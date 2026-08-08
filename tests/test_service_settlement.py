from otomasyon import config, service
from otomasyon.engine import Coupon, Leg
from otomasyon.iddaa.normalize import NormalizedEvent
from otomasyon.settlement import MatchResult
from otomasyon.storage import Database


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
