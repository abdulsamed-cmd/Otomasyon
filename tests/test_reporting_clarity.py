"""How performance is reported.

Two reporting faults made a correct database look wrong: the per-kind line
showed only how many coupons had settled, so two winning coupons read as "1",
and a single settled bet was printed with a zero-width confidence interval,
which claims a precision the data cannot support.
"""

from otomasyon import config, probability, service
from otomasyon.engine import Coupon, Leg
from otomasyon.iddaa.normalize import NormalizedEvent
from otomasyon.settlement import MatchResult
from otomasyon.storage import Database


def _event(eid, start_ts=1_786_197_600):
    return NormalizedEvent(
        event_id=eid,
        home=f"Ev{eid}",
        away=f"Dep{eid}",
        competition_id=-1,
        competition_name="Lig",
        country_code="TR",
        sport_id=1,
        start_ts=start_ts,
        status=0,
        markets=[],
    )


def _coupon(kind, eid):
    return Coupon(
        kind=kind,
        legs=[
            Leg(
                event_id=eid,
                home=f"Ev{eid}",
                away=f"Dep{eid}",
                competition="Lig",
                start_ts=1_786_197_600,
                market_code=config.MARKET_OVER_UNDER,
                market_name="Alt/Üst",
                sov="3.5",
                outcome_no=1,
                outcome_name="Alt",
                odd=2.0,
                fair_prob=0.55,
            )
        ],
    )


def _settled_day(path, for_date, *, main_goals, alt_goals):
    """One main and one alternative coupon, both resolved for a date."""
    with Database(path) as db:
        db.save_events([_event(1), _event(2)])
        db.save_coupon(_coupon("daily_main", 1), for_date)
        db.save_coupon(_coupon("daily_alt", 2), for_date)
        db.save_result(MatchResult(1, *main_goals))
        db.save_result(MatchResult(2, *alt_goals))
    service.settle_pending(path, None, notify=False)


def test_both_winning_coupons_are_reported_as_winning(tmp_path):
    """Two wins must not read as one; the line states wins and losses."""
    path = str(tmp_path / "wins.db")
    _settled_day(path, "2026-08-09", main_goals=(1, 0), alt_goals=(0, 0))

    split = service.metrics_by_kind(path)
    assert split["daily_main"]["won"] == 1
    assert split["daily_main"]["lost"] == 0
    assert split["daily_alt"]["won"] == 1

    text = service.model_status_text(path)
    assert "Ana kupon — 1 tuttu / 0 tutmadı" in text
    assert "Alternatif — 1 tuttu / 0 tutmadı" in text


def test_a_single_result_does_not_claim_a_precise_interval(tmp_path):
    """One settled bet cannot produce a zero-width confidence interval."""
    path = str(tmp_path / "single.db")
    _settled_day(path, "2026-08-09", main_goals=(1, 0), alt_goals=(0, 0))

    item = service.metrics_by_kind(path)["daily_main"]
    assert item["coupons"] == 1
    assert item["roi_ci95"] is None
    assert item["gate_passed"] is False

    text = service.model_status_text(path)
    assert "aralık için veri yetersiz" in text
    assert "[95% +100.0%..+100.0%]" not in text


def test_pending_coupons_explain_why_the_numbers_did_not_move(tmp_path):
    """An unchanged report should say what it is still waiting on."""
    path = str(tmp_path / "pending.db")
    _settled_day(path, "2026-08-09", main_goals=(1, 0), alt_goals=(0, 0))
    with Database(path) as db:
        db.save_events([_event(3)])
        db.save_coupon(_coupon("daily_main", 3), "2026-08-10")

    summary = service.pending_coupon_summary(path)
    assert summary["coupons"] == 1
    assert summary["dates"] == ["2026-08-10"]
    assert "1 kupon henüz sonuçlanmadı" in service.model_status_text(path)


def test_two_results_do_produce_an_interval():
    roi, interval = probability.roi_interval([1.0, -1.0])

    assert roi == 0.0
    assert interval is not None
    assert interval[0] < roi < interval[1]


def test_identical_results_do_not_pass_as_a_precise_interval():
    """Three identical losses have no spread to estimate uncertainty from."""
    roi, interval = probability.roi_interval([-1.0, -1.0, -1.0])

    assert roi == -1.0
    assert interval is None


def test_interval_helpers_state_when_data_is_insufficient():
    assert probability.roi_interval([])[1] is None
    assert probability.roi_interval([0.5])[1] is None
    assert "veri yetersiz" in probability.roi_text(1.0, None)
    assert "veri yetersiz" in probability.interval_text(None)
