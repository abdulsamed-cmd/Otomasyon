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
