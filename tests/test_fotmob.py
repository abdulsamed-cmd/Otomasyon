from datetime import date, datetime, timezone

from otomasyon import service
from otomasyon.fotmob import (
    FotMobClient,
    FotMobFixture,
    FotMobMatchContext,
    FotMobStarter,
    match_fixtures,
)
from otomasyon.storage import Database

from .test_engine import _ou_event


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Session:
    def get(self, url, params, timeout, headers):
        if url.endswith("/matches"):
            return Response(
                {
                    "leagues": [
                        {
                            "id": 1,
                            "name": "Lig",
                            "matches": [
                                {
                                    "id": 99,
                                    "leagueId": 1,
                                    "home": {"id": 10, "name": "Arsenal"},
                                    "away": {"id": 20, "name": "Chelsea"},
                                    "status": {
                                        "utcTime": "2026-08-08T12:00:00.000Z",
                                        "started": False,
                                        "finished": False,
                                        "cancelled": False,
                                    },
                                }
                            ],
                        }
                    ]
                }
            )
        if url.endswith("/teams"):
            return Response(
                {
                    "fixtures": {
                        "allFixtures": {
                            "fixtures": [
                                {
                                    "id": 98,
                                    "home": {"id": 10, "name": "Arsenal"},
                                    "away": {"id": 30, "name": "Other"},
                                    "tournament": {"leagueId": 1, "name": "Lig"},
                                    "status": {
                                        "utcTime": "2026-08-01T12:00:00.000Z",
                                        "started": True,
                                        "finished": True,
                                        "cancelled": False,
                                    },
                                }
                            ]
                        }
                    }
                }
            )
        return Response(
            {
                "general": {
                    "matchId": "99",
                    "started": True,
                    "finished": True,
                    "coverageLevel": "xG",
                },
                "content": {
                    "stats": {
                        "Periods": {
                            "All": {
                                "stats": [
                                    {
                                        "stats": [
                                            {
                                                "key": "expected_goals",
                                                "stats": ["1.2", "0.8"],
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                    "lineup": {
                        "homeTeam": {
                            "starters": [
                                {"id": i, "name": f"H{i}", "marketValue": i * 1000}
                                for i in range(1, 12)
                            ]
                        },
                        "awayTeam": {
                            "starters": [
                                {"id": i, "name": f"A{i}", "marketValue": i * 1000}
                                for i in range(12, 23)
                            ]
                        },
                    },
                },
            }
        )


def test_fotmob_client_parses_fixtures_xg_and_lineups():
    client = FotMobClient(session=Session())
    fixture = client.fetch_date(date(2026, 8, 8))[0]
    assert fixture.match_id == 99
    assert fixture.home == "Arsenal"
    context = client.fetch_context(99, captured_ts=123)
    assert (context.xg_home, context.xg_away) == (1.2, 0.8)
    assert context.lineup_available is True
    assert context.captured_ts == 123


def test_lineup_requires_unique_player_ids():
    starters = FotMobClient._starters(
        {"starters": [{"id": 1, "name": "A"}, {"id": 1, "name": "A"}]}
    )
    assert len(starters) == 1


def test_client_parses_recent_team_fixtures():
    fixture = FotMobClient(session=Session()).fetch_team_fixtures(10)[0]
    assert fixture.match_id == 98
    assert fixture.finished is True


def test_fixture_matcher_requires_name_and_time_agreement():
    event = _ou_event(100, 1.5, 2.6)
    event.home, event.away = "Arsenal FC", "Chelsea"
    event.start_ts = int(
        datetime(2026, 8, 8, 12, tzinfo=timezone.utc).timestamp()
    )
    fixture = FotMobFixture(
        99, 1, "Lig", 10, "Arsenal", 20, "Chelsea", event.start_ts,
        False, False, False,
    )
    links, diagnostics = match_fixtures([event], [fixture])
    assert links[100].match_id == 99
    assert diagnostics[0]["score"] == 1.0


def test_fotmob_fixture_and_context_captures_are_persisted():
    client = FotMobClient(session=Session())
    fixture = client.fetch_date(date(2026, 8, 8))[0]
    context = client.fetch_context(99, captured_ts=123)
    with Database(":memory:") as db:
        assert db.save_fotmob_fixtures([fixture], now=123) == 1
        assert db.save_fotmob_contexts([context]) == 1
        assert db.count("fotmob_fixtures") == 1
        assert db.count("fotmob_context_captures") == 1
        assert db.count("fotmob_lineup_players") == 22


def test_lineup_features_compare_only_with_prior_prematch_lineup():
    client = FotMobClient(session=Session())
    current_fixture = client.fetch_date(date(2026, 8, 8))[0]
    current = client.fetch_context(99, captured_ts=current_fixture.start_ts - 600)
    prior_fixture = FotMobFixture(
        98, 1, "Lig", 10, "Arsenal", 30, "Other",
        current_fixture.start_ts - 86400, False, True, False,
    )
    prior_players = [
        FotMobStarter(i, f"H{i}", None, i * 1000) for i in range(1, 12)
    ]
    prior = FotMobMatchContext(
        98, prior_fixture.start_ts - 600, False, False, "xG", None, None,
        True, 11, 11, prior_players, [
            FotMobStarter(i, f"O{i}", None, None) for i in range(30, 41)
        ],
    )
    with Database(":memory:") as db:
        db.save_fotmob_fixtures([prior_fixture, current_fixture], now=1)
        db.save_fotmob_contexts([prior, current])
        features = db.lineup_features(99, current.captured_ts)
    assert features["home"]["returning_starters"] == 11
    assert features["home"]["changes"] == 0
    assert features["away"]["returning_starters"] is None


def test_context_capture_is_rate_limited_between_bot_polls(tmp_path, monkeypatch):
    now = datetime(2026, 8, 8, 15, tzinfo=timezone.utc)
    event = _ou_event(100, 1.5, 2.6)
    event.home, event.away = "Arsenal", "Chelsea"
    event.start_ts = int(now.timestamp()) + 3600
    fixture = FotMobFixture(
        99, 1, "Lig", 10, "Arsenal", 20, "Chelsea", event.start_ts,
        False, False, False,
    )
    context = FotMobMatchContext(
        99, int(now.timestamp()), False, False, "xG", None, None,
        True, 11, 11,
    )

    class FakeClient:
        def fetch_date(self, day):
            return [fixture]

        def fetch_context(self, match_id, captured_ts=None):
            return context

        def fetch_team_fixtures(self, team_id):
            return []

    monkeypatch.setattr(
        service,
        "get_live_events",
        lambda: ([event], {1: {"name": "Test Lig", "country_code": "TR"}}),
    )
    db_path = str(tmp_path / "context.db")
    first = service.capture_fotmob_context(
        db_path, client=FakeClient(), now=now, force=True
    )
    second = service.capture_fotmob_context(
        db_path, client=FakeClient(), now=now
    )
    assert first["prematch_lineups"] == 1
    assert second["skipped"] is True
