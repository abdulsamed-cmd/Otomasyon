"""How performance is reported.

Two reporting faults made a correct database look wrong: the per-kind line
showed only how many coupons had settled, so two winning coupons read as "1",
and a single settled bet was printed with a zero-width confidence interval,
which claims a precision the data cannot support.
"""

import json

from datetime import datetime

from otomasyon import config, engine, formatting, probability, service
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
    assert "Ana kupon — kupon: 1 tuttu / 0 tutmadı" in text
    assert "Alternatif — kupon: 1 tuttu / 0 tutmadı" in text


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


def test_evidence_is_counted_in_matches_not_coupons():
    """The 200 target is match predictions, so it grows several times a day.

    Counting coupons meant one data point per day and roughly 200 days before
    any conclusion. Each leg is a separate prediction and counts as one.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/matches.db"
        with Database(path) as db:
            db.save_events([_event(1), _event(2)])
            coupon = Coupon(
                kind="daily_main",
                legs=[_coupon("daily_main", 1).legs[0], _leg_for(2)],
            )
            db.save_coupon(coupon, "2026-08-09")
            db.save_result(MatchResult(1, 1, 0))
            db.save_result(MatchResult(2, 5, 4))
        service.settle_pending(path, None, notify=False)

        item = service.metrics_by_kind(path)["daily_main"]

        # One coupon, but two match predictions: one right, one wrong.
        assert item["coupons"] == 1
        assert item["matches"] == 2
        assert item["match_hits"] == 1
        assert item["minimum_matches"] == config.PERFORMANCE_GATE_MIN_MATCHES[
            "daily_main"
        ]
        assert "Maç tahmini: 1/2 doğru" in service.model_status_text(path)


def _leg_for(eid):
    return Leg(
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


def test_report_states_whether_the_model_picks_the_coupons():
    """Training alone reads as if the model were already choosing selections."""
    text = service.model_influence_text()

    assert "MODEL HENÜZ KULLANILMIYOR" in text
    assert "oranlarından türetilen adil olasılıkla" in text


def test_excluded_legacy_coupons_are_stated_not_hidden(tmp_path):
    """Hidden exclusions make the totals look like a counting error."""
    path = str(tmp_path / "legacy.db")
    _settled_day(path, "2026-08-08", main_goals=(1, 0), alt_goals=(0, 0))
    with Database(path) as db:
        db.conn.execute("UPDATE coupons SET notes='legacy_ineligible'")
        db.conn.commit()

    summary = service.excluded_coupon_summary(path)
    assert summary["coupons"] == 2
    assert summary["won"] == 2
    assert "2 eski kupon (2 tutan) sayıma girmiyor" in service.model_status_text(
        path
    )


def test_report_states_that_the_model_earns_no_weight_yet(tmp_path):
    """"Trained" and "used" are different claims; the layer decides which."""
    path = str(tmp_path / "calib.db")
    with Database(path) as db:
        db.set_setting(
            "model_calibration",
            json.dumps(
                {
                    "model_earns_weight": False,
                    "markets": {
                        "ou25": {
                            "holdout": {
                                "samples": 9094,
                                "intercept": 0.0,
                                "market_weight": 1.11,
                                "model_weight": -0.005,
                                "market_logloss": 0.6763,
                                "model_logloss": 0.6831,
                                "pooled_logloss": 0.6763,
                                "model_contribution": 0.000009,
                            }
                        }
                    },
                }
            ),
        )
        db.conn.commit()

    text = service.calibration_text(path)
    assert "ölçülebilir bilgi eklemiyor" in text
    assert "model ağırlığı -0.01" in text
    assert "MODEL HENÜZ KULLANILMIYOR" in service.model_influence_text(path)


def test_report_says_the_model_is_in_use_once_it_earns_weight(tmp_path):
    path = str(tmp_path / "earned.db")
    with Database(path) as db:
        db.set_setting(
            "model_calibration",
            json.dumps(
                {
                    "model_earns_weight": True,
                    "markets": {
                        "ou25": {
                            "holdout": {
                                "samples": 9094,
                                "intercept": 0.0,
                                "market_weight": 0.7,
                                "model_weight": 0.55,
                                "market_logloss": 0.676,
                                "model_logloss": 0.669,
                                "pooled_logloss": 0.665,
                                "model_contribution": 0.011,
                            }
                        }
                    },
                }
            ),
        )
        db.conn.commit()

    assert "ağırlığı kazandı" in service.calibration_text(path)
    assert service.model_influence_text(path) == "Kupon seçimi: MODEL kullanılıyor"


def _coupon_at(prob: float, odd: float):
    leg = engine.Leg(
        event_id=1,
        home="Ev",
        away="Deplasman",
        competition="Test Lig",
        start_ts=int(datetime(2026, 8, 11, 21, tzinfo=config.TIMEZONE).timestamp()),
        market_code=config.MARKET_BTTS,
        market_name="Karşılıklı Gol",
        sov=None,
        outcome_no=2,
        outcome_name="Yok",
        odd=odd,
        fair_prob=prob,
    )
    return engine.Coupon(kind="daily_main", legs=[leg])


def test_a_leg_is_labelled_as_the_markets_own_view_not_our_forecast():
    text = formatting.format_coupon("ANA KUPON", _coupon_at(0.457, 1.85))
    assert "piyasa %46 veriyor" in text
    assert "adil" not in text


def test_an_underdog_coupon_says_it_is_not_beating_the_market():
    text = formatting.format_daily(
        {"main": _coupon_at(0.457, 1.85), "alt": None}, "2026-08-11"
    )
    assert "piyasayı yenme iddiası taşımaz" in text
    assert "daha az ihtimal verdiği taraftadır" in text


def test_a_favourite_coupon_drops_the_underdog_warning():
    text = formatting.format_daily(
        {"main": _coupon_at(0.606, 1.40), "alt": None}, "2026-08-11"
    )
    assert "daha az ihtimal verdiği taraftadır" not in text
