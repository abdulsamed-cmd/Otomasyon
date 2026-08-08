from otomasyon.model import GoalModel, backtest
from otomasyon.results.matcher import normalize_team


def _row(i, home="Alpha", away="Beta", score=(3, 0), odds=True):
    return {
        "source_id": str(i),
        "iddaa_code": i,
        "start_ts": 1_700_000_000 + i * 86400,
        "match_date": "2024-01-01",
        "competition": "Test Lig",
        "home": home,
        "away": away,
        "home_key": normalize_team(home),
        "away_key": normalize_team(away),
        "ft_home": score[0],
        "ft_away": score[1],
        "ht_home": 1,
        "ht_away": 0,
        "odds_home": 1.80 if odds else None,
        "odds_draw": 3.50 if odds else None,
        "odds_away": 4.50 if odds else None,
        "odds_under25": 2.10 if odds else None,
        "odds_over25": 1.70 if odds else None,
    }


def test_goal_model_learns_team_strength_and_valid_probabilities():
    history = [_row(i) for i in range(20)]
    cutoff = history[-1]["start_ts"] + 86400
    model = GoalModel(history, cutoff)
    pred = model.predict("Alpha", "Beta")

    assert pred.home_lambda > pred.away_lambda
    assert pred.probs["1"] > pred.probs["2"]
    assert pred.elo_home > pred.elo_away
    assert abs(sum(pred.probs[x] for x in ("1", "0", "2")) - 1.0) < 1e-9
    assert abs(pred.probs["Var"] + pred.probs["Yok"] - 1.0) < 1e-9
    assert pred.samples_home == 20 and pred.samples_away == 20


def test_chronological_backtest_uses_holdout_and_reports_roi():
    history = [_row(i) for i in range(45)]
    report = backtest(history, test_days=10)

    assert report["train_matches"] < len(history)
    assert report["test_matches"] > 0
    assert report["bets"] > 0
    assert report["wins"] == report["bets"]
    assert report["roi"] > 0
    assert report["brier_1x2"] is not None
    assert report["model_live_enabled"] is False
    assert report["gate_passed"] is False  # fewer than required 200 bets


def test_friendlies_are_not_used_for_training_or_test():
    history = [_row(i) for i in range(20)]
    history[0]["competition"] = "Kulüplerarası Hazırlık Maçlar"
    cutoff = history[-1]["start_ts"] + 86400
    model = GoalModel(history, cutoff)
    assert len(model.history) == 19


def test_isolated_future_reschedule_does_not_move_holdout_window():
    history = [_row(i) for i in range(45)]
    outlier = _row(999)
    outlier["match_date"] = "2030-01-01"
    history.append(outlier)
    report = backtest(history, test_days=10)
    assert report["test_matches"] > 1
