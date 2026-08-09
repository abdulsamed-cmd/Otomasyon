"""Historical replay for the production daily coupon engine.

The Mackolik archive contains final pre-match 1X2 and O/U 2.5 odds, not a
10:00 odds snapshot and not every market available in the live iddaa bulletin.
Replay therefore exercises the real coupon construction and settlement paths,
but is explicitly labelled ``closing_odds_partial``. It must not be presented
as an exact simulation of what was visible at delivery time.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import date, datetime, timedelta

from . import config, engine
from .iddaa.normalize import (
    NormalizedEvent,
    NormalizedMarket,
    NormalizedSelection,
)
from .settlement import MatchResult, settle_coupon


def _valid_odds(values) -> bool:
    return all(value is not None and value > 1.0 for value in values)


def historical_events(rows: list[dict]) -> tuple[list[NormalizedEvent], dict[int, MatchResult]]:
    """Convert outcome-free archive fields to production event objects."""
    events = []
    results = {}
    for event_id, row in enumerate(rows, start=1):
        markets = []
        odds_1x2 = [row.get("odds_home"), row.get("odds_draw"), row.get("odds_away")]
        if _valid_odds(odds_1x2):
            markets.append(
                NormalizedMarket(
                    market_id=event_id * 10 + 1,
                    t=config.MARKET_MATCH_RESULT[0],
                    st=config.MARKET_MATCH_RESULT[1],
                    name="Maç Sonucu",
                    sov=None,
                    status=1,
                    selections=[
                        NormalizedSelection(1, "1", odds_1x2[0]),
                        NormalizedSelection(2, "0", odds_1x2[1]),
                        NormalizedSelection(3, "2", odds_1x2[2]),
                    ],
                )
            )
        odds_ou = [row.get("odds_under25"), row.get("odds_over25")]
        if _valid_odds(odds_ou):
            markets.append(
                NormalizedMarket(
                    market_id=event_id * 10 + 2,
                    t=config.MARKET_OVER_UNDER[0],
                    st=config.MARKET_OVER_UNDER[1],
                    name="Alt/Üst 2.5",
                    sov="2.5",
                    status=1,
                    selections=[
                        NormalizedSelection(1, "Alt", odds_ou[0]),
                        NormalizedSelection(2, "Üst", odds_ou[1]),
                    ],
                )
            )
        if not markets:
            continue
        events.append(
            NormalizedEvent(
                event_id=event_id,
                home=row["home"],
                away=row["away"],
                competition_id=None,
                competition_name=row.get("competition") or "",
                country_code=None,
                sport_id=config.SPORT_FOOTBALL,
                start_ts=row["start_ts"],
                status=0,
                markets=markets,
            )
        )
        results[event_id] = MatchResult(
            event_id=event_id,
            ft_home=row["ft_home"],
            ft_away=row["ft_away"],
            ht_home=row.get("ht_home"),
            ht_away=row.get("ht_away"),
            status="final",
            source="historical_replay",
        )
    return events, results


def _settle_replay_coupon(
    coupon, results: dict[int, MatchResult], for_date: str
) -> dict:
    legs = [
        {
            "event_id": leg.event_id,
            "market_t": leg.market_code[0],
            "market_st": leg.market_code[1],
            "market_sov": leg.sov,
            "outcome_name": leg.outcome_name,
            "odd": leg.odd,
        }
        for leg in coupon.legs
    ]
    settled = settle_coupon(legs, results)
    return {
        "kind": coupon.kind,
        "for_date": for_date,
        "total_odds": coupon.total_odds,
        "predicted_prob": coupon.combined_prob,
        "legs": len(coupon.legs),
        "status": settled.status,
        "profit": settled.profit,
        "markets": Counter(leg.market_name for leg in coupon.legs),
    }


def _summary(coupons: list[dict], total_days: int) -> dict:
    decided = [coupon for coupon in coupons if coupon["status"] in ("won", "lost")]
    profits = [coupon["profit"] for coupon in decided]
    wins = sum(coupon["status"] == "won" for coupon in decided)
    return {
        "days": total_days,
        "coupons": len(coupons),
        "coverage": len(coupons) / total_days if total_days else 0.0,
        "won": wins,
        "lost": sum(coupon["status"] == "lost" for coupon in decided),
        "hit_rate": wins / len(decided) if decided else 0.0,
        "roi": statistics.mean(profits) if profits else 0.0,
        "average_odds": (
            statistics.mean(coupon["total_odds"] for coupon in decided)
            if decided
            else 0.0
        ),
        "average_legs": (
            statistics.mean(coupon["legs"] for coupon in decided) if decided else 0.0
        ),
        "leg_counts": dict(Counter(coupon["legs"] for coupon in decided)),
        "market_counts": dict(
            sum((coupon["markets"] for coupon in decided), Counter())
        ),
        "_records": decided,
    }


def replay_daily(
    history: list[dict],
    *,
    start_date: str,
    end_date: str,
    generation_hour: int = config.DAILY_PUSH_HOUR,
    calibrated: bool = False,
    calibration_prior: float = 100.0,
    min_expected_value: float | None = None,
) -> dict:
    """Generate and settle main/alternative coupons for every calendar day."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    probability_provider = None
    if calibrated:
        from .calibration import MarketCalibrator

        training = [row for row in history if row["match_date"] < start_date]
        probability_provider = MarketCalibrator(
            training, prior_strength=calibration_prior
        )
    rows_by_date: dict[str, list[dict]] = {}
    # Include the next day because the production engine has a 24-hour fallback.
    for row in history:
        if start_date <= row["match_date"] <= (end + timedelta(days=1)).isoformat():
            rows_by_date.setdefault(row["match_date"], []).append(row)

    main_results = []
    alt_results = []
    day = start
    while day <= end:
        next_day = day + timedelta(days=1)
        rows = rows_by_date.get(day.isoformat(), []) + rows_by_date.get(
            next_day.isoformat(), []
        )
        events, results = historical_events(rows)
        now = datetime(
            day.year,
            day.month,
            day.day,
            generation_hour,
            tzinfo=config.TIMEZONE,
        )
        coupons = engine.build_daily_coupons(
            events,
            now=now,
            probability_provider=probability_provider,
            min_expected_value=min_expected_value,
        )
        if coupons["main"]:
            main_results.append(
                _settle_replay_coupon(coupons["main"], results, day.isoformat())
            )
        if coupons["alt"]:
            alt_results.append(
                _settle_replay_coupon(coupons["alt"], results, day.isoformat())
            )
        day += timedelta(days=1)

    total_days = (end - start).days + 1
    combined = main_results + alt_results
    return {
        "method": "closing_odds_partial",
        "limitations": [
            "archive odds are final pre-match odds, not the 10:00 snapshot",
            "only 1X2 and O/U 2.5 are available; double chance and BTTS are absent",
        ],
        "start_date": start_date,
        "end_date": end_date,
        "generation_hour": generation_hour,
        "calibrated": calibrated,
        "calibration_prior": calibration_prior if calibrated else None,
        "min_expected_value": min_expected_value,
        "main": _summary(main_results, total_days),
        "alternative": _summary(alt_results, total_days),
        "combined": _summary(combined, total_days * 2),
    }
