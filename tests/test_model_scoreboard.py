"""How a model's score is reported next to the price it is betting against.

A Brier score is meaningless on its own. It has to be read against what the
price scored on the very matches the model disagreed about. The status screen
used to print the shadow models' Brier alone, a few lines under the
walk-forward's model/market pair, so the reader compared a number from one
sample with a number from another - and a model whose ROI confidence interval
sat entirely below zero read as if it were beating the market.
"""

from otomasyon import service


def _metrics(brier, market_brier):
    return {"brier": brier, "market_brier": market_brier}


def test_a_model_behind_the_price_is_reported_as_behind_it():
    text = service._brier_text(_metrics(0.2407, 0.2271))
    assert "0.2407" in text and "0.2271" in text
    assert "piyasa önde" in text


def test_a_model_ahead_of_the_price_is_reported_as_ahead():
    text = service._brier_text(_metrics(0.2271, 0.2407))
    assert "model önde" in text


def test_the_verdict_is_stated_rather_than_left_to_the_reader():
    """Both numbers on one line, with which of them won already worked out."""
    text = service._brier_text(_metrics(0.2523, 0.2478))
    assert "0.0045" in text, "fark okuyucuya cikartma yaptirmadan verilmeli"


def test_a_model_with_no_settled_bets_says_so():
    assert service._brier_text(_metrics(None, None)) == "Brier: veri yok"


def test_the_shadow_scoreboard_scores_the_price_on_the_same_bets(
    tmp_path, monkeypatch
):
    """The comparison must come from one sample, not two.

    The market's Brier is computed over exactly the rows the model's is
    computed over, so the two are answerable to the same matches.
    """
    from otomasyon import config
    from otomasyon.iddaa.normalize import NormalizedEvent
    from otomasyon.storage import Database

    def event(eid):
        return NormalizedEvent(
            event_id=eid,
            home=f"Ev{eid}",
            away=f"Dep{eid}",
            competition_id=-1,
            competition_name="Lig",
            country_code="TR",
            sport_id=1,
            start_ts=1_786_197_600,
            status=0,
            markets=[],
        )

    path = str(tmp_path / "shadow.db")
    with Database(path) as db:
        db.save_events([event(1), event(2)])
        db.conn.execute(
            """
            INSERT INTO model_predictions
                (model_version, for_date, event_id, market, outcome_name,
                 predicted_prob, market_fair, edge, odd, captured_ts, result)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("v", "2026-01-01", 1, "ou25", "Üst", 0.7, 0.5, 0.2, 2.0, 0, "lose"),
        )
        db.conn.execute(
            """
            INSERT INTO model_predictions
                (model_version, for_date, event_id, market, outcome_name,
                 predicted_prob, market_fair, edge, odd, captured_ts, result)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("v", "2026-01-02", 2, "ou25", "Üst", 0.9, 0.6, 0.3, 2.0, 0, "win"),
        )
        db.conn.commit()

    monkeypatch.setattr(config, "MODEL_MIN_EDGE", 0.0)
    m = service.shadow_model_metrics(path, "v")
    assert m["predictions"] == 2
    # Model: 0.7 on a loss and 0.9 on a win. Market: 0.5 and 0.6.
    assert m["brier"] == (0.7**2 + 0.1**2) / 2
    assert m["market_brier"] == (0.5**2 + 0.4**2) / 2
    assert "piyasa önde" in service._brier_text(m)
