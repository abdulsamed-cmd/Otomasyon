from datetime import datetime
from math import comb

from otomasyon import config, surprise
from otomasyon.iddaa.normalize import (
    NormalizedEvent,
    NormalizedMarket,
    NormalizedSelection,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=config.TIMEZONE)
START = int(NOW.timestamp()) + 3600


def _htft_event(eid: int, odd_12: float, odd_21: float) -> NormalizedEvent:
    m = NormalizedMarket(
        eid * 10, config.MARKET_HTFT[0], config.MARKET_HTFT[1],
        "1. Yarı / Maç Sonucu", None, 1,
        [
            NormalizedSelection(1, "1/1", 3.0),
            NormalizedSelection(3, "1/2", odd_12),
            NormalizedSelection(7, "2/1", odd_21),
        ],
    )
    return _wrap(eid, [m])


def _goals_event(eid: int, odd_6plus: float) -> NormalizedEvent:
    m = NormalizedMarket(
        eid * 10, config.MARKET_TOTAL_GOALS_BAND[0], config.MARKET_TOTAL_GOALS_BAND[1],
        "Toplam Gol", None, 1,
        [
            NormalizedSelection(1, "0-1 gol", 3.2),
            NormalizedSelection(2, "2-3 gol", 1.9),
            NormalizedSelection(3, "4-5 gol", 4.5),
            NormalizedSelection(4, "6+ gol", odd_6plus),
        ],
    )
    return _wrap(eid, [m])


def _wrap(eid: int, markets) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=eid, home=f"H{eid}", away=f"A{eid}", competition_id=1,
        competition_name="Lig", country_code="TR", sport_id=1,
        start_ts=START, status=0, markets=markets,
    )


def test_finds_and_ranks_candidates():
    events = [
        _htft_event(1, odd_12=9.0, odd_21=15.0),   # 1/2 more likely than event 2
        _htft_event(2, odd_12=13.0, odd_21=21.0),
        _goals_event(3, odd_6plus=7.0),
    ]
    report = surprise.build_surprise(events, now=NOW)

    assert report.has_candidates
    twelve = report.by_category["htft_12"]
    assert [c.event_id for c in twelve] == [1, 2]  # lower odd (event 1) ranks first
    assert all(c.outcome_name == "1/2" for c in twelve)

    goals = report.by_category["goals_6plus"]
    assert len(goals) == 1 and goals[0].outcome_name == "6+ gol"


def test_system_set_has_distinct_matches():
    events = [_htft_event(i, 8.0 + i, 16.0 + i) for i in range(1, 5)]
    events.append(_goals_event(9, 6.5))
    report = surprise.build_surprise(events, now=NOW)
    ids = [c.event_id for c in report.system_set]
    assert len(ids) == len(set(ids))  # no match repeated


def test_system_scenarios_columns_and_cost():
    scenarios = surprise.system_scenarios(6, unit_stake=20.0)
    assert [s.size for s in scenarios] == [2, 3, 4, 5, 6]
    by_size = {s.size: s for s in scenarios}
    assert by_size[3].columns == comb(6, 3) == 20
    assert by_size[3].min_cost == 20 * 20.0
    assert by_size[6].columns == 1  # full combine
