"""Deterministic coupon settlement.

Given a match result (half-time and full-time scores), decide whether each leg
won, lost, or is void, then settle the whole coupon. Postponed/cancelled
selections (and markets that cannot be evaluated) are treated as **void**,
i.e. their odd counts as 1.00 - matching the agreed rule.

These are pure functions with no I/O so they can be exhaustively unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config

WIN = "win"
LOSE = "lose"
VOID = "void"
PENDING = "pending"


@dataclass
class MatchResult:
    event_id: int
    ft_home: int | None = None
    ft_away: int | None = None
    ht_home: int | None = None
    ht_away: int | None = None
    status: str = "final"  # final | postponed | cancelled
    source: str | None = None

    @property
    def is_final(self) -> bool:
        return (
            self.status == "final"
            and self.ft_home is not None
            and self.ft_away is not None
        )


def _code(home: int, away: int) -> str:
    """1X2-style code: '1' home win, '0' draw, '2' away win."""
    if home > away:
        return "1"
    if home == away:
        return "0"
    return "2"


def _half_time(result: MatchResult) -> tuple[int, int] | None:
    """Half-time score, or ``None`` when it cannot be trusted.

    A half-time score that is missing, negative, or ahead of the full-time
    score never happened, and half-of-match markets are left ungraded rather
    than decided off a scoreline the match did not have.
    """
    home, away = result.ht_home, result.ht_away
    if home is None or away is None:
        return None
    if home < 0 or away < 0:
        return None
    if home > result.ft_home or away > result.ft_away:
        return None
    return home, away


def _decide(won: bool) -> str:
    return WIN if won else LOSE


def _one_x_two(home: int, away: int, outcome_name: str) -> str:
    if outcome_name not in ("1", "0", "2"):
        return VOID
    return _decide(outcome_name == _code(home, away))


def _double_chance(home: int, away: int, outcome_name: str) -> str:
    allowed = {
        "1 ve 0": {"1", "0"},
        "1 ve 2": {"1", "2"},
        "0 ve 2": {"0", "2"},
    }.get(outcome_name)
    if allowed is None:
        return VOID
    return _decide(_code(home, away) in allowed)


def _over_under(total: int, sov: str | None, outcome_name: str) -> str:
    try:
        line = float(sov)
    except (TypeError, ValueError):
        return VOID
    if total == line:
        return VOID  # push (only possible on integer lines)
    if outcome_name == "Üst":
        return _decide(total > line)
    if outcome_name == "Alt":
        return _decide(total < line)
    return VOID


def _both_scored(home: int, away: int, outcome_name: str) -> str:
    both = home > 0 and away > 0
    if outcome_name == "Var":
        return _decide(both)
    if outcome_name == "Yok":
        return _decide(not both)
    return VOID


def settle_leg(t: int, st: int, sov: str | None, outcome_name: str, result: MatchResult) -> str:
    """Return WIN / LOSE / VOID for one leg given a match result.

    An outcome name this module does not recognise is VOID rather than LOSE:
    if iddaa relabels a selection we would rather grade nothing than record a
    loss the coupon never took.
    """
    if not result.is_final:
        return VOID

    code = (t, st)
    ft = _code(result.ft_home, result.ft_away)
    total = result.ft_home + result.ft_away

    # --- Full match ---------------------------------------------------------
    if code == config.MARKET_MATCH_RESULT:
        return _one_x_two(result.ft_home, result.ft_away, outcome_name)

    if code == config.MARKET_DOUBLE_CHANCE:
        return _double_chance(result.ft_home, result.ft_away, outcome_name)

    if code == config.MARKET_OVER_UNDER:
        return _over_under(total, sov, outcome_name)

    if code == config.MARKET_BTTS:
        return _both_scored(result.ft_home, result.ft_away, outcome_name)

    if code == config.MARKET_ODD_EVEN:
        if outcome_name == "Tek":
            return _decide(total % 2 == 1)
        if outcome_name == "Çift":
            return _decide(total % 2 == 0)  # a goalless match is even
        return VOID

    if code == config.MARKET_TOTAL_GOALS_BAND:
        if outcome_name == "6+ gol":
            return _decide(total >= 6)
        band = {"0-1 gol": (0, 1), "2-3 gol": (2, 3), "4-5 gol": (4, 5)}.get(outcome_name)
        if band is None:
            return VOID
        return _decide(band[0] <= total <= band[1])

    # --- One team's goals ---------------------------------------------------
    if code == config.MARKET_HOME_OVER_UNDER:
        return _over_under(result.ft_home, sov, outcome_name)

    if code == config.MARKET_AWAY_OVER_UNDER:
        return _over_under(result.ft_away, sov, outcome_name)

    # --- Halves -------------------------------------------------------------
    half = _half_time(result)
    if half is None:
        return VOID  # every market below needs a half-time score
    ht_home, ht_away = half

    if code == config.MARKET_HT_RESULT:
        return _one_x_two(ht_home, ht_away, outcome_name)

    if code == config.MARKET_HT_DOUBLE_CHANCE:
        return _double_chance(ht_home, ht_away, outcome_name)

    if code == config.MARKET_HT_OVER_UNDER:
        return _over_under(ht_home + ht_away, sov, outcome_name)

    if code == config.MARKET_HT_BTTS:
        return _both_scored(ht_home, ht_away, outcome_name)

    if code == config.MARKET_HOME_HT_OVER_UNDER:
        return _over_under(ht_home, sov, outcome_name)

    if code == config.MARKET_AWAY_HT_OVER_UNDER:
        return _over_under(ht_away, sov, outcome_name)

    if code == config.MARKET_SECOND_HALF_RESULT:
        return _one_x_two(
            result.ft_home - ht_home, result.ft_away - ht_away, outcome_name
        )

    if code == config.MARKET_HIGHER_SCORING_HALF:
        first = ht_home + ht_away
        second = total - first
        scored_more = {
            "1.": first > second,
            "Eşit": first == second,
            "2.": second > first,
        }.get(outcome_name)
        if scored_more is None:
            return VOID
        return _decide(scored_more)

    if code == config.MARKET_HTFT:
        want = outcome_name.split("/")
        if len(want) != 2 or want[0] not in ("1", "0", "2") or want[1] not in ("1", "0", "2"):
            return VOID
        return _decide(want[0] == _code(ht_home, ht_away) and want[1] == ft)

    return VOID  # unknown / unsupported market


@dataclass
class LegSettlement:
    event_id: int
    outcome: str
    odd: float
    result: str  # win | lose | void | pending


@dataclass
class CouponSettlement:
    status: str  # won | lost | void | pending
    legs: list[LegSettlement] = field(default_factory=list)
    effective_odds: float = 1.0
    profit: float = 0.0  # flat 1-unit stake

    @property
    def is_decided(self) -> bool:
        return self.status in (WIN_COUPON := "won", "lost", "void")


def settle_coupon(legs: list[dict], results: dict[int, MatchResult]) -> CouponSettlement:
    """Settle a coupon.

    ``legs`` items must have: event_id, market_t, market_st, market_sov,
    outcome_name, odd. ``results`` maps event_id -> MatchResult.

    A coupon is 'won' if no leg lost and at least one leg won (void legs count
    as odd 1.00); 'lost' if any leg lost; 'void' if every leg is void; and
    'pending' while any leg still lacks a result.
    """
    leg_settlements: list[LegSettlement] = []
    any_pending = False
    any_lose = False
    any_win = False
    effective = 1.0

    for leg in legs:
        result = results.get(leg["event_id"])
        if result is None:
            outcome_res = PENDING
            any_pending = True
        else:
            outcome_res = settle_leg(
                leg["market_t"], leg["market_st"], leg.get("market_sov"),
                leg["outcome_name"], result,
            )
        leg_settlements.append(
            LegSettlement(
                event_id=leg["event_id"],
                outcome=leg["outcome_name"],
                odd=leg["odd"],
                result=outcome_res,
            )
        )
        if outcome_res == WIN:
            any_win = True
            effective *= leg["odd"]
        elif outcome_res == LOSE:
            any_lose = True
        # void -> odd counts as 1.00 (no change to effective)

    if any_pending and not any_lose:
        return CouponSettlement(status=PENDING, legs=leg_settlements)

    if any_lose:
        status, profit, effective = "lost", -1.0, 0.0
    elif any_win:
        status, profit = "won", effective - 1.0
    else:  # all void
        status, profit, effective = "void", 0.0, 1.0

    return CouponSettlement(
        status=status, legs=leg_settlements, effective_odds=effective, profit=profit
    )


def settlement_from_coupon(coupon: dict) -> CouponSettlement:
    """Rebuild a settlement from a coupon whose legs were already decided.

    Used to re-announce a result that was decided but never delivered, so the
    message matches what the original notification would have said.
    """
    legs = [
        LegSettlement(
            event_id=leg["event_id"],
            outcome=leg.get("outcome_name", ""),
            odd=leg["odd"],
            result=leg.get("result") or PENDING,
        )
        for leg in coupon.get("legs", [])
    ]
    status = coupon.get("status", PENDING)
    effective = 1.0
    for leg in legs:
        if leg.result == WIN:
            effective *= leg.odd

    if status == "lost":
        return CouponSettlement(status=status, legs=legs, effective_odds=0.0, profit=-1.0)
    if status == "void":
        return CouponSettlement(status=status, legs=legs, effective_odds=1.0, profit=0.0)
    if status == "won":
        return CouponSettlement(
            status=status, legs=legs, effective_odds=effective, profit=effective - 1.0
        )
    return CouponSettlement(status=PENDING, legs=legs)
