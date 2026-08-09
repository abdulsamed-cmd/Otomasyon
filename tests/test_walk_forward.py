from datetime import datetime, timedelta

from otomasyon import config, service
from otomasyon.storage import Database


def _insert_match(db, index, start, *, target=False):
    source_id = f"history:{index}"
    xg_id = f"understat:{index}"
    db.conn.execute(
        """
        INSERT INTO historical_matches
            (source_id, start_ts, match_date, competition, home, away,
             home_key, away_key, ft_home, ft_away,
             odds_under25, odds_over25)
        VALUES (?, ?, ?, 'Test Lig', 'Alpha', 'Beta', 'alpha', 'beta',
                2, 1, ?, ?)
        """,
        (
            source_id,
            int(start.timestamp()),
            start.strftime("%Y-%m-%d"),
            1.90 if target else None,
            1.80 if target else None,
        ),
    )
    db.conn.execute(
        """
        INSERT INTO understat_matches
            (source_id, league, season, start_ts, home, away,
             ft_home, ft_away, xg_home, xg_away)
        VALUES (?, 'EPL', 2025, ?, 'Alpha', 'Beta', 2, 1, 1.8, 1.1)
        """,
        (xg_id, int(start.timestamp())),
    )
    db.conn.execute(
        """
        INSERT INTO historical_xg_links
            (understat_source_id, historical_source_id, match_score)
        VALUES (?, ?, 1.0)
        """,
        (xg_id, source_id),
    )


def test_walk_forward_predictions_use_only_prior_days_and_are_idempotent(tmp_path):
    path = str(tmp_path / "walk.db")
    base = datetime(2026, 1, 1, 12, tzinfo=config.TIMEZONE)
    with Database(path) as db:
        for index in range(8):
            _insert_match(db, index, base + timedelta(days=index))
        _insert_match(
            db,
            100,
            datetime(2026, 2, 1, 15, tzinfo=config.TIMEZONE),
            target=True,
        )
        _insert_match(
            db,
            101,
            datetime(2026, 2, 2, 15, tzinfo=config.TIMEZONE),
            target=True,
        )
        db.conn.commit()

    first = service.build_walk_forward_archive(
        path, start_date="2026-02-01", end_date="2026-02-02"
    )
    assert first["saved"] == 2
    with Database(path) as db:
        rows = db.load_walk_forward_predictions(
            config.MODEL_WALK_FORWARD_VERSION
        )
    assert rows[0]["cutoff_ts"] < int(
        datetime(2026, 2, 1, 15, tzinfo=config.TIMEZONE).timestamp()
    )
    assert rows[0]["xg_samples_home"] == 8

    second = service.build_walk_forward_archive(
        path, start_date="2026-02-01", end_date="2026-02-02"
    )
    assert second["saved"] == 0
    metrics = service.walk_forward_metrics(path, min_edge=-1.0)
    assert metrics["all_predictions"] == 2
    assert metrics["qualified_predictions"] == 2
    assert metrics["model_brier"] is not None
    assert metrics["market_brier"] is not None
    status = service.model_status_text(path)
    assert "Walk-forward (xg-ou-v1-wf)" in status
    assert "Brier model/piyasa" in status
