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


def settle_leg(t: int, st: int, sov: str | None, outcome_name: str, result: MatchResult) -> str:
    """Return WIN / LOSE / VOID for one leg given a match result."""
    if not result.is_final:
        return VOID

    code = (t, st)
    ft = _code(result.ft_home, result.ft_away)
    total = result.ft_home + result.ft_away

    if code == config.MARKET_MATCH_RESULT:
        return WIN if outcome_name == ft else LOSE

    if code == config.MARKET_DOUBLE_CHANCE:
        allowed = {
            "1 ve 0": {"1", "0"},
            "1 ve 2": {"1", "2"},
            "0 ve 2": {"0", "2"},
        }.get(outcome_name)
        return WIN if allowed and ft in allowed else LOSE

    if code == config.MARKET_OVER_UNDER:
        try:
            line = float(sov)
        except (TypeError, ValueError):
            return VOID
        if total == line:
            return VOID  # push (only possible on integer lines)
        if outcome_name == "Üst":
            return WIN if total > line else LOSE
        if outcome_name == "Alt":
            return WIN if total < line else LOSE
        return VOID

    if code == config.MARKET_BTTS:
        both = result.ft_home > 0 and result.ft_away > 0
        if outcome_name == "Var":
            return WIN if both else LOSE
        if outcome_name == "Yok":
            return WIN if not both else LOSE
        return VOID

    if code == config.MARKET_TOTAL_GOALS_BAND:
        if outcome_name == "6+ gol":
            return WIN if total >= 6 else LOSE
        bands = {"0-1 gol": (0, 1), "2-3 gol": (2, 3), "4-5 gol": (4, 5)}
        band = bands.get(outcome_name)
        return WIN if band and band[0] <= total <= band[1] else LOSE

    if code == config.MARKET_HTFT:
        if result.ht_home is None or result.ht_away is None:
            return VOID
        ht = _code(result.ht_home, result.ht_away)
        want = outcome_name.split("/")
        return WIN if len(want) == 2 and want[0] == ht and want[1] == ft else LOSE

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
