from otomasyon.calibration import MarketCalibrator
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
