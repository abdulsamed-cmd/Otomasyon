import copy

from otomasyon import engine
from otomasyon.storage import Database

from .test_engine import NOW, _events


def test_closing_odds_use_last_snapshot_before_kickoff():
    events = _events()
    coupon = engine.build_daily_coupons(events, now=NOW)["main"]
    with Database(":memory:") as db:
        db.upsert_competitions(
            {1: {"name": "Test Lig", "country_code": "TR"}}
        )
        db.save_events(events, now=int(NOW.timestamp()))
        coupon_id = db.save_coupon(coupon, "2026-08-08", now=int(NOW.timestamp()))
        assert (
            db.save_coupon(coupon, "2026-08-08", now=int(NOW.timestamp()) + 1)
            == coupon_id
        )
        assert db.count("coupons") == 1

        changed = copy.deepcopy(events)
        selected = {leg.event_id: leg.outcome_no for leg in coupon.legs}
        expected = {}
        for event in changed:
            if event.event_id not in selected:
                continue
            for selection in event.markets[0].selections:
                if selection.outcome_no == selected[event.event_id]:
                    selection.odd += 0.05
                    expected[event.event_id] = selection.odd
        db.save_events(changed, now=int(NOW.timestamp()) + 1800)
        assert db.populate_closing_odds(coupon_id) == len(coupon.legs)
        rows = db.conn.execute(
            """
            SELECT event_id, closing_odd FROM coupon_legs
            WHERE coupon_id=?
            """,
            (coupon_id,),
        ).fetchall()
    assert {row["event_id"]: row["closing_odd"] for row in rows} == expected
