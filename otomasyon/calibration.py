"""Leakage-safe empirical calibration for daily coupon market probabilities.

Two calibrators live here:

``MarketCalibrator`` shrinks each odds bucket toward the current market price
using outcomes observed before the evaluated period.

``PooledCalibration`` answers the question the daily coupon actually depends
on: given the market price *and* our own model, what is the best probability
we can state? It fits both as competing opinions on the log-odds scale, so the
weight each one earns is measured rather than assumed. If the model carries no
information the market lacks, its weight collapses toward zero and the layer
reproduces the market - which is the floor we want, not a failure. If the
model later earns an edge, the same fit hands it weight without a code change.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

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


def _logit(p: float) -> float:
    p = min(1.0 - 1e-9, max(1e-9, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    value = math.exp(x)
    return value / (1.0 + value)


def _fit_logistic(
    rows: list[tuple[list[float], int]],
    *,
    ridge: float = 1e-4,
    iterations: int = 25,
) -> list[float]:
    """Newton-fitted logistic regression over a handful of features.

    The feature count is tiny (intercept plus two opinions), so a dense solve
    each step is cheap and converges in a few iterations.
    """
    width = len(rows[0][0])
    beta = [0.0] * width
    for _ in range(iterations):
        gradient = [0.0] * width
        hessian = [[ridge if i == j else 0.0 for j in range(width)] for i in range(width)]
        for features, label in rows:
            p = _sigmoid(sum(b * x for b, x in zip(beta, features)))
            error = p - label
            weight = max(1e-6, p * (1.0 - p))
            for i, xi in enumerate(features):
                gradient[i] += error * xi
                for j, xj in enumerate(features):
                    hessian[i][j] += weight * xi * xj
        for i in range(width):
            gradient[i] += ridge * beta[i]
        step = _solve(hessian, gradient)
        if step is None:
            break
        beta = [b - s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-8:
            break
    return beta


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting."""
    size = len(vector)
    rows = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for other in range(size):
            if other == column:
                continue
            factor = rows[other][column] / rows[column][column]
            for k in range(column, size + 1):
                rows[other][k] -= factor * rows[column][k]
    return [rows[i][size] / rows[i][i] for i in range(size)]


def _logloss(probabilities, labels) -> float:
    values = list(zip(probabilities, labels))
    if not values:
        return 0.0
    return sum(
        -math.log(max(1e-12, p if y else 1.0 - p)) for p, y in values
    ) / len(values)


@dataclass
class PooledFit:
    """What one market family's calibration learned, and what it is worth."""

    market: str
    samples: int
    intercept: float
    market_weight: float
    model_weight: float
    market_logloss: float
    model_logloss: float
    pooled_logloss: float
    context_names: tuple[str, ...] = ()
    context_weights: tuple[float, ...] = ()
    context_logloss: float | None = None

    def blend(
        self,
        market_prob: float,
        model_prob: float | None,
        context: Sequence[float] | None = None,
    ) -> float:
        if model_prob is None:
            model_prob = market_prob
        score = (
            self.intercept
            + self.market_weight * _logit(market_prob)
            + self.model_weight * _logit(model_prob)
        )
        if context and self.context_weights:
            score += sum(
                weight * value
                for weight, value in zip(self.context_weights, context)
            )
        return _sigmoid(score)

    @property
    def model_contribution(self) -> float:
        """Log loss the model saves over the calibrated market alone.

        Positive means the model earned its place; zero or negative means the
        price already contained everything it knows.
        """
        return self.market_logloss - self.pooled_logloss

    @property
    def context_contribution(self) -> float | None:
        """Log loss the context features save on top of market and model."""
        if self.context_logloss is None:
            return None
        return self.pooled_logloss - self.context_logloss

    def as_dict(self) -> dict:
        return {
            "market": self.market,
            "samples": self.samples,
            "intercept": self.intercept,
            "market_weight": self.market_weight,
            "model_weight": self.model_weight,
            "market_logloss": self.market_logloss,
            "model_logloss": self.model_logloss,
            "pooled_logloss": self.pooled_logloss,
            "model_contribution": self.model_contribution,
            "context_names": list(self.context_names),
            "context_weights": list(self.context_weights),
            "context_logloss": self.context_logloss,
            "context_contribution": self.context_contribution,
        }


def fit_pooled(
    market_name: str,
    samples: list[tuple],
    *,
    context_names: Sequence[str] = (),
) -> PooledFit | None:
    """Fit one market family from ``(market_prob, model_prob, context, won)``.

    ``context`` is whatever the match itself told us - the weather at the
    ground, and anything later collected alongside it. It is fitted as a third
    opinion so its worth is measured on the same scale as the model's, and it
    earns weight only if the price did not already contain it.
    """
    if len(samples) < config.CALIBRATION_MIN_SAMPLES:
        return None
    labels = [row[-1] for row in samples]
    rows = [
        ([1.0, _logit(row[0]), _logit(row[1])], row[-1]) for row in samples
    ]
    intercept, market_weight, model_weight = _fit_logistic(rows)
    market_only = _fit_logistic([([1.0, f[1]], y) for f, y in rows])
    pooled_logloss = _logloss(
        [
            _sigmoid(intercept + market_weight * f[1] + model_weight * f[2])
            for f, _ in rows
        ],
        labels,
    )
    context_weights: tuple[float, ...] = ()
    context_logloss: float | None = None
    if context_names and len(samples[0]) == 4:
        wide = [
            (features + list(row[2]), label)
            for (features, label), row in zip(rows, samples)
        ]
        beta = _fit_logistic(wide)
        context_weights = tuple(beta[3:])
        context_logloss = _logloss(
            [_sigmoid(sum(b * x for b, x in zip(beta, f))) for f, _ in wide],
            labels,
        )
    return PooledFit(
        market=market_name,
        samples=len(samples),
        intercept=intercept,
        market_weight=market_weight,
        model_weight=model_weight,
        market_logloss=_logloss(
            [
                _sigmoid(market_only[0] + market_only[1] * f[1])
                for f, _ in rows
            ],
            labels,
        ),
        model_logloss=_logloss([row[1] for row in samples], labels),
        pooled_logloss=pooled_logloss,
        context_names=tuple(context_names),
        context_weights=context_weights,
        context_logloss=context_logloss,
    )


CALIBRATION_MARKETS = ("1x2", "ou25")
# What the match itself told us, beyond the two opinions about it. The
# orientation below says which way each feature pushes a given selection; the
# fitted weight decides whether it pushes at all.
CONTEXT_NAMES = ("yagmur", "ruzgar")
# More rain and more wind push a match one way; a selection that wins when the
# match goes that way carries +1, its opposite -1, and a selection the feature
# says nothing about carries 0.
_CONTEXT_ORIENTATION = {
    "1x2": {"1": 1.0, "0": 0.0, "2": -1.0},
    "ou25": {"Alt": -1.0, "Üst": 1.0},
}


def weather_context(
    weather: dict[tuple[str, str], tuple[float, float]] | None,
    team_key: str,
    match_date: str,
    orientation: float,
) -> tuple[float, ...] | None:
    """Rain and wind at the ground, oriented for one selection."""
    if not weather:
        return None
    observed = weather.get((team_key, match_date))
    if not observed:
        return None
    rain, wind = observed
    if rain is None or wind is None:
        return None
    return (
        orientation * math.log1p(max(0.0, rain)),
        orientation * (wind - 20.0) / 15.0,
    )


def calibration_samples(
    history: list[dict],
    model,
    weather: dict[tuple[str, str], tuple[float, float]] | None = None,
) -> dict[str, list[tuple]]:
    """Turn settled matches into ``(market_prob, model_prob, context, won)``.

    Every selection of a market family becomes its own row, because the layer
    calibrates a probability we might state about a *selection*, not about a
    match. A match with no weather on record is dropped from the fit rather
    than fed a zero, so an absent observation cannot read as a calm day.
    """
    from .eligibility import is_event_eligible

    samples: dict[str, list[tuple]] = defaultdict(list)
    for row in history:
        if row.get("ft_home") is None or row.get("ft_away") is None:
            continue
        if not is_event_eligible(
            row.get("competition") or "", row["home"], row["away"]
        ):
            continue
        prediction = model.predict(row["home"], row["away"], row.get("competition"))
        total = row["ft_home"] + row["ft_away"]

        def context(family: str, name: str) -> tuple[float, ...] | None:
            return weather_context(
                weather,
                row["home_key"],
                row["match_date"],
                _CONTEXT_ORIENTATION[family][name],
            )

        odds_1x2 = [row.get("odds_home"), row.get("odds_draw"), row.get("odds_away")]
        if all(odd and odd > 1.0 for odd in odds_1x2):
            fair = probability.fair_probs(odds_1x2)
            outcomes = (
                row["ft_home"] > row["ft_away"],
                row["ft_home"] == row["ft_away"],
                row["ft_home"] < row["ft_away"],
            )
            for name, market_prob, won in zip(("1", "0", "2"), fair, outcomes):
                found = context("1x2", name)
                if weather is not None and found is None:
                    continue
                samples["1x2"].append(
                    (market_prob, prediction.probs[name], found or (), int(won))
                )
        odds_ou = [row.get("odds_under25"), row.get("odds_over25")]
        if all(odd and odd > 1.0 for odd in odds_ou):
            fair = probability.fair_probs(odds_ou)
            over = prediction.probs["Üst 2.5"]
            for name, market_prob, model_prob, won in (
                ("Alt", fair[0], 1.0 - over, total < 2.5),
                ("Üst", fair[1], over, total > 2.5),
            ):
                found = context("ou25", name)
                if weather is not None and found is None:
                    continue
                samples["ou25"].append(
                    (market_prob, model_prob, found or (), int(won))
                )
    return dict(samples)


def fit_calibration(
    history: list[dict],
    cutoff_ts: int,
    weather: dict[tuple[str, str], tuple[float, float]] | None = None,
) -> dict:
    """Fit the layer on history before ``cutoff_ts`` and score it after.

    The model used for the fit only ever sees matches before the fit window,
    and the holdout that scores it is later still, so neither the coefficients
    nor the reported gain can borrow an outcome they were measured on.
    """
    from .model import GoalModel

    holdout_start = cutoff_ts - config.CALIBRATION_HOLDOUT_DAYS * 86400
    fit_start = holdout_start - config.CALIBRATION_FIT_DAYS * 86400
    fit_rows = [row for row in history if fit_start <= row["start_ts"] < holdout_start]
    holdout_rows = [
        row for row in history if holdout_start <= row["start_ts"] < cutoff_ts
    ]
    fit_model = GoalModel(
        [row for row in history if row["start_ts"] < fit_start], fit_start
    )
    holdout_model = GoalModel(
        [row for row in history if row["start_ts"] < holdout_start], holdout_start
    )
    fit_samples = calibration_samples(fit_rows, fit_model, weather)
    holdout_samples = calibration_samples(holdout_rows, holdout_model, weather)
    context_names = CONTEXT_NAMES if weather else ()

    fits: dict[str, dict] = {}
    for market in CALIBRATION_MARKETS:
        fitted = fit_pooled(
            market, fit_samples.get(market, []), context_names=context_names
        )
        if fitted is None:
            continue
        holdout = holdout_samples.get(market, [])
        if len(holdout) >= config.CALIBRATION_MIN_SAMPLES:
            labels = [row[-1] for row in holdout]
            market_only = PooledFit(
                market=market,
                samples=fitted.samples,
                intercept=fitted.intercept,
                market_weight=fitted.market_weight,
                model_weight=0.0,
                market_logloss=0.0,
                model_logloss=0.0,
                pooled_logloss=0.0,
            )
            scored = PooledFit(
                market=market,
                samples=len(holdout),
                intercept=fitted.intercept,
                market_weight=fitted.market_weight,
                model_weight=fitted.model_weight,
                market_logloss=_logloss(
                    [market_only.blend(row[0], None) for row in holdout], labels
                ),
                model_logloss=_logloss([row[1] for row in holdout], labels),
                pooled_logloss=_logloss(
                    [fitted.blend(row[0], row[1]) for row in holdout], labels
                ),
                context_names=fitted.context_names,
                context_weights=fitted.context_weights,
                context_logloss=(
                    _logloss(
                        [
                            fitted.blend(row[0], row[1], row[2])
                            for row in holdout
                        ],
                        labels,
                    )
                    if fitted.context_weights
                    else None
                ),
            )
            fits[market] = {
                "fit": fitted.as_dict(),
                "holdout": scored.as_dict(),
                "raw_market_logloss": _logloss(
                    [row[0] for row in holdout], labels
                ),
            }
        else:
            fits[market] = {"fit": fitted.as_dict(), "holdout": None}

    gains = [
        item["holdout"]["model_contribution"]
        for item in fits.values()
        if item.get("holdout")
    ]
    context_gains = [
        item["holdout"]["context_contribution"]
        for item in fits.values()
        if item.get("holdout")
        and item["holdout"]["context_contribution"] is not None
    ]
    return {
        "cutoff_ts": cutoff_ts,
        "fit_matches": len(fit_rows),
        "holdout_matches": len(holdout_rows),
        "markets": fits,
        "model_earns_weight": bool(gains)
        and min(gains) >= config.CALIBRATION_MIN_MODEL_GAIN,
        "context_earns_weight": bool(context_gains)
        and min(context_gains) >= config.CALIBRATION_MIN_MODEL_GAIN,
    }


def _market_family(market) -> str | None:
    if market.code == config.MARKET_MATCH_RESULT:
        return "1x2"
    if market.code == config.MARKET_OVER_UNDER and str(market.sov) == "2.5":
        return "ou25"
    return None


_MODEL_OUTCOME = {
    "1x2": {"1": "1", "0": "0", "2": "2"},
    "ou25": {"Alt": "Alt 2.5", "Üst": "Üst 2.5"},
}


class CalibratedProvider:
    """Coupon probabilities from the pooled market/model calibration.

    Returns ``None`` for any market the layer was not fitted on, which leaves
    the engine on its margin-free market price for that market.
    """

    def __init__(self, fits: dict[str, PooledFit], model):
        self.fits = fits
        self.model = model

    def __call__(self, event, market) -> list[float] | None:
        family = _market_family(market)
        fit = self.fits.get(family) if family else None
        if fit is None:
            return None
        odds = market.odds
        if not all(odd and odd > 1.0 for odd in odds):
            return None
        coverage = config.MARKET_OUTCOME_COVERAGE.get(market.code, 1)
        fair = probability.fair_probs(odds, coverage)
        prediction = self.model.predict(
            event.home, event.away, event.competition_name
        )
        blended = []
        for selection, market_prob in zip(market.selections, fair):
            key = _MODEL_OUTCOME[family].get(selection.name)
            model_prob = prediction.probs.get(key) if key else None
            blended.append(fit.blend(market_prob, model_prob))
        total = sum(blended)
        if total <= 0:
            return None
        return [value * coverage / total for value in blended]


def provider_from_report(report: dict, model) -> CalibratedProvider | None:
    """Build a provider only if the report says the model earned its weight."""
    if not report or not report.get("model_earns_weight"):
        return None
    fits = {}
    for market, item in (report.get("markets") or {}).items():
        values = item.get("holdout") or item.get("fit")
        if not values:
            continue
        fits[market] = PooledFit(
            market=market,
            samples=values["samples"],
            intercept=values["intercept"],
            market_weight=values["market_weight"],
            model_weight=values["model_weight"],
            market_logloss=values["market_logloss"],
            model_logloss=values["model_logloss"],
            pooled_logloss=values["pooled_logloss"],
        )
    return CalibratedProvider(fits, model) if fits else None
