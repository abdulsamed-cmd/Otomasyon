from otomasyon import service
from otomasyon.settlement import MatchResult
from otomasyon.storage import Database

from .test_engine import NOW, _ou_event


def test_shadow_xg_predictions_are_isolated_persisted_and_settled(tmp_path):
    path = str(tmp_path / "shadow.db")
    event = _ou_event(900, 1.70, 2.10)
    event.home, event.away = "Alpha", "Beta"
    now_ts = int(NOW.timestamp())
    with Database(path) as db:
        db.upsert_competitions(
            {1: {"name": "Test Lig", "country_code": "TR"}}
        )
        db.save_events([event], now=now_ts)
        for index in range(8):
            start_ts = now_ts - (index + 1) * 86400
            historical_id = f"h:{index}"
            xg_id = f"u:{index}"
            db.conn.execute(
                """
                INSERT INTO historical_matches
                    (source_id, start_ts, match_date, competition, home, away,
                     home_key, away_key, ft_home, ft_away)
                VALUES (?, ?, '2026-08-01', 'Test Lig', 'Alpha', 'Beta',
                        'alpha', 'beta', 1, 1)
                """,
                (historical_id, start_ts),
            )
            db.conn.execute(
                """
                INSERT INTO understat_matches
                    (source_id, league, season, start_ts, home, away,
                     ft_home, ft_away, xg_home, xg_away)
                VALUES (?, 'EPL', 2025, ?, 'Alpha', 'Beta', 1, 1, 0.8, 1.6)
                """,
                (xg_id, start_ts),
            )
            db.conn.execute(
                """
                INSERT INTO historical_xg_links
                    (understat_source_id, historical_source_id, match_score)
                VALUES (?, ?, 1.0)
                """,
                (xg_id, historical_id),
            )
        db.conn.commit()

    captured = service.capture_shadow_predictions(
        path, events=[event], now=NOW
    )
    assert captured["eligible"] == 1
    assert captured["saved"] == 1
    with Database(path) as db:
        prediction = db.conn.execute(
            "SELECT * FROM model_predictions"
        ).fetchone()
        db.conn.execute(
            "UPDATE model_predictions SET edge=0.10 WHERE id=?",
            (prediction["id"],),
        )
        db.conn.commit()
    if prediction["outcome_name"] == "Alt":
        result = MatchResult(900, 1, 0)
    else:
        result = MatchResult(900, 2, 1)
    service.record_result(path, result)
    assert service.settle_shadow_predictions(path) == 1
    metrics = service.shadow_model_metrics(path)
    assert metrics["predictions"] == 1
    assert metrics["wins"] == 1
    assert metrics["live_enabled"] is False
