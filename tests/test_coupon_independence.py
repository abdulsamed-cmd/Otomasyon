"""The day's coupons stand on separate matches, including the later ones.

A day hands over more than one coupon per kind: once a coupon's first match
kicks off, a build from what is left is filed alongside it. That later build is
a fresh one, and the risk is that it lands on a match a coupon already on
record is riding -- two slips then live or die on the same result, which is the
one thing the three kinds are meant not to do.
"""

from datetime import datetime, timedelta

import pytest

from otomasyon import config, service
from otomasyon.iddaa.normalize import (
    NormalizedEvent,
    NormalizedMarket,
    NormalizedSelection,
)

FOR_DATE = "2026-09-08"


def _at(hour, minute=0):
    return datetime.strptime(FOR_DATE, "%Y-%m-%d").replace(
        hour=hour, minute=minute, tzinfo=config.TIMEZONE
    )


def _event(event_id: int, start_ts: int, alt_odd: float, ust_odd: float):
    return NormalizedEvent(
        event_id=event_id,
        home=f"Ev{event_id}",
        away=f"Dep{event_id}",
        competition_id=1,
        competition_name="Test Lig",
        country_code="TR",
        sport_id=1,
        start_ts=start_ts,
        status=0,
        markets=[
            NormalizedMarket(
                market_id=event_id * 10,
                t=config.MARKET_OVER_UNDER[0],
                st=config.MARKET_OVER_UNDER[1],
                name="Alt/Üst 2.5",
                sov="2.5",
                status=1,
                selections=[
                    NormalizedSelection(1, "Alt", alt_odd),
                    NormalizedSelection(2, "Üst", ust_odd),
                ],
            )
        ],
    )


# Prices spread either side of even money so every kind has something to reach
# for, and kickoffs half an hour apart so the day can be advanced past one
# coupon without advancing past all of them.
_PRICES = [
    (1.60, 2.20),
    (1.95, 1.80),
    (2.05, 1.72),
    (1.45, 2.55),
    (1.75, 2.00),
    (1.55, 2.35),
    (1.85, 1.90),
    (2.10, 1.68),
]


def _bulletin():
    first = _at(13)
    return [
        _event(
            i + 1,
            int((first + timedelta(minutes=30 * i)).timestamp()),
            alt,
            ust,
        )
        for i, (alt, ust) in enumerate(_PRICES)
    ]


@pytest.fixture
def day(monkeypatch):
    """Run the real build against a fixed bulletin at an hour we choose."""
    events = _bulletin()
    clock = {"now": _at(10)}

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(service, "datetime", _Clock)
    monkeypatch.setattr(service, "get_live_events", lambda *a, **k: (events, {}))
    monkeypatch.setattr(service, "capture_shadow_predictions", lambda *a, **k: None)
    return clock


def _live_coupons(path):
    record = service.coupons_of_record(path, FOR_DATE)
    return [entry for entries in record.values() for entry in entries]


def _first_kickoff(entry):
    return min(leg.start_ts for leg in entry["coupon"].legs)


def test_a_follow_up_does_not_land_on_a_match_a_live_coupon_already_rides(
    tmp_path, day
):
    path = str(tmp_path / "day.db")
    service.daily_text(path)

    opening = _live_coupons(path)
    assert len(opening) >= 2, "need more than one coupon for them to collide"

    # Move the day just past the earliest coupon, so that one may be followed
    # up while the others keep the coupon they have.
    day["now"] = datetime.fromtimestamp(
        min(_first_kickoff(e) for e in opening) + 60, tz=config.TIMEZONE
    )
    service.daily_text(path)

    seen: dict[int, int] = {}
    for entry in _live_coupons(path):
        for leg in entry["coupon"].legs:
            assert leg.event_id not in seen, (
                f"match {leg.event_id} is ridden by coupon {seen.get(leg.event_id)} "
                f"and coupon {entry['id']} at the same time"
            )
            seen[leg.event_id] = entry["id"]

    assert len(_live_coupons(path)) > len(opening), "no follow-up was filed at all"


def test_a_rebuild_may_take_back_the_matches_the_retired_coupons_held(tmp_path, day):
    """Superseding the day frees its matches; otherwise a rebuild starves."""
    path = str(tmp_path / "day.db")
    service.daily_text(path)
    held = {
        leg.event_id for e in _live_coupons(path) for leg in e["coupon"].legs
    }
    assert held

    service.daily_text(path, rebuild=True)

    rebuilt = _live_coupons(path)
    assert rebuilt, "the rebuild produced nothing"
    assert {leg.event_id for e in rebuilt for leg in e["coupon"].legs} & held
