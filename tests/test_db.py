import copy
import time

from otomasyon.iddaa.markets import MarketResolver
from otomasyon.iddaa.normalize import build_competitions_map, normalize_events
from otomasyon.storage import Database

from .test_normalize import MARKET_CONFIG, RAW_COMPETITIONS, RAW_EVENTS


def _events():
    resolver = MarketResolver(MARKET_CONFIG)
    comps = build_competitions_map(RAW_COMPETITIONS)
    return comps, normalize_events(RAW_EVENTS, resolver, comps)


def test_save_events_persists_counts():
    comps, events = _events()
    db = Database(":memory:")
    db.upsert_competitions(comps)
    stats = db.save_events(events)

    assert stats["events"] == 1
    assert stats["markets"] == 2
    assert stats["selections"] == 5  # 3 (1X2) + 2 (O/U)
    assert stats["odds"] == 5

    assert db.count("events") == 1
    assert db.count("competitions") == 1
    assert db.count("odds_snapshots") == 5
    db.close()


def test_save_events_is_idempotent_for_entities_but_appends_odds():
    comps, events = _events()
    db = Database(":memory:")
    db.upsert_competitions(comps)
    db.save_events(events)
    db.save_events(events)  # second capture (e.g. later in the day)

    # Entities are upserted (no duplicates)...
    assert db.count("events") == 1
    assert db.count("markets") == 2
    assert db.count("selections") == 5
    # ...but odds snapshots accumulate for movement/CLV analysis.
    assert db.count("odds_snapshots") == 10
    db.close()


def test_load_bulletin_as_of_rebuilds_exact_capture_odds_and_status():
    comps, events = _events()
    db = Database(":memory:")
    db.upsert_competitions(comps)
    db.save_events(events, now=100)

    changed = copy.deepcopy(events)
    changed[0].markets[0].status = 0
    changed[0].markets[0].selections[0].odd = 9.99
    db.save_events(changed, now=200)

    old = db.load_bulletin_as_of(150)
    new = db.load_bulletin_as_of(200)
    assert old[0].markets[0].status == 1
    assert old[0].markets[0].selections[0].odd != 9.99
    assert new[0].markets[0].status == 0
    assert new[0].markets[0].selections[0].odd == 9.99
    db.close()


def test_empty_capture_removes_prior_events_from_point_in_time_bulletin():
    comps, events = _events()
    db = Database(":memory:")
    db.upsert_competitions(comps)
    db.save_events(events, now=100)
    db.save_events([], now=200)
    assert len(db.load_bulletin_as_of(150)) == 1
    assert db.load_bulletin_as_of(200) == []
    db.close()


def _in_a_competition_nobody_listed():
    """A bulletin whose one fixture names a competition the listing left out."""
    comps, events = _events()
    events[0].competition_id = 999
    return comps, events


def test_a_match_in_an_unlisted_competition_does_not_cost_the_day_its_prices():
    comps, events = _in_a_competition_nobody_listed()
    db = Database(":memory:")
    db.upsert_competitions(comps)

    stats = db.save_events(events)

    assert stats["events"] == 1
    assert db.count("odds_snapshots") == 5
    assert db.load_bulletin_as_of(int(time.time()) + 1)[0].event_id == 3069509
    db.close()


def test_the_competition_is_named_once_a_listing_finally_carries_it():
    comps, events = _in_a_competition_nobody_listed()
    db = Database(":memory:")
    db.upsert_competitions(comps)
    db.save_events(events)
    assert db.conn.execute(
        "SELECT name FROM competitions WHERE id=999"
    ).fetchone()["name"] == ""

    db.upsert_competitions({999: {"name": "Malta Premier Ligi", "country_code": "MT"}})

    assert db.conn.execute(
        "SELECT name FROM competitions WHERE id=999"
    ).fetchone()["name"] == "Malta Premier Ligi"
    assert db.count("competitions") == 2
    db.close()
