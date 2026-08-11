from datetime import datetime

from otomasyon import config, engine
from otomasyon.iddaa.normalize import (
    NormalizedEvent,
    NormalizedMarket,
    NormalizedSelection,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=config.TIMEZONE)
START = int(NOW.timestamp()) + 3600  # 13:00 same local day


def _event(event_id: int, markets: list[NormalizedMarket]) -> NormalizedEvent:
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
        markets=markets,
    )


def _ou_market(event_id: int, alt_odd: float, ust_odd: float) -> NormalizedMarket:
    return NormalizedMarket(
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


def _ou_event(event_id: int, alt_odd: float, ust_odd: float) -> NormalizedEvent:
    """An event exposing a single full-match Over/Under market."""
    return _event(event_id, [_ou_market(event_id, alt_odd, ust_odd)])


def _events():
    # Events 2 and 3 offer a selection priced inside the target band; events 1
    # and 4 only offer legs that need a partner.
    return [
        _ou_event(1, 1.60, 2.20),
        _ou_event(2, 1.95, 1.80),
        _ou_event(3, 2.05, 1.72),
        _ou_event(4, 1.45, 2.55),
    ]


def test_main_coupon_lands_in_the_odds_band_with_one_selection_per_match():
    main = engine.build_daily_coupons(_events(), now=NOW)["main"]
    assert main is not None
    assert config.DAILY_MIN_LEGS <= len(main.legs) <= config.DAILY_MAX_LEGS
    assert config.DAILY_MIN_TOTAL_ODDS <= main.total_odds <= config.DAILY_MAX_TOTAL_ODDS
    assert len(main.event_ids) == len(main.legs)
    assert all(leg.fair_prob >= config.LEG_MIN_FAIR_PROB for leg in main.legs)


def test_alternative_is_disjoint_from_main():
    coupons = engine.build_daily_coupons(_events(), now=NOW)
    main, alt = coupons["main"], coupons["alt"]
    assert main is not None and alt is not None
    assert main.event_ids.isdisjoint(alt.event_ids)
    assert config.DAILY_MIN_TOTAL_ODDS <= alt.total_odds <= config.DAILY_MAX_TOTAL_ODDS


def test_main_takes_the_likeliest_selection_priced_in_the_band():
    main = engine.build_daily_coupons(_events(), now=NOW)["main"]
    # Event 2's "Alt" at 1.95 is the most likely leg that reaches the band on
    # its own; event 3's 2.05 is the runner-up and goes to the alternative.
    assert main.event_ids == {2}
    assert main.legs[0].odd == 1.95


def test_single_leg_is_preferred_over_a_pair_paying_the_same():
    # The pair 1.45 x 1.40 = 2.03 also lands in the band, but it pays for two
    # market margins instead of one and must lose to the single.
    events = [_ou_event(1, 1.95, 1.80), _ou_event(2, 1.45, 2.55), _ou_event(3, 1.40, 2.75)]
    main = engine.build_daily_coupons(events, now=NOW)["main"]
    assert len(main.legs) == 1
    assert main.combined_prob > 0.45


def test_falls_back_to_a_pair_when_no_single_leg_reaches_the_band():
    events = [_ou_event(1, 1.45, 2.55), _ou_event(2, 1.40, 2.75), _ou_event(3, 1.30, 3.20)]
    main = engine.build_daily_coupons(events, now=NOW)["main"]
    assert len(main.legs) == 2
    assert config.DAILY_MIN_TOTAL_ODDS <= main.total_odds <= config.DAILY_MAX_TOTAL_ODDS


def test_cumulative_margin_grows_with_every_extra_leg():
    single = engine.build_daily_coupons([_ou_event(1, 1.95, 1.80)], now=NOW)["main"]
    pair = engine.build_daily_coupons(
        [_ou_event(2, 1.45, 2.55), _ou_event(3, 1.40, 2.75)], now=NOW
    )["main"]
    assert len(single.legs) == 1 and len(pair.legs) == 2
    assert pair.cumulative_margin > single.cumulative_margin


def test_double_chance_probabilities_are_not_halved():
    # The three double chance selections cover two results each, so their fair
    # probabilities must sum to 2 and stay usable, not be squeezed into 1.
    market = NormalizedMarket(
        70,
        config.MARKET_DOUBLE_CHANCE[0],
        config.MARKET_DOUBLE_CHANCE[1],
        "Çifte Şans",
        None,
        1,
        [
            NormalizedSelection(1, "1 ve 0", 1.59),
            NormalizedSelection(2, "1 ve 2", 1.17),
            NormalizedSelection(3, "0 ve 2", 1.22),
        ],
    )
    legs = engine.candidate_legs_for_event(_event(7, [market]))
    by_outcome = {leg.outcome_name: leg.fair_prob for leg in legs}
    assert by_outcome["1 ve 2"] > 0.70
    assert 1.9 < sum(by_outcome.values()) < 2.1


def test_expensive_market_is_left_out_of_the_pool():
    pricey = NormalizedMarket(
        80,
        config.MARKET_OVER_UNDER[0],
        config.MARKET_OVER_UNDER[1],
        "Alt/Üst 2.5",
        "2.5",
        1,
        [
            NormalizedSelection(1, "Alt", 1.65),
            NormalizedSelection(2, "Üst", 1.65),  # 21% margin
        ],
    )
    assert engine.candidate_legs_for_event(_event(8, [pricey])) == []


def test_combination_markets_are_not_offered_to_the_coupon():
    combo = NormalizedMarket(
        81,
        config.MARKET_HTFT[0],
        config.MARKET_HTFT[1],
        "İlk Yarı / Maç Sonucu",
        None,
        1,
        [
            NormalizedSelection(1, "1/1", 1.95),
            NormalizedSelection(2, "0/1", 1.95),
        ],
    )
    assert engine.candidate_legs_for_event(_event(9, [combo])) == []


def test_expected_value_gate_can_refuse_an_unprofitable_coupon():
    coupons = engine.build_daily_coupons(_events(), now=NOW, min_expected_value=0.0)
    assert coupons == {"main": None, "alt": None}


def test_daily_pool_excludes_friendlies_even_with_attractive_odds():
    friendly = _ou_event(99, 1.95, 1.80)
    friendly.competition_name = "Kulüplerarası Hazırlık Maçlar"
    regular = _ou_event(100, 1.95, 1.80)
    pool = engine._select_pool(
        [friendly, regular], int(NOW.timestamp()), int(NOW.timestamp()) + 7200
    )
    assert {leg.event_id for leg in pool} == {100}
