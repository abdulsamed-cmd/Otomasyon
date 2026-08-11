from types import SimpleNamespace

import pytest

from otomasyon import config, probability
from otomasyon.calibration import (
    MarketCalibrator,
    fit_pooled,
    provider_from_report,
)
from otomasyon.iddaa.normalize import (
    NormalizedEvent,
    NormalizedMarket,
    NormalizedSelection,
)
from otomasyon.replay import historical_events


def _row(under_won: bool) -> dict:
    return {
        "home": "A",
        "away": "B",
        "competition": "Lig",
        "start_ts": 1,
        "ft_home": 1 if under_won else 3,
        "ft_away": 0,
        "ht_home": 0,
        "ht_away": 0,
        "odds_home": None,
        "odds_draw": None,
        "odds_away": None,
        "odds_under25": 1.50,
        "odds_over25": 2.60,
    }


def test_calibrator_learns_historical_market_bias_and_normalizes():
    history = [_row(True) for _ in range(20)] + [_row(False) for _ in range(2)]
    calibrator = MarketCalibrator(history, prior_strength=1)
    events, _ = historical_events([_row(True)])
    probs = calibrator(events[0], events[0].markets[0])
    assert abs(sum(probs) - 1.0) < 1e-9
    assert probs[0] > probs[1]


def _pooled_samples(market_weight: float, model_weight: float, n: int = 4000):
    """Selections whose outcomes follow a known market/model mixture."""
    import math
    import random

    rnd = random.Random(11)
    rows = []
    for _ in range(n):
        market = rnd.uniform(0.15, 0.85)
        model = rnd.uniform(0.15, 0.85)
        logit = (
            market_weight * math.log(market / (1 - market))
            + model_weight * math.log(model / (1 - model))
        )
        truth = 1 / (1 + math.exp(-logit))
        rows.append((market, model, int(rnd.random() < truth)))
    return rows


def test_pooled_fit_ignores_a_model_that_only_repeats_the_price():
    fit = fit_pooled("ou25", _pooled_samples(1.0, 0.0))
    assert fit is not None
    assert abs(fit.model_weight) < 0.15
    assert fit.model_contribution < 0.01
    # With no model weight the layer must reproduce the market probability.
    assert fit.blend(0.44, 0.90) == pytest.approx(0.44, abs=0.03)


def test_pooled_fit_gives_weight_to_a_model_that_knows_something():
    fit = fit_pooled("ou25", _pooled_samples(0.6, 0.8))
    assert fit.model_weight > 0.4
    assert fit.model_contribution > 0.01
    assert fit.blend(0.44, 0.90) > 0.55


def test_provider_stays_out_until_the_model_earns_weight():
    report = {"model_earns_weight": False, "markets": {}}
    assert provider_from_report(report, object()) is None


def test_provider_blends_market_and_model_once_weight_is_earned():
    report = {
        "model_earns_weight": True,
        "markets": {
            "ou25": {
                "holdout": {
                    "samples": 5000,
                    "intercept": 0.0,
                    "market_weight": 0.6,
                    "model_weight": 0.8,
                    "market_logloss": 0.68,
                    "model_logloss": 0.66,
                    "pooled_logloss": 0.65,
                }
            }
        },
    }

    class _Model:
        def predict(self, home, away, competition=None):
            return SimpleNamespace(probs={"Alt 2.5": 0.30, "Üst 2.5": 0.70})

    provider = provider_from_report(report, _Model())
    events, _ = historical_events([_row(True)])
    event = events[0]
    market = event.markets[0]
    probs = provider(event, market)
    assert probs is not None
    assert sum(probs) == pytest.approx(1.0)
    market_fair = probability.fair_probs(market.odds)
    # The market alone prices "Alt" as the favourite; the model disagrees, so
    # the blend has to sit between the two opinions.
    assert probs[1] > market_fair[1]
    assert probs[1] < 0.70


def test_provider_leaves_markets_it_was_not_fitted_on_alone():
    report = {
        "model_earns_weight": True,
        "markets": {
            "ou25": {
                "holdout": {
                    "samples": 5000,
                    "intercept": 0.0,
                    "market_weight": 1.0,
                    "model_weight": 0.5,
                    "market_logloss": 0.68,
                    "model_logloss": 0.66,
                    "pooled_logloss": 0.65,
                }
            }
        },
    }
    provider = provider_from_report(report, object())
    event = _btts_event()
    assert provider(event, event.markets[0]) is None


def _btts_event() -> NormalizedEvent:
    return NormalizedEvent(
        event_id=1,
        home="A",
        away="B",
        competition_id=1,
        competition_name="Lig",
        country_code="TR",
        sport_id=1,
        start_ts=1,
        status=0,
        markets=[
            NormalizedMarket(
                1,
                config.MARKET_BTTS[0],
                config.MARKET_BTTS[1],
                "Karşılıklı Gol",
                None,
                1,
                [
                    NormalizedSelection(1, "Var", 1.85),
                    NormalizedSelection(2, "Yok", 1.85),
                ],
            )
        ],
    )
