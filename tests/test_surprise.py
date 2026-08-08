from datetime import datetime
from math import comb

from otomasyon import config, service, surprise
from otomasyon.iddaa.normalize import (
    NormalizedEvent,
    NormalizedMarket,
    NormalizedSelection,
)
from otomasyon.settlement import MatchResult
from otomasyon.storage import Database
from otomasyon.results.mackolik import SourceMatch

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=config.TIMEZONE)
START = int(NOW.timestamp()) + 3600


def _htft_event(eid: int, odd_12: float, odd_21: float) -> NormalizedEvent:
    m = NormalizedMarket(
        eid * 10, config.MARKET_HTFT[0], config.MARKET_HTFT[1],
        "1. Yarı / Maç Sonucu", None, 1,
        [
            NormalizedSelection(1, "1/1", 3.0),
            NormalizedSelection(3, "1/2", odd_12),
            NormalizedSelection(7, "2/1", odd_21),
        ],
    )
    return _wrap(eid, [m])


def _goals_event(eid: int, odd_6plus: float) -> NormalizedEvent:
    m = NormalizedMarket(
        eid * 10, config.MARKET_TOTAL_GOALS_BAND[0], config.MARKET_TOTAL_GOALS_BAND[1],
        "Toplam Gol", None, 1,
        [
            NormalizedSelection(1, "0-1 gol", 3.2),
            NormalizedSelection(2, "2-3 gol", 1.9),
            NormalizedSelection(3, "4-5 gol", 4.5),
            NormalizedSelection(4, "6+ gol", odd_6plus),
        ],
    )
    return _wrap(eid, [m])


def _wrap(eid: int, markets) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=eid, home=f"H{eid}", away=f"A{eid}", competition_id=1,
        competition_name="Lig", country_code="TR", sport_id=1,
        start_ts=START, status=0, markets=markets,
    )


def test_finds_and_ranks_candidates():
    events = [
        _htft_event(1, odd_12=9.0, odd_21=15.0),   # 1/2 more likely than event 2
        _htft_event(2, odd_12=13.0, odd_21=21.0),
        _goals_event(3, odd_6plus=7.0),
    ]
    report = surprise.build_surprise(events, now=NOW)

    assert report.has_candidates
    twelve = report.by_category["htft_12"]
    assert [c.event_id for c in twelve] == [1, 2]  # lower odd (event 1) ranks first
    assert all(c.outcome_name == "1/2" for c in twelve)

    goals = report.by_category["goals_6plus"]
    assert len(goals) == 1 and goals[0].outcome_name == "6+ gol"


def test_system_set_has_distinct_matches():
    events = [_htft_event(i, 8.0 + i, 16.0 + i) for i in range(1, 5)]
    events.append(_goals_event(9, 6.5))
    report = surprise.build_surprise(events, now=NOW)
    ids = [c.event_id for c in report.system_set]
    assert len(ids) == len(set(ids))  # no match repeated


def test_surprise_excludes_friendlies():
    event = _goals_event(99, 6.5)
    event.competition_name = "Kulüplerarası Hazırlık Maçları"
    report = surprise.build_surprise([event], now=NOW)
    assert report.has_candidates is False


def test_surprise_excludes_reserve_teams_in_official_leagues():
    event = _goals_event(100, 6.5)
    event.away = "Brann 2"
    report = surprise.build_surprise([event], now=NOW)
    assert report.has_candidates is False


def test_system_scenarios_columns_and_cost():
    scenarios = surprise.system_scenarios(6, unit_stake=20.0)
    assert [s.size for s in scenarios] == [2, 3, 4, 5, 6]
    by_size = {s.size: s for s in scenarios}
    assert by_size[3].columns == comb(6, 3) == 20
    assert by_size[3].min_cost == 20 * 20.0
    assert by_size[6].columns == 1  # full combine


def test_surprise_reports_settle_separately_with_system_roi(tmp_path):
    events = [
        _htft_event(1, 9.0, 15.0),
        _htft_event(2, 10.0, 16.0),
        _goals_event(3, 7.0),
    ]
    report = surprise.build_surprise(events, now=NOW)
    path = str(tmp_path / "surprise.db")
    with Database(path) as db:
        db.upsert_competitions(
            {1: {"name": "Lig", "country_code": "TR"}}
        )
        db.save_events(events, now=int(NOW.timestamp()))
        report_id = db.save_surprise_report(
            report, "2026-W32", now=int(NOW.timestamp())
        )
        db.save_result(MatchResult(1, 2, 3, 1, 0))
        db.save_result(MatchResult(2, 2, 3, 1, 0))
        db.save_result(MatchResult(3, 4, 2, 2, 1))
    assert service.settle_surprise_reports(path) == 1
    with Database(path) as db:
        status = db.conn.execute(
            "SELECT status FROM surprise_reports WHERE id=?", (report_id,)
        ).fetchone()["status"]
        scenario = db.conn.execute(
            """
            SELECT roi FROM surprise_scenarios
            WHERE report_id=? AND system_size=2
            """,
            (report_id,),
        ).fetchone()
    assert status == "settled"
    assert scenario["roi"] > 0
    split = service.metrics_by_kind(path)
    assert split["surprise"]["coupons"] == 1
    assert split["surprise"]["gate_passed"] is False


def test_auto_results_includes_pending_surprise_candidates(tmp_path):
    events = [_htft_event(1, 9.0, 15.0), _goals_event(3, 7.0)]
    report = surprise.build_surprise(events, now=NOW)
    path = str(tmp_path / "auto-surprise.db")
    with Database(path) as db:
        db.upsert_competitions(
            {1: {"name": "Lig", "country_code": "TR"}}
        )
        db.save_events(events, now=int(NOW.timestamp()))
        db.save_surprise_report(report, "2026-W32", now=int(NOW.timestamp()))

    class Results:
        def fetch_date(self, day):
            return [
                SourceMatch(
                    "one", 1, "H1", "A1", START, "post", "fullTime",
                    2, 3, 1, 0,
                ),
                SourceMatch(
                    "three", 3, "H3", "A3", START, "post", "fullTime",
                    4, 2, 2, 1,
                ),
            ]

    outcome = service.auto_results(
        path, force=True, result_client=Results()
    )
    assert outcome["matched"] == 2
    assert outcome["surprise_settled"] == 1
