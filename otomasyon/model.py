"""Contextual football goal model and chronological value backtest.

This is intentionally a transparent baseline, not a black box:

* recent matches receive exponentially more weight;
* team scoring/conceding strength is shrunk toward the global mean;
* home advantage is learned from the historical sample;
* independent Poisson goals produce 1X2, O/U, BTTS and related probabilities.

The model is report/backtest-only by default. It must demonstrate stable
out-of-sample calibration/ROI before it may influence live coupons.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from . import config, probability
from .eligibility import is_daily_eligible
from .results.matcher import normalize_team


@dataclass
class GoalPrediction:
    home_lambda: float
    away_lambda: float
    confidence: float
    samples_home: int
    samples_away: int
    probs: dict[str, float]


def _poisson_probs(lam: float, max_goals: int = 10) -> list[float]:
    probs = [math.exp(-lam)]
    for goals in range(1, max_goals + 1):
        probs.append(probs[-1] * lam / goals)
    # Put the tiny omitted tail into the last bucket.
    probs[-1] += max(0.0, 1.0 - sum(probs))
    return probs


class GoalModel:
    def __init__(self, history: list[dict], cutoff_ts: int) -> None:
        earliest = cutoff_ts - config.MODEL_LOOKBACK_DAYS * 86400
        self.history = [
            row
            for row in history
            if earliest <= row["start_ts"] < cutoff_ts
            and is_daily_eligible(row.get("competition") or "")
        ]
        self.cutoff_ts = cutoff_ts
        self._fit()

    def _fit(self) -> None:
        ln2 = math.log(2)
        global_weight = global_home = global_away = 0.0
        stats: dict[tuple[str, str], dict] = {}
        competitions: dict[str, dict] = {}

        for row in self.history:
            age_days = max(0.0, (self.cutoff_ts - row["start_ts"]) / 86400)
            weight = math.exp(-ln2 * age_days / config.MODEL_HALF_LIFE_DAYS)
            global_weight += weight
            global_home += weight * row["ft_home"]
            global_away += weight * row["ft_away"]
            competition = row.get("competition") or ""
            comp = competitions.setdefault(
                competition, {"weight": 0.0, "home": 0.0, "away": 0.0}
            )
            comp["weight"] += weight
            comp["home"] += weight * row["ft_home"]
            comp["away"] += weight * row["ft_away"]
            for key, venue, scored, conceded in (
                (row["home_key"], "home", row["ft_home"], row["ft_away"]),
                (row["away_key"], "away", row["ft_away"], row["ft_home"]),
            ):
                item = stats.setdefault(
                    (key, venue),
                    {"weight": 0.0, "scored": 0.0, "conceded": 0.0, "n": 0},
                )
                item["weight"] += weight
                item["scored"] += weight * scored
                item["conceded"] += weight * conceded
                item["n"] += 1

        if global_weight:
            self.avg_home = global_home / global_weight
            self.avg_away = global_away / global_weight
        else:
            self.avg_home, self.avg_away = 1.45, 1.15
        self.avg_team = max(0.2, (self.avg_home + self.avg_away) / 2)
        self.home_factor = math.sqrt(
            max(0.5, self.avg_home) / max(0.5, self.avg_away)
        )
        self.stats = stats
        self.competitions = competitions

    def _competition_rates(self, competition: str | None) -> tuple[float, float]:
        item = self.competitions.get(competition or "")
        # Require a reasonable effective league sample; otherwise use global.
        if not item or item["weight"] < 20:
            return self.avg_home, self.avg_away
        return item["home"] / item["weight"], item["away"] / item["weight"]

    def _team_rates(
        self,
        team: str,
        venue: str,
        baseline_scored: float,
        baseline_conceded: float,
    ) -> tuple[float, float, int]:
        item = self.stats.get((normalize_team(team), venue))
        if not item:
            return baseline_scored, baseline_conceded, 0
        prior = config.MODEL_PRIOR_MATCHES
        denominator = item["weight"] + prior
        attack = (item["scored"] + prior * baseline_scored) / denominator
        defense = (item["conceded"] + prior * baseline_conceded) / denominator
        return attack, defense, item["n"]

    def predict(
        self, home: str, away: str, competition: str | None = None
    ) -> GoalPrediction:
        league_home, league_away = self._competition_rates(competition)
        home_attack, home_defense, home_n = self._team_rates(
            home, "home", league_home, league_away
        )
        away_attack, away_defense, away_n = self._team_rates(
            away, "away", league_away, league_home
        )
        # Opponent-adjusted multiplicative attack/defence strengths.
        home_lam = home_attack * (away_defense / max(0.2, league_home))
        away_lam = away_attack * (home_defense / max(0.2, league_away))
        home_lam = min(4.0, max(0.15, home_lam))
        away_lam = min(4.0, max(0.15, away_lam))
        sample = min(home_n, away_n)
        confidence = sample / (sample + config.MODEL_PRIOR_MATCHES)
        if sample < config.MODEL_MIN_TEAM_MATCHES:
            confidence *= sample / config.MODEL_MIN_TEAM_MATCHES
        return GoalPrediction(
            home_lambda=home_lam,
            away_lambda=away_lam,
            confidence=confidence,
            samples_home=home_n,
            samples_away=away_n,
            probs=self._market_probs(home_lam, away_lam),
        )

    @staticmethod
    def _market_probs(home_lam: float, away_lam: float) -> dict[str, float]:
        hp, ap = _poisson_probs(home_lam), _poisson_probs(away_lam)
        matrix = [
            [home_p * away_p for away_p in ap]
            for home_p in hp
        ]
        p_home = sum(
            matrix[h][a] for h in range(len(hp)) for a in range(len(ap)) if h > a
        )
        p_draw = sum(matrix[g][g] for g in range(min(len(hp), len(ap))))
        p_away = max(0.0, 1.0 - p_home - p_draw)
        btts = sum(
            matrix[h][a]
            for h in range(1, len(hp))
            for a in range(1, len(ap))
        )
        probs = {
            "1": p_home,
            "0": p_draw,
            "2": p_away,
            "1 ve 0": p_home + p_draw,
            "1 ve 2": p_home + p_away,
            "0 ve 2": p_draw + p_away,
            "Var": btts,
            "Yok": 1.0 - btts,
        }
        for line in (0.5, 1.5, 2.5, 3.5, 4.5):
            under = sum(
                matrix[h][a]
                for h in range(len(hp))
                for a in range(len(ap))
                if h + a < line
            )
            probs[f"Alt {line}"] = under
            probs[f"Üst {line}"] = 1.0 - under
        return probs


def _actual_1x2(row: dict) -> str:
    if row["ft_home"] > row["ft_away"]:
        return "1"
    if row["ft_home"] == row["ft_away"]:
        return "0"
    return "2"


def backtest(history: list[dict], *, test_days: int = 14) -> dict:
    """Chronological holdout backtest using only information before cutoff."""
    if not history:
        return {"error": "no_history"}
    # A historical feed can retain isolated postponed fixtures under their new
    # future date. Pick the latest well-populated date rather than max(start_ts)
    # so one rescheduled match cannot move the holdout window into the future.
    date_counts = Counter(row["match_date"] for row in history)
    populated_dates = {day for day, count in date_counts.items() if count >= 20}
    candidates = [
        row["start_ts"] for row in history if row["match_date"] in populated_dates
    ]
    end_ts = max(candidates or [row["start_ts"] for row in history]) + 1
    cutoff = end_ts - test_days * 86400
    train = [row for row in history if row["start_ts"] < cutoff]
    test = [
        row
        for row in history
        if row["start_ts"] >= cutoff
        and is_daily_eligible(row.get("competition") or "")
    ]
    model = GoalModel(train, cutoff)

    bets = []
    market_favourite_bets = []
    brier_values = []
    for row in test:
        pred = model.predict(row["home"], row["away"], row.get("competition"))
        actual = _actual_1x2(row)
        brier_values.append(
            sum(
                (pred.probs[outcome] - (1.0 if actual == outcome else 0.0)) ** 2
                for outcome in ("1", "0", "2")
            )
        )
        candidates = []
        odds_1x2 = [row.get("odds_home"), row.get("odds_draw"), row.get("odds_away")]
        if all(odds_1x2):
            fair = probability.fair_probs(odds_1x2)
            for outcome, odd, market_p in zip(("1", "0", "2"), odds_1x2, fair):
                candidates.append((outcome, odd, pred.probs[outcome] - market_p))
            favourite_index = min(range(3), key=lambda i: odds_1x2[i])
            favourite_odd = odds_1x2[favourite_index]
            favourite_outcome = ("1", "0", "2")[favourite_index]
            if config.LEG_MIN_ODD <= favourite_odd <= config.LEG_MAX_ODD:
                won = favourite_outcome == actual
                market_favourite_bets.append(
                    favourite_odd - 1.0 if won else -1.0
                )
        odds_ou = [row.get("odds_under25"), row.get("odds_over25")]
        if all(odds_ou):
            fair = probability.fair_probs(odds_ou)
            candidates.extend(
                [
                    ("Alt 2.5", odds_ou[0], pred.probs["Alt 2.5"] - fair[0]),
                    ("Üst 2.5", odds_ou[1], pred.probs["Üst 2.5"] - fair[1]),
                ]
            )
        candidates = [
            c
            for c in candidates
            if config.LEG_MIN_ODD <= c[1] <= config.LEG_MAX_ODD
            and c[2] >= config.MODEL_MIN_EDGE
            and pred.confidence >= 0.35
        ]
        if not candidates:
            continue
        outcome, odd, edge = max(candidates, key=lambda c: c[2])
        if outcome in ("1", "0", "2"):
            won = outcome == actual
            predicted = pred.probs[outcome]
        else:
            total = row["ft_home"] + row["ft_away"]
            won = (outcome == "Alt 2.5" and total < 2.5) or (
                outcome == "Üst 2.5" and total > 2.5
            )
            predicted = pred.probs[outcome]
        bets.append(
            {
                "won": won,
                "odd": odd,
                "profit": odd - 1.0 if won else -1.0,
                "predicted": predicted,
                "edge": edge,
            }
        )

    profits = [bet["profit"] for bet in bets]
    roi = statistics.mean(profits) if profits else 0.0
    if len(profits) > 1:
        margin = 1.96 * statistics.stdev(profits) / math.sqrt(len(profits))
    else:
        margin = 0.0
    return {
        "train_matches": len(train),
        "test_matches": len(test),
        "bets": len(bets),
        "wins": sum(bet["won"] for bet in bets),
        "hit_rate": (sum(bet["won"] for bet in bets) / len(bets)) if bets else 0.0,
        "roi": roi,
        "roi_ci95": (roi - margin, roi + margin),
        "avg_odds": statistics.mean(bet["odd"] for bet in bets) if bets else 0.0,
        "avg_edge": statistics.mean(bet["edge"] for bet in bets) if bets else 0.0,
        "brier_1x2": statistics.mean(brier_values) if brier_values else None,
        "market_favourite_bets": len(market_favourite_bets),
        "market_favourite_roi": (
            statistics.mean(market_favourite_bets)
            if market_favourite_bets
            else 0.0
        ),
        "model_live_enabled": config.MODEL_LIVE_ENABLED,
    }
