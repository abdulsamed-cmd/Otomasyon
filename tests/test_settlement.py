import pytest

from otomasyon import config, settlement as s
from otomasyon.settlement import MatchResult, settle_leg, settle_coupon

MR = config.MARKET_MATCH_RESULT
DC = config.MARKET_DOUBLE_CHANCE
OU = config.MARKET_OVER_UNDER
BTTS = config.MARKET_BTTS
BAND = config.MARKET_TOTAL_GOALS_BAND
HTFT = config.MARKET_HTFT
ODD_EVEN = config.MARKET_ODD_EVEN
HT_RESULT = config.MARKET_HT_RESULT
HT_DC = config.MARKET_HT_DOUBLE_CHANCE
HT_OU = config.MARKET_HT_OVER_UNDER
HT_BTTS = config.MARKET_HT_BTTS
SH_RESULT = config.MARKET_SECOND_HALF_RESULT
HIGH_HALF = config.MARKET_HIGHER_SCORING_HALF
HOME_OU = config.MARKET_HOME_OVER_UNDER
AWAY_OU = config.MARKET_AWAY_OVER_UNDER
HOME_HT_OU = config.MARKET_HOME_HT_OVER_UNDER
AWAY_HT_OU = config.MARKET_AWAY_HT_OVER_UNDER

# Every half-of-match market needs one, so most cases below carry a half-time
# score even when the market under test does not read it.
HALF_MARKETS = (
    (HT_RESULT, None, "1"),
    (HT_DC, None, "1 ve 0"),
    (HT_OU, "1.5", "Üst"),
    (HT_BTTS, None, "Var"),
    (SH_RESULT, None, "1"),
    (HIGH_HALF, None, "1."),
    (HOME_HT_OU, "0.5", "Üst"),
    (AWAY_HT_OU, "0.5", "Üst"),
    (HTFT, None, "1/1"),
)


def r(ft_h, ft_a, ht_h=None, ht_a=None, status="final"):
    return MatchResult(1, ft_h, ft_a, ht_h, ht_a, status)


def test_match_result():
    assert settle_leg(*MR, None, "1", r(2, 0)) == s.WIN
    assert settle_leg(*MR, None, "0", r(1, 1)) == s.WIN
    assert settle_leg(*MR, None, "2", r(0, 1)) == s.WIN
    assert settle_leg(*MR, None, "1", r(0, 1)) == s.LOSE


def test_double_chance():
    assert settle_leg(*DC, None, "1 ve 0", r(1, 1)) == s.WIN   # draw
    assert settle_leg(*DC, None, "1 ve 0", r(2, 0)) == s.WIN   # home
    assert settle_leg(*DC, None, "1 ve 0", r(0, 2)) == s.LOSE  # away
    assert settle_leg(*DC, None, "0 ve 2", r(0, 1)) == s.WIN
    assert settle_leg(*DC, None, "1 ve 2", r(1, 1)) == s.LOSE


def test_over_under():
    assert settle_leg(*OU, "2.5", "Üst", r(2, 1)) == s.WIN   # 3 > 2.5
    assert settle_leg(*OU, "2.5", "Alt", r(1, 1)) == s.WIN   # 2 < 2.5
    assert settle_leg(*OU, "2.5", "Üst", r(1, 1)) == s.LOSE
    assert settle_leg(*OU, "3", "Alt", r(1, 2)) == s.VOID    # push on integer line


def test_btts():
    assert settle_leg(*BTTS, None, "Var", r(1, 2)) == s.WIN
    assert settle_leg(*BTTS, None, "Var", r(1, 0)) == s.LOSE
    assert settle_leg(*BTTS, None, "Yok", r(3, 0)) == s.WIN


def test_total_goals_band():
    assert settle_leg(*BAND, None, "6+ gol", r(4, 3)) == s.WIN   # 7
    assert settle_leg(*BAND, None, "6+ gol", r(3, 2)) == s.LOSE  # 5
    assert settle_leg(*BAND, None, "2-3 gol", r(1, 1)) == s.WIN
    assert settle_leg(*BAND, None, "0-1 gol", r(1, 0)) == s.WIN
    assert settle_leg(*BAND, None, "4-5 gol", r(2, 2)) == s.WIN


def test_htft():
    # 2/1 = away led at half, home won at full time
    assert settle_leg(*HTFT, None, "2/1", r(2, 1, ht_h=0, ht_a=1)) == s.WIN
    # 1/2 = home led at half, away won
    assert settle_leg(*HTFT, None, "1/2", r(1, 2, ht_h=1, ht_a=0)) == s.WIN
    assert settle_leg(*HTFT, None, "2/1", r(1, 1, ht_h=0, ht_a=1)) == s.LOSE
    # missing HT score -> cannot settle -> void
    assert settle_leg(*HTFT, None, "2/1", r(2, 1)) == s.VOID


def test_odd_even():
    assert settle_leg(*ODD_EVEN, None, "Tek", r(2, 1)) == s.WIN   # 3
    assert settle_leg(*ODD_EVEN, None, "Çift", r(2, 1)) == s.LOSE
    assert settle_leg(*ODD_EVEN, None, "Çift", r(2, 2)) == s.WIN  # 4
    # A goalless match has zero goals, and zero is even.
    assert settle_leg(*ODD_EVEN, None, "Çift", r(0, 0)) == s.WIN
    assert settle_leg(*ODD_EVEN, None, "Tek", r(0, 0)) == s.LOSE


def test_half_time_result():
    assert settle_leg(*HT_RESULT, None, "1", r(1, 1, ht_h=1, ht_a=0)) == s.WIN
    assert settle_leg(*HT_RESULT, None, "0", r(3, 0, ht_h=0, ht_a=0)) == s.WIN
    assert settle_leg(*HT_RESULT, None, "2", r(2, 1, ht_h=0, ht_a=1)) == s.WIN
    # Winning the match after trailing at half time does not win the half.
    assert settle_leg(*HT_RESULT, None, "1", r(2, 1, ht_h=0, ht_a=1)) == s.LOSE


def test_half_time_double_chance():
    assert settle_leg(*HT_DC, None, "1 ve 0", r(0, 3, ht_h=0, ht_a=0)) == s.WIN
    assert settle_leg(*HT_DC, None, "1 ve 0", r(1, 3, ht_h=1, ht_a=0)) == s.WIN
    assert settle_leg(*HT_DC, None, "1 ve 0", r(0, 3, ht_h=0, ht_a=1)) == s.LOSE
    assert settle_leg(*HT_DC, None, "0 ve 2", r(1, 1, ht_h=0, ht_a=1)) == s.WIN


def test_half_time_over_under():
    assert settle_leg(*HT_OU, "0.5", "Üst", r(4, 0, ht_h=1, ht_a=0)) == s.WIN
    assert settle_leg(*HT_OU, "0.5", "Alt", r(4, 0, ht_h=0, ht_a=0)) == s.WIN
    # Four goals in the match, none of them before the break.
    assert settle_leg(*HT_OU, "0.5", "Üst", r(4, 0, ht_h=0, ht_a=0)) == s.LOSE
    assert settle_leg(*HT_OU, "1.5", "Üst", r(2, 2, ht_h=1, ht_a=1)) == s.WIN
    assert settle_leg(*HT_OU, "1.5", "Alt", r(2, 2, ht_h=1, ht_a=0)) == s.WIN


def test_half_time_btts():
    assert settle_leg(*HT_BTTS, None, "Var", r(2, 1, ht_h=1, ht_a=1)) == s.WIN
    # Both teams scored in the match, but not both in the first half.
    assert settle_leg(*HT_BTTS, None, "Var", r(2, 1, ht_h=2, ht_a=0)) == s.LOSE
    assert settle_leg(*HT_BTTS, None, "Yok", r(2, 1, ht_h=2, ht_a=0)) == s.WIN


def test_second_half_result():
    # 1-1 at the break, 3-1 at the end: the second half was won 2-0.
    assert settle_leg(*SH_RESULT, None, "1", r(3, 1, ht_h=1, ht_a=1)) == s.WIN
    assert settle_leg(*SH_RESULT, None, "0", r(3, 1, ht_h=1, ht_a=1)) == s.LOSE
    # 2-0 up at the break, 2-2 at the end: the second half was lost 0-2.
    assert settle_leg(*SH_RESULT, None, "2", r(2, 2, ht_h=2, ht_a=0)) == s.WIN
    # Nothing after the break is a goalless second half, which is a draw.
    assert settle_leg(*SH_RESULT, None, "0", r(1, 0, ht_h=1, ht_a=0)) == s.WIN


def test_team_goals_over_under():
    assert settle_leg(*HOME_OU, "1.5", "Üst", r(2, 0)) == s.WIN
    assert settle_leg(*HOME_OU, "1.5", "Üst", r(1, 4)) == s.LOSE  # away goals do not count
    assert settle_leg(*AWAY_OU, "0.5", "Üst", r(0, 1)) == s.WIN
    assert settle_leg(*AWAY_OU, "0.5", "Alt", r(5, 0)) == s.WIN
    assert settle_leg(*HOME_HT_OU, "0.5", "Üst", r(3, 0, ht_h=1, ht_a=0)) == s.WIN
    assert settle_leg(*HOME_HT_OU, "0.5", "Üst", r(3, 0, ht_h=0, ht_a=0)) == s.LOSE
    assert settle_leg(*AWAY_HT_OU, "0.5", "Alt", r(0, 2, ht_h=0, ht_a=0)) == s.WIN


def test_higher_scoring_half():
    # 2-0 at the break, 2-1 at the end: 2 goals then 1.
    assert settle_leg(*HIGH_HALF, None, "1.", r(2, 1, ht_h=2, ht_a=0)) == s.WIN
    assert settle_leg(*HIGH_HALF, None, "2.", r(2, 1, ht_h=2, ht_a=0)) == s.LOSE
    # 0-0 at the break, 1-2 at the end: 0 goals then 3.
    assert settle_leg(*HIGH_HALF, None, "2.", r(1, 2, ht_h=0, ht_a=0)) == s.WIN
    # One goal in each half.
    assert settle_leg(*HIGH_HALF, None, "Eşit", r(1, 1, ht_h=1, ht_a=0)) == s.WIN
    # A goalless match scored equally in both halves.
    assert settle_leg(*HIGH_HALF, None, "Eşit", r(0, 0, ht_h=0, ht_a=0)) == s.WIN


@pytest.mark.parametrize("code,sov,outcome", HALF_MARKETS)
def test_half_markets_are_void_without_a_half_time_score(code, sov, outcome):
    assert settle_leg(*code, sov, outcome, r(2, 1)) == s.VOID


@pytest.mark.parametrize("code,sov,outcome", HALF_MARKETS)
def test_half_markets_are_void_when_half_time_beats_full_time(code, sov, outcome):
    # 3 goals by the break in a match that finished 1-0 never happened, so the
    # leg is left ungraded rather than settled off a scoreline that is wrong.
    assert settle_leg(*code, sov, outcome, r(1, 0, ht_h=3, ht_a=0)) == s.VOID


@pytest.mark.parametrize(
    "code,sov",
    [
        (MR, None), (DC, None), (OU, "2.5"), (BTTS, None), (BAND, None),
        (HTFT, None), (ODD_EVEN, None), (HT_RESULT, None), (HT_DC, None),
        (HT_OU, "1.5"), (HT_BTTS, None), (SH_RESULT, None), (HIGH_HALF, None),
        (HOME_OU, "1.5"), (AWAY_OU, "1.5"), (HOME_HT_OU, "0.5"), (AWAY_HT_OU, "0.5"),
    ],
)
def test_unknown_outcome_name_is_void_not_a_loss(code, sov):
    # If iddaa relabels a selection we would rather grade nothing than record
    # a loss the coupon never took.
    assert settle_leg(*code, sov, "Kırmızı Kart", r(2, 1, ht_h=1, ht_a=1)) == s.VOID


def test_postponed_is_void():
    assert settle_leg(*OU, "2.5", "Üst", r(None, None, status="postponed")) == s.VOID


# What each pooled market actually offers, read off a live iddaa bulletin. A
# market whose selections we cannot name is a market we cannot grade, so this
# is the vocabulary the coupon is allowed to speak.
BULLETIN_VOCABULARY = {
    MR: ("Maç Sonucu", None, ["1", "0", "2"]),
    DC: ("Çifte Şans", None, ["1 ve 0", "1 ve 2", "0 ve 2"]),
    OU: ("Alt/Üst", "2.5", ["Alt", "Üst"]),
    BTTS: ("Karşılıklı Gol", None, ["Var", "Yok"]),
    HT_RESULT: ("1. Yarı Sonucu", None, ["1", "0", "2"]),
    HT_DC: ("1. Yarı Çifte Şans", None, ["1 ve 0", "1 ve 2", "0 ve 2"]),
    HT_OU: ("1. Yarı Alt/Üst", "0.5", ["Alt", "Üst"]),
    HT_BTTS: ("1. Yarı Karşılıklı Gol", None, ["Var", "Yok"]),
    SH_RESULT: ("2. Yarı Sonucu", None, ["1", "0", "2"]),
    HIGH_HALF: ("Hangi Yarıda Daha Fazla Gol Olur", None, ["1.", "Eşit", "2."]),
    HOME_OU: ("Ev Sahibi Alt/Üst", "1.5", ["Alt", "Üst"]),
    AWAY_OU: ("Deplasman Alt/Üst", "1.5", ["Alt", "Üst"]),
    HOME_HT_OU: ("Ev Sahibi 1. Yarı Altı/Üstü", "0.5", ["Alt", "Üst"]),
    AWAY_HT_OU: ("Deplasman 1. Yarı Altı/Üstü", "0.5", ["Alt", "Üst"]),
}


@pytest.mark.parametrize("code", config.DAILY_COUPON_MARKETS)
def test_every_pooled_market_can_be_graded(code):
    """A coupon may never hold a leg the settlement cannot decide."""
    assert code in BULLETIN_VOCABULARY, "pooled market has no known selections"
    _, sov, outcomes = BULLETIN_VOCABULARY[code]
    graded = [
        settle_leg(*code, sov, outcome, r(2, 1, ht_h=1, ht_a=1))
        for outcome in outcomes
    ]
    assert s.VOID not in graded
    # Exactly one of a market's selections may win a given match, except double
    # chance, where each of the three covers two results and two must land.
    expected_winners = config.MARKET_OUTCOME_COVERAGE.get(code, 1)
    assert graded.count(s.WIN) == expected_winners


def _leg(event_id, code, outcome, odd, sov=None):
    return {
        "event_id": event_id,
        "market_t": code[0],
        "market_st": code[1],
        "market_sov": sov,
        "outcome_name": outcome,
        "odd": odd,
    }


def test_coupon_won_and_profit():
    legs = [
        _leg(1, OU, "Alt", 1.50, "3.5"),
        _leg(2, DC, "1 ve 0", 1.40),
    ]
    results = {1: MatchResult(1, 1, 1), 2: MatchResult(2, 2, 0)}
    st = settle_coupon(legs, results)
    assert st.status == "won"
    assert round(st.effective_odds, 2) == 2.10
    assert round(st.profit, 2) == 1.10


def test_coupon_lost_when_any_leg_loses():
    legs = [_leg(1, OU, "Üst", 1.5, "2.5"), _leg(2, MR, "1", 1.6)]
    results = {1: MatchResult(1, 0, 0), 2: MatchResult(2, 3, 0)}  # leg1 loses
    st = settle_coupon(legs, results)
    assert st.status == "lost"
    assert st.profit == -1.0


def test_coupon_void_leg_counts_as_one():
    legs = [_leg(1, OU, "Alt", 1.8, "3.5"), _leg(2, MR, "1", 1.7)]
    results = {
        1: MatchResult(1, None, None, status="postponed"),  # void -> 1.00
        2: MatchResult(2, 2, 0),  # win
    }
    st = settle_coupon(legs, results)
    assert st.status == "won"
    assert round(st.effective_odds, 2) == 1.70  # only the winning leg counts
    assert round(st.profit, 2) == 0.70


def test_coupon_pending_until_all_results_in():
    legs = [_leg(1, OU, "Alt", 1.5, "3.5"), _leg(2, MR, "1", 1.6)]
    results = {1: MatchResult(1, 1, 1)}  # leg 2 missing
    st = settle_coupon(legs, results)
    assert st.status == "pending"
