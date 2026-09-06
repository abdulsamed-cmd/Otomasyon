"""The mixed coupon's place in the day.

The engine is tested separately for whether the coupon it picks is the right
one. What is checked here is that a third coupon is a full member of the day:
it is stored, read back, shown, retired, followed up, settled and measured on
its own, rather than being built and then quietly dropped by one of the many
places that used to know a day had exactly two coupons.
"""

from datetime import datetime

from otomasyon import config, formatting, service
from otomasyon.engine import Coupon, Leg
from otomasyon.iddaa.normalize import NormalizedEvent
from otomasyon.settlement import MatchResult
from otomasyon.storage import Database

COMPETITIONS = {-1: {"name": "Lig", "country_code": "TR"}}
FOR_DATE = "2026-09-06"


def _event(eid, start_ts):
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


def _coupon(kind, eid, start_ts, *, odd=2.0):
    return Coupon(
        kind=kind,
        legs=[
            Leg(
                event_id=eid,
                home=f"Ev{eid}",
                away=f"Dep{eid}",
                competition="Lig",
                start_ts=start_ts,
                market_code=config.MARKET_OVER_UNDER,
                market_name="Alt/Üst",
                sov="3.5",
                outcome_no=1,
                outcome_name="Alt",
                odd=odd,
                fair_prob=0.5,
            )
        ],
    )


def _at(hour, minute=0):
    return datetime.strptime(FOR_DATE, "%Y-%m-%d").replace(
        hour=hour, minute=minute, tzinfo=config.TIMEZONE
    )


def _stub_build(monkeypatch, event_ids, *, at=None, kickoff=None, coupons=None):
    """Pin what a build returns, and when it happened.

    Both hours matter: a coupon may only be replaced before its first match,
    and may only be followed up after it.
    """
    now = at or _at(10)
    kick = int((kickoff or _at(13)).timestamp())
    if coupons is None:
        coupons = {
            slot: _coupon(kind, eid, kick)
            for slot, kind, eid in zip(
                ("main", "alt", "mix"),
                config.DAILY_COUPON_KINDS,
                event_ids,
            )
        }
    monkeypatch.setattr(service, "capture_shadow_predictions", lambda *a, **k: None)
    monkeypatch.setattr(
        service,
        "_build_daily",
        lambda _path: (
            coupons,
            [_event(eid, kick) for eid in event_ids],
            COMPETITIONS,
            now,
            FOR_DATE,
        ),
    )


def test_the_day_hands_over_a_mixed_coupon_alongside_the_other_two(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "day.db")
    _stub_build(monkeypatch, [1, 2, 3])

    text = service.daily_text(path)
    assert "ANA KUPON" in text
    assert "ALTERNATİF" in text
    assert "KARMA KUPON" in text
    # Shown in the order they were built, so the reader meets the safest first.
    assert text.index("ANA KUPON") < text.index("ALTERNATİF") < text.index(
        "KARMA KUPON"
    )

    record = service.coupons_of_record(path, FOR_DATE)
    assert [entry["coupon"].kind for entry in record["mix"]] == ["daily_mix"]
    assert record["mix"][0]["coupon"].legs[0].event_id == 3


def test_a_stored_mixed_coupon_is_read_back_rather_than_rebuilt(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "record.db")
    _stub_build(monkeypatch, [1, 2, 3])
    service.daily_text(path)

    # A later build is a different slip at different prices; the day's answer
    # stays the one that will be settled.
    _stub_build(monkeypatch, [11, 12, 13])
    evening = service.daily_text(path)
    assert "Ev3 - Dep3" in evening
    assert "Ev13 - Dep13" not in evening


def test_a_rebuild_retires_the_mixed_coupon_too(tmp_path, monkeypatch):
    path = str(tmp_path / "rebuild.db")
    _stub_build(monkeypatch, [1, 2, 3])
    service.daily_text(path)

    _stub_build(monkeypatch, [11, 12, 13])
    replaced = service.daily_text(path, rebuild=True)
    assert "Ev13 - Dep13" in replaced
    assert "Ev3 - Dep3" not in replaced

    with Database(path) as db:
        kinds = [row["kind"] for row in db.daily_coupons_of_record(FOR_DATE)]
    assert kinds == ["daily_main", "daily_alt", "daily_mix"]


def test_a_played_mixed_coupon_is_followed_up_rather_than_rewritten(
    tmp_path, monkeypatch
):
    """Once its match has kicked off the coupon has been handed over.

    What is left of the day can carry another one, and the coupon already
    given out keeps standing rather than being replaced by it.
    """
    path = str(tmp_path / "followup.db")
    _stub_build(monkeypatch, [1, 2, 3], at=_at(10), kickoff=_at(13))
    service.daily_text(path)

    # Late afternoon: the 13:00 matches have been played, and the evening
    # board can carry a second slip.
    _stub_build(monkeypatch, [11, 12, 13], at=_at(15), kickoff=_at(20))
    service.daily_text(path)

    record = service.coupons_of_record(path, FOR_DATE)
    assert [entry["coupon"].legs[0].event_id for entry in record["mix"]] == [3, 13]
    assert len(record["main"]) == 2 and len(record["alt"]) == 2


def test_a_mixed_coupon_that_has_not_kicked_off_is_not_doubled_up(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "notyet.db")
    _stub_build(monkeypatch, [1, 2, 3], at=_at(10), kickoff=_at(20))
    service.daily_text(path)

    _stub_build(monkeypatch, [11, 12, 13], at=_at(12), kickoff=_at(22))
    service.daily_text(path)

    record = service.coupons_of_record(path, FOR_DATE)
    assert len(record["mix"]) == 1


def test_a_mixed_follow_up_cannot_be_filed_under_the_wrong_day(
    tmp_path, monkeypatch
):
    """A late build widens its window into tomorrow to find anything at all.

    Tomorrow's own coupon would then be free to bet the same match a second
    time, so a follow-up that reaches past midnight is refused.
    """
    path = str(tmp_path / "tomorrow.db")
    _stub_build(monkeypatch, [1, 2, 3], at=_at(10), kickoff=_at(13))
    service.daily_text(path)

    tomorrow = _at(13).replace(day=7)
    _stub_build(monkeypatch, [11, 12, 13], at=_at(23), kickoff=tomorrow)
    service.daily_text(path)

    record = service.coupons_of_record(path, FOR_DATE)
    assert len(record["mix"]) == 1


def test_the_mixed_coupon_is_settled_and_named_in_its_own_right(tmp_path):
    path = str(tmp_path / "settle.db")
    start = 1_786_197_600
    with Database(path) as db:
        db.save_events([_event(3, start)])
        db.save_coupon(_coupon("daily_mix", 3, start, odd=3.4), FOR_DATE)
        # Alt 3.5 with four goals on the board: the leg loses.
        db.save_result(MatchResult(3, 2, 2, 1, 1))
    service.settle_pending(path, None, notify=False)

    with Database(path) as db:
        stored = db.conn.execute(
            "SELECT kind, status FROM coupons WHERE for_date=?", (FOR_DATE,)
        ).fetchone()
    assert stored["kind"] == "daily_mix"
    assert stored["status"] == "lost"
    assert formatting._KIND_LABELS["daily_mix"] == "Karma Kupon"


def test_the_mixed_coupon_is_measured_on_evidence_of_its_own(tmp_path):
    """Its results are never pooled with the other two.

    The three coupons buy different things, so a mixed coupon riding on the
    main coupon's record - or dragging it down - would hide which of them is
    working.
    """
    path = str(tmp_path / "metrics.db")
    start = 1_786_197_600
    with Database(path) as db:
        db.save_events([_event(1, start), _event(3, start)])
        db.save_coupon(_coupon("daily_main", 1, start), FOR_DATE)
        db.save_coupon(_coupon("daily_mix", 3, start, odd=3.4), FOR_DATE)
        db.save_result(MatchResult(1, 1, 0, 0, 0))  # main leg lands
        db.save_result(MatchResult(3, 2, 2, 1, 1))  # mixed leg does not
    service.settle_pending(path, None, notify=False)

    by_kind = service.metrics_by_kind(path)
    assert by_kind["daily_main"]["won"] == 1
    assert by_kind["daily_mix"]["won"] == 0
    assert by_kind["daily_mix"]["lost"] == 1
    assert by_kind["daily_mix"]["minimum_matches"] == (
        config.PERFORMANCE_GATE_MIN_MATCHES["daily_mix"]
    )
    assert by_kind["daily_mix"]["gate_passed"] is False


def test_a_day_with_no_mixed_coupon_still_reads_as_a_day(tmp_path, monkeypatch):
    path = str(tmp_path / "nomix.db")
    _stub_build(
        monkeypatch,
        [1],
        coupons={
            "main": _coupon("daily_main", 1, int(_at(13).timestamp())),
            "alt": None,
            "mix": None,
        },
    )
    text = service.daily_text(path)
    assert "KARMA KUPON: uygun kupon bulunamadı." in text
