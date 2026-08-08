from otomasyon.understat import (
    UnderstatClient,
    UnderstatMatch,
    match_understat_history,
)
from otomasyon.results.mackolik import SourceMatch
from otomasyon.storage import Database


class Response:
    def __init__(self, payload=None):
        self.payload = payload or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Session:
    def get(self, url, headers, timeout):
        if "getLeagueData" not in url:
            return Response()
        return Response(
            {
                "dates": [
                    {
                        "id": "123",
                        "isResult": True,
                        "h": {"title": "Arsenal"},
                        "a": {"title": "Chelsea"},
                        "goals": {"h": "2", "a": "1"},
                        "xG": {"h": "1.75", "a": "0.92"},
                        "datetime": "2026-03-10 20:00:00",
                    },
                    {"id": "future", "isResult": False},
                ]
            }
        )


def test_understat_client_parses_completed_xg_matches():
    matches = UnderstatClient(session=Session()).fetch_league("EPL", 2025)
    assert len(matches) == 1
    assert matches[0].source_id == "understat:123"
    assert (matches[0].xg_home, matches[0].xg_away) == (1.75, 0.92)


def test_understat_matcher_requires_score_teams_and_time():
    source = UnderstatMatch(
        "understat:123", "EPL", 2025, 10000,
        "Arsenal", "Chelsea", 2, 1, 1.75, 0.92,
    )
    target = {
        "source_id": "mackolik:1",
        "start_ts": 10060,
        "home": "Arsenal FC",
        "away": "Chelsea",
        "ft_home": 2,
        "ft_away": 1,
    }
    links, diagnostics = match_understat_history(
        [source.__dict__], [target]
    )
    assert links[0]["historical_source_id"] == "mackolik:1"
    assert diagnostics[0]["method"] == "team_time_score"


def test_linked_xg_is_joined_into_model_history():
    source = UnderstatMatch(
        "understat:123", "EPL", 2025, 10000,
        "Arsenal", "Chelsea", 2, 1, 1.75, 0.92,
    )
    historical = SourceMatch(
        "mackolik:1", 1, "Arsenal", "Chelsea", 10060,
        "post", "fullTime", 2, 1, 1, 0,
        competition_name="Premier League",
    )
    with Database(":memory:") as db:
        db.save_historical_matches([historical])
        db.save_understat_matches([source])
        links, _ = match_understat_history(
            db.load_understat_matches(), db.load_historical_matches()
        )
        db.save_historical_xg_links(links)
        row = db.load_historical_matches()[0]
    assert row["xg_home"] == 1.75
    assert row["xg_away"] == 0.92
