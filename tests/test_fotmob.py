from datetime import date, datetime, timezone

from otomasyon import service
from otomasyon.fotmob import (
    FotMobClient,
    FotMobFixture,
    FotMobMatchContext,
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
                        "homeTeam": {"starters": [{}] * 11},
                        "awayTeam": {"starters": [{}] * 11},
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
