from otomasyon import config
from otomasyon.clubelo import ClubEloClient, ClubEloFixture, compare_to_iddaa
from otomasyon.iddaa.normalize import (
    NormalizedEvent,
    NormalizedMarket,
    NormalizedSelection,
)


class Response:
    text = (
        "Date,Country,Home,Away,GD<-5,GD=-5,GD=-4,GD=-3,GD=-2,GD=-1,"
        "GD=0,GD=1,GD=2,GD=3,GD=4,GD=5,GD>5\n"
        "2026-08-08,ENG,Arsenal,Chelsea,0,0,0.01,0.02,0.08,0.19,"
        "0.25,0.25,0.12,0.05,0.02,0.01,0\n"
    )

    def raise_for_status(self):
        pass


class Session:
    def get(self, url, timeout):
        return Response()


def test_parses_clubelo_goal_difference_probabilities():
    fixtures = ClubEloClient(session=Session()).fetch_fixtures()
    assert len(fixtures) == 1
    fixture = fixtures[0]
    assert abs(fixture.p_home + fixture.p_draw + fixture.p_away - 1.0) < 1e-9
    assert fixture.p_home > fixture.p_away


def test_compares_clubelo_to_iddaa_without_enabling_live_model():
    event = NormalizedEvent(
        event_id=1,
        home="Arsenal",
        away="Chelsea FC",
        competition_id=1,
        competition_name="Premier Lig",
        country_code="GB",
        sport_id=1,
        start_ts=1786183200,  # 2026-08-08 local date
        status=0,
        markets=[
            NormalizedMarket(
                1,
                *config.MARKET_MATCH_RESULT,
                "Maç Sonucu",
                None,
                1,
                [
                    NormalizedSelection(1, "1", 2.2),
                    NormalizedSelection(2, "0", 3.2),
                    NormalizedSelection(3, "2", 3.4),
                ],
            )
        ],
    )
    fixture = ClubEloFixture("2026-08-08", "Arsenal", "Chelsea", 0.60, 0.23, 0.17)
    report = compare_to_iddaa([event], [fixture])
    assert report["matched"] == 1
    assert report["reports"][0]["outcome"] == "1"
    assert report["reports"][0]["difference"] > 0
    assert report["live_enabled"] is False
