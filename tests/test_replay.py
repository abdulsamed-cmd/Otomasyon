from datetime import datetime

import pytest

from otomasyon import config
from otomasyon.replay import replay_daily


def _row(
    index: int, total_goals: int, under: float = 1.50, over: float = 2.60
) -> dict:
    start = datetime(2026, 3, 10, 13 + index, tzinfo=config.TIMEZONE)
    suffix = ("Alpha", "Bravo", "Charlie", "Delta")[index]
    return {
        "source_id": str(index),
        "start_ts": int(start.timestamp()),
        "match_date": "2026-03-10",
        "competition": "Test Lig",
        "home": f"Home {suffix}",
        "away": f"Away {suffix}",
        "ft_home": total_goals,
        "ft_away": 0,
        "ht_home": 0,
        "ht_away": 0,
        "odds_home": None,
        "odds_draw": None,
        "odds_away": None,
        "odds_under25": under,
        "odds_over25": over,
    }


def test_replay_uses_production_engine_and_settles_main_and_alternative():
    history = [
        # Only these two matches offer a selection priced inside the band, so
        # they become the main and the alternative coupon respectively.
        _row(0, 1, under=1.95, over=1.80),
        _row(1, 3, under=2.05, over=1.72),
        _row(2, 3),
        _row(3, 1),
    ]
    report = replay_daily(
        history, start_date="2026-03-10", end_date="2026-03-10"
    )
    assert report["method"] == "closing_odds_partial"
    assert report["main"]["coupons"] == 1
    assert report["main"]["won"] == 1
    assert report["main"]["average_legs"] == 1
    assert report["alternative"]["coupons"] == 1
    assert report["alternative"]["lost"] == 1
    assert report["combined"]["roi"] == pytest.approx((0.95 - 1.0) / 2)


def test_a_day_counts_once_however_many_of_its_coupons_landed():
    history = [
        _row(0, 1, under=1.95, over=1.80),  # main wins
        _row(1, 3, under=2.05, over=1.72),  # alternative loses
        _row(2, 3),
        _row(3, 1),
    ]
    report = replay_daily(history, start_date="2026-03-10", end_date="2026-03-10")
    any_hit = report["daily_any_hit"]
    assert report["combined"]["coupons"] == 2
    assert any_hit["days"] == 1
    assert any_hit["days_with_a_win"] == 1
    assert any_hit["rate"] == pytest.approx(1.0)


def test_a_day_whose_coupons_all_lost_scores_zero():
    history = [
        _row(0, 3, under=1.95, over=1.80),
        _row(1, 3, under=2.05, over=1.72),
        _row(2, 3),
        _row(3, 1),
    ]
    report = replay_daily(history, start_date="2026-03-10", end_date="2026-03-10")
    assert report["daily_any_hit"] == {
        "days": 1,
        "days_with_a_win": 0,
        "rate": 0.0,
    }


def test_a_cheaper_odds_band_buys_a_likelier_selection():
    history = [
        # 1.45 is the safer price, 1.95 the target price; only the requested
        # band should be reachable.
        _row(0, 1, under=1.45, over=2.70),
        _row(1, 1, under=1.95, over=1.80),
    ]
    default = replay_daily(history, start_date="2026-03-10", end_date="2026-03-10")
    cheaper = replay_daily(
        history,
        start_date="2026-03-10",
        end_date="2026-03-10",
        odds_band=(1.40, 1.60),
    )
    assert default["odds_band"] == (
        config.DAILY_MIN_TOTAL_ODDS,
        config.DAILY_MAX_TOTAL_ODDS,
    )
    assert default["main"]["average_odds"] == pytest.approx(1.95)
    assert cheaper["odds_band"] == (1.40, 1.60)
    assert cheaper["main"]["average_odds"] == pytest.approx(1.45)


def test_replay_excludes_matches_started_before_daily_generation():
    row = _row(0, 1)
    row["start_ts"] = int(
        datetime(2026, 3, 10, 9, tzinfo=config.TIMEZONE).timestamp()
    )
    report = replay_daily(
        [row], start_date="2026-03-10", end_date="2026-03-10"
    )
    assert report["main"]["coupons"] == 0
