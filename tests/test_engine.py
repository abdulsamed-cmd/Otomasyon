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
    return [
        _ou_event(1, 1.60, 2.20),
        _ou_event(2, 1.95, 1.80),
        _ou_event(3, 2.05, 1.72),
        _ou_event(4, 1.45, 2.55),
    ]


def test_main_coupon_clears_its_floor_with_one_selection_per_match():
    main = engine.build_daily_coupons(_events(), now=NOW)["main"]
    assert main is not None
    assert config.DAILY_MIN_LEGS <= len(main.legs) <= config.DAILY_MAX_LEGS
    assert main.total_odds >= config.DAILY_MAIN_MIN_ODDS
    assert len(main.event_ids) == len(main.legs)
    assert all(leg.fair_prob >= config.LEG_MIN_FAIR_PROB for leg in main.legs)


def test_alternative_reaches_a_higher_payout_on_different_matches():
    coupons = engine.build_daily_coupons(_events(), now=NOW)
    main, alt = coupons["main"], coupons["alt"]
    assert main is not None and alt is not None
    assert main.event_ids.isdisjoint(alt.event_ids)
    assert alt.total_odds >= config.DAILY_ALT_MIN_ODDS
    # The alternative buys a bigger payout, so it must be the longer shot.
    assert alt.combined_prob < main.combined_prob


def test_main_takes_the_likeliest_selection_that_clears_the_floor():
    main = engine.build_daily_coupons(_events(), now=NOW)["main"]
    # 1.60 is the cheapest price at or above the 1.50 floor, and therefore the
    # likeliest single available; nothing pricier and no pair can beat it.
    assert main.event_ids == {1}
    assert main.legs[0].odd == 1.60


def _mbs_event(event_id: int, alt_odd: float, ust_odd: float, mbs: int):
    event = _ou_event(event_id, alt_odd, ust_odd)
    event.markets[0].mbs = mbs
    return event


def test_a_leg_iddaa_will_not_accept_alone_cannot_carry_a_single():
    # The likeliest selection on the board needs two matches on the slip, so a
    # single has to fall back to the weaker one that may be played on its own.
    events = [
        _mbs_event(1, 1.55, 2.45, mbs=2),
        _mbs_event(2, 1.75, 2.10, mbs=1),
    ]
    main = engine.build_daily_coupons(events, now=NOW)["main"]
    assert len(main.legs) == 1
    assert main.legs[0].event_id == 2
    assert main.legs[0].odd == 1.75


def test_a_restricted_leg_is_allowed_once_the_coupon_is_long_enough():
    events = [
        _mbs_event(1, 1.45, 2.60, mbs=2),
        _mbs_event(2, 1.40, 2.75, mbs=2),
    ]
    main = engine.build_daily_coupons(events, now=NOW)["main"]
    assert len(main.legs) == 2
    assert all(leg.mbs == 2 for leg in main.legs)
    assert len(main.legs) >= max(leg.mbs for leg in main.legs)


def test_a_market_nobody_can_reach_produces_no_coupon():
    # Three matches on the board but every market demands four, so there is
    # nothing here that could be handed over a counter.
    events = [_mbs_event(index, 1.45, 2.60, mbs=4) for index in range(1, 4)]
    assert engine.build_daily_coupons(events, now=NOW)["main"] is None


def test_no_upper_bound_stops_a_coupon_from_paying_more_than_asked():
    # The favourite sits under the floor, so the only selection that clears it
    # pays 2.60 - well past what was asked for, and still accepted.
    events = [_ou_event(1, 1.45, 2.60)]
    main = engine.build_daily_coupons(events, now=NOW, main_min_odds=1.50)["main"]
    assert main is not None
    assert main.total_odds == 2.60


def test_single_leg_is_preferred_over_a_pair_paying_the_same():
    # The pair 1.45 x 1.40 = 2.03 also lands in the band, but it pays for two
    # market margins instead of one and must lose to the single.
    events = [_ou_event(1, 1.95, 1.80), _ou_event(2, 1.45, 2.55), _ou_event(3, 1.40, 2.75)]
    main = engine.build_daily_coupons(events, now=NOW)["main"]
    assert len(main.legs) == 1
    assert main.combined_prob > 0.45


def test_a_pair_wins_when_every_single_at_that_price_is_a_longshot():
    # Each match is a heavy favourite priced under the floor, so reaching the
    # floor alone means buying the outsider. Two favourites together clear it
    # and land far more often.
    events = [_ou_event(1, 1.45, 2.55), _ou_event(2, 1.40, 2.75), _ou_event(3, 1.30, 3.20)]
    main = engine.build_daily_coupons(events, now=NOW)["main"]
    assert len(main.legs) == 2
    assert main.total_odds >= config.DAILY_MAIN_MIN_ODDS
    assert all(leg.outcome_name == "Alt" for leg in main.legs)
    assert main.combined_prob > 0.45


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
