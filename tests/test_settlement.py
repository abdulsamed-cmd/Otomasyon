from otomasyon import config, settlement as s
from otomasyon.settlement import MatchResult, settle_leg, settle_coupon

MR = config.MARKET_MATCH_RESULT
DC = config.MARKET_DOUBLE_CHANCE
OU = config.MARKET_OVER_UNDER
BTTS = config.MARKET_BTTS
BAND = config.MARKET_TOTAL_GOALS_BAND
HTFT = config.MARKET_HTFT


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


def test_postponed_is_void():
    assert settle_leg(*OU, "2.5", "Üst", r(None, None, status="postponed")) == s.VOID


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
