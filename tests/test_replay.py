from datetime import datetime

from otomasyon import config
from otomasyon.replay import replay_daily


def _row(index: int, total_goals: int) -> dict:
    start = datetime(2026, 3, 10, 13 + index, tzinfo=config.TIMEZONE)
    return {
        "source_id": str(index),
        "start_ts": int(start.timestamp()),
        "match_date": "2026-03-10",
        "competition": "Test Lig",
        "home": f"Home {index}",
        "away": f"Away {index}",
        "ft_home": total_goals,
        "ft_away": 0,
        "ht_home": 0,
        "ht_away": 0,
        "odds_home": None,
        "odds_draw": None,
        "odds_away": None,
        "odds_under25": 1.50,
        "odds_over25": 2.60,
    }


def test_replay_uses_production_engine_and_settles_main_and_alternative():
    history = [
        _row(0, 1),
        _row(1, 2),
        _row(2, 3),
        _row(3, 1),
    ]
    report = replay_daily(
        history, start_date="2026-03-10", end_date="2026-03-10"
    )
    assert report["method"] == "closing_odds_partial"
    assert report["main"]["coupons"] == 1
    assert report["main"]["won"] == 1
    assert report["alternative"]["coupons"] == 1
    assert report["alternative"]["lost"] == 1
    assert report["combined"]["roi"] == 0.125  # (2.25-1 - 1) / 2


def test_replay_excludes_matches_started_before_daily_generation():
    row = _row(0, 1)
    row["start_ts"] = int(
        datetime(2026, 3, 10, 9, tzinfo=config.TIMEZONE).timestamp()
    )
    report = replay_daily(
        [row], start_date="2026-03-10", end_date="2026-03-10"
    )
    assert report["main"]["coupons"] == 0
