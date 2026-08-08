from datetime import datetime, time, timedelta

from otomasyon import config, service
from otomasyon.results.mackolik import SourceMatch
from otomasyon.storage import Database


class ArchiveClient:
    def __init__(self, failing=None):
        self.failing = set(failing or [])
        self.days = []

    def fetch_archive_date(self, day):
        self.days.append(day)
        if day in self.failing:
            raise RuntimeError("temporary archive failure")
        start = datetime.combine(day, time(12), tzinfo=config.TIMEZONE)
        return [
            SourceMatch(
                f"archive:{day}",
                None,
                f"Home {day}",
                f"Away {day}",
                int(start.timestamp()),
                "post",
                "fullTime",
                2,
                1,
                1,
                0,
                competition_name="Test Lig",
                odds_home=1.8,
                odds_draw=3.4,
                odds_away=4.2,
                odds_under25=1.9,
                odds_over25=1.8,
            )
        ]


def test_nightly_archive_saves_all_recent_days_and_rate_limits(tmp_path):
    path = str(tmp_path / "archive.db")
    now = datetime(2026, 8, 8, 5, tzinfo=config.TIMEZONE)
    client = ArchiveClient()
    first = service.auto_history_archive(path, now=now, result_client=client)
    assert first["skipped"] is False
    assert len(first["days"]) == config.HISTORY_ARCHIVE_RETRY_DAYS
    assert first["rows"] == config.HISTORY_ARCHIVE_RETRY_DAYS
    second = service.auto_history_archive(path, now=now, result_client=client)
    assert second["skipped"] is True
    assert second["reason"] == "rate_limited"


def test_failed_archive_day_remains_pending_for_next_retry(tmp_path):
    path = str(tmp_path / "archive.db")
    now = datetime(2026, 8, 8, 5, tzinfo=config.TIMEZONE)
    failed_day = now.date() - timedelta(days=2)
    first = service.auto_history_archive(
        path,
        now=now,
        force=True,
        result_client=ArchiveClient({failed_day}),
    )
    assert any(item["date"] == failed_day.isoformat() for item in first["errors"])
    with Database(path) as db:
        assert db.get_setting(f"history_archive:{failed_day}") is None

    retry = ArchiveClient()
    second = service.auto_history_archive(
        path,
        now=now + timedelta(hours=1),
        force=True,
        result_client=retry,
    )
    assert second["days"] == [failed_day.isoformat()]


def test_archive_waits_until_configured_local_hour(tmp_path):
    report = service.auto_history_archive(
        str(tmp_path / "archive.db"),
        now=datetime(2026, 8, 8, 2, tzinfo=config.TIMEZONE),
        result_client=ArchiveClient(),
    )
    assert report["skipped"] is True
    assert report["reason"] == "before_archive_hour"
