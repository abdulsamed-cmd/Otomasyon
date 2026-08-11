import pytest

from otomasyon import config
from otomasyon.calibration import CONTEXT_NAMES, fit_pooled, weather_context
from otomasyon.storage import Database
from otomasyon.weather import DayWeather, OpenMeteoClient, Venue, venue_from_match_details


def _details(lat, lon):
    return {
        "content": {
            "matchFacts": {
                "infoBox": {
                    "Stadium": {
                        "name": "Test Arena",
                        "city": "Testville",
                        "country": "Testland",
                        "lat": lat,
                        "long": lon,
                    }
                }
            }
        }
    }


def test_venue_comes_out_of_a_match_details_payload():
    venue = venue_from_match_details("testteam", _details(51.5, -0.13), 42)
    assert venue == Venue(
        team_key="testteam",
        latitude=51.5,
        longitude=-0.13,
        stadium="Test Arena",
        city="Testville",
        country="Testland",
        source_match=42,
    )


def test_a_match_without_coordinates_yields_no_venue():
    assert venue_from_match_details("testteam", _details(None, None), 42) is None
    assert venue_from_match_details("testteam", {}, 42) is None


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.responses.pop(0)


DAILY = {
    "daily": {
        "time": ["2026-08-11", "2026-08-12"],
        "precipitation_sum": [0.0, 4.2],
        "wind_speed_10m_max": [12.0, 31.0],
    }
}


def test_archive_returns_one_row_per_day():
    client = OpenMeteoClient(session=_Session([_Response(payload=DAILY)]))
    venue = Venue("testteam", 51.5, -0.13)
    rows = client.archive(venue, "2026-08-11", "2026-08-12")
    assert rows == [
        DayWeather("testteam", "2026-08-11", 0.0, 12.0),
        DayWeather("testteam", "2026-08-12", 4.2, 31.0),
    ]


def test_a_rate_limited_venue_is_waited_for_not_dropped(monkeypatch):
    monkeypatch.setattr("otomasyon.weather.time.sleep", lambda _: None)
    session = _Session([_Response(status_code=429), _Response(payload=DAILY)])
    client = OpenMeteoClient(session=session)
    rows = client.archive(Venue("testteam", 51.5, -0.13), "2026-08-11", "2026-08-12")
    assert session.calls == 2
    assert len(rows) == 2


def test_giving_up_on_a_venue_is_an_error_not_a_silent_gap(monkeypatch):
    monkeypatch.setattr("otomasyon.weather.time.sleep", lambda _: None)
    session = _Session([_Response(status_code=429)] * config.WEATHER_RETRIES)
    client = OpenMeteoClient(session=session)
    with pytest.raises(RuntimeError, match="Open-Meteo"):
        client.archive(Venue("testteam", 51.5, -0.13), "2026-08-11", "2026-08-12")


def test_venue_and_weather_survive_a_round_trip(tmp_path):
    path = str(tmp_path / "test.db")
    with Database(path) as db:
        db.save_venues([Venue("testteam", 51.5, -0.13, "Test Arena")])
        db.save_venue_weather([DayWeather("testteam", "2026-08-11", 4.2, 31.0)])
    with Database(path) as db:
        assert db.load_venues()["testteam"]["stadium"] == "Test Arena"
        assert db.load_venue_weather() == {("testteam", "2026-08-11"): (4.2, 31.0)}


def test_a_day_with_a_missing_reading_is_not_offered_as_a_calm_one(tmp_path):
    path = str(tmp_path / "test.db")
    with Database(path) as db:
        db.save_venues([Venue("testteam", 51.5, -0.13)])
        db.save_venue_weather([DayWeather("testteam", "2026-08-11", None, 31.0)])
        assert db.load_venue_weather() == {}


def test_context_points_opposite_ways_for_opposite_selections():
    weather = {("testteam", "2026-08-11"): (4.2, 31.0)}
    over = weather_context(weather, "testteam", "2026-08-11", 1.0)
    under = weather_context(weather, "testteam", "2026-08-11", -1.0)
    assert over is not None
    assert under == tuple(-value for value in over)
    assert weather_context(weather, "otherteam", "2026-08-11", 1.0) is None
    assert weather_context(None, "testteam", "2026-08-11", 1.0) is None


def test_context_that_predicts_nothing_is_measured_as_worthless():
    # Weather drawn independently of the result: the fit may hand it a small
    # weight, but it must not claim to have saved any log loss.
    samples = [
        (0.5, 0.5, (0.1 * (index % 7), -0.1 * (index % 5)), index % 2)
        for index in range(config.CALIBRATION_MIN_SAMPLES * 2)
    ]
    fitted = fit_pooled("ou25", samples, context_names=CONTEXT_NAMES)
    assert fitted is not None
    assert len(fitted.context_weights) == len(CONTEXT_NAMES)
    assert fitted.context_contribution == pytest.approx(0.0, abs=5e-4)


def test_context_that_predicts_the_result_is_measured_as_worth_something():
    samples = [
        (0.5, 0.5, (1.0 if index % 2 else -1.0, 0.0), index % 2)
        for index in range(config.CALIBRATION_MIN_SAMPLES * 2)
    ]
    fitted = fit_pooled("ou25", samples, context_names=CONTEXT_NAMES)
    assert fitted is not None
    assert fitted.context_weights[0] > 0.5
    assert fitted.context_contribution > 0.1
