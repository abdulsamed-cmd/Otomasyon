"""Leakage-safe empirical calibration for daily coupon market probabilities."""

from __future__ import annotations

from collections import defaultdict

from . import config, probability


def _bucket(odd: float) -> int:
    return int(odd * 10)


class MarketCalibrator:
    """Calibrate 1X2 and O/U probabilities from matches before a replay period.

    Each market/outcome/0.10-odds bucket is shrunk toward the current market's
    margin-free probability. This corrects systematic favourite/market bias
    without using any result from the evaluated period.
    """

    def __init__(self, history: list[dict], *, prior_strength: float = 100.0):
        self.prior_strength = prior_strength
        self.groups = defaultdict(lambda: [0, 0])
        for row in history:
            self._add_row(row)

    def _add_market(self, market: str, names: list[str], odds: list, actual: int) -> None:
        if not all(odd is not None and odd > 1.0 for odd in odds):
            return
        for index, (name, odd) in enumerate(zip(names, odds)):
            group = self.groups[(market, name, _bucket(odd))]
            group[0] += 1
            group[1] += index == actual

    def _add_row(self, row: dict) -> None:
        actual_1x2 = (
            0
            if row["ft_home"] > row["ft_away"]
            else 1
            if row["ft_home"] == row["ft_away"]
            else 2
        )
        self._add_market(
            "1x2",
            ["1", "0", "2"],
            [row.get("odds_home"), row.get("odds_draw"), row.get("odds_away")],
            actual_1x2,
        )
        actual_ou = 0 if row["ft_home"] + row["ft_away"] < 2.5 else 1
        self._add_market(
            "ou25",
            ["Alt", "Üst"],
            [row.get("odds_under25"), row.get("odds_over25")],
            actual_ou,
        )

    def __call__(self, event, market) -> list[float] | None:
        if market.code == config.MARKET_MATCH_RESULT:
            market_key = "1x2"
        elif market.code == config.MARKET_OVER_UNDER and str(market.sov) == "2.5":
            market_key = "ou25"
        else:
            return None
        odds = market.odds
        if not all(odd is not None and odd > 1.0 for odd in odds):
            return None
        fair = probability.fair_probs(odds)
        calibrated = []
        for selection, odd, prior in zip(market.selections, odds, fair):
            total, wins = self.groups[
                (market_key, selection.name, _bucket(odd))
            ]
            calibrated.append(
                (wins + self.prior_strength * prior)
                / (total + self.prior_strength)
            )
        normalizer = sum(calibrated)
        return [value / normalizer for value in calibrated]
