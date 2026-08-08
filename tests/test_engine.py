from datetime import datetime

from otomasyon import config, engine
from otomasyon.iddaa.normalize import (
    NormalizedEvent,
    NormalizedMarket,
    NormalizedSelection,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=config.TIMEZONE)
START = int(NOW.timestamp()) + 3600  # 13:00 same local day


def _ou_event(event_id: int, alt_odd: float, ust_odd: float) -> NormalizedEvent:
    """An event exposing a single full-match Over/Under market."""
    market = NormalizedMarket(
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
    return NormalizedEvent(
        event_id=event_id,
        home=f"Home{event_id}",
        away=f"Away{event_id}",
        competition_id=1,
        competition_name="Test Lig",
        country_code="TR",
        sport_id=1,
        start_ts=START,
        status=0,
        markets=[market],
    )


def _events():
    # Each "Alt" is the favourite; odds chosen so pairs land in [2.0, 3.0].
    return [
        _ou_event(1, 1.50, 2.60),
        _ou_event(2, 1.55, 2.45),
        _ou_event(3, 1.45, 2.75),
        _ou_event(4, 1.60, 2.35),
    ]


def test_main_coupon_within_odds_window_and_one_per_match():
    coupons = engine.build_daily_coupons(_events(), now=NOW)
    main = coupons["main"]
    assert main is not None
    assert config.DAILY_MIN_LEGS <= len(main.legs) <= config.DAILY_MAX_LEGS
    assert config.DAILY_MIN_TOTAL_ODDS <= main.total_odds <= config.DAILY_MAX_TOTAL_ODDS
    # one selection per match
    assert len(main.event_ids) == len(main.legs)
    # all legs clear the safety floor
    assert all(leg.fair_prob >= config.LEG_MIN_FAIR_PROB for leg in main.legs)


def test_alternative_is_disjoint_from_main():
    coupons = engine.build_daily_coupons(_events(), now=NOW)
    main, alt = coupons["main"], coupons["alt"]
    assert main is not None and alt is not None
    assert main.event_ids.isdisjoint(alt.event_ids)
    assert config.DAILY_MIN_TOTAL_ODDS <= alt.total_odds <= config.DAILY_MAX_TOTAL_ODDS


def test_main_maximises_combined_probability():
    coupons = engine.build_daily_coupons(_events(), now=NOW)
    main = coupons["main"]
    # The two safest legs (highest fair prob = lowest odds) are events 3 (1.45)
    # and 1 (1.50); their product 2.175 is in range and is the safest pair.
    assert main.event_ids == {1, 3}


def test_favorite_match_result_requires_strong_edge():
    # A near-coin-flip 1X2 must not qualify (fair prob < 0.60 threshold).
    weak = NormalizedEvent(
        event_id=9,
        home="A",
        away="B",
        competition_id=1,
        competition_name="L",
        country_code="TR",
        sport_id=1,
        start_ts=START,
        status=0,
        markets=[
            NormalizedMarket(
                90,
                config.MARKET_MATCH_RESULT[0],
                config.MARKET_MATCH_RESULT[1],
                "Maç Sonucu",
                None,
                1,
                [
                    NormalizedSelection(1, "1", 2.10),
                    NormalizedSelection(2, "0", 3.30),
                    NormalizedSelection(3, "2", 3.50),
                ],
            )
        ],
    )
    assert engine.candidate_legs_for_event(weak) == []


def test_daily_pool_excludes_friendlies_even_with_attractive_odds():
    friendly = _ou_event(99, 1.50, 2.60)
    friendly.competition_name = "Kulüplerarası Hazırlık Maçlar"
    regular = _ou_event(100, 1.50, 2.60)
    pool = engine._select_pool(
        [friendly, regular], int(NOW.timestamp()), int(NOW.timestamp()) + 7200
    )
    assert {leg.event_id for leg in pool} == {100}
