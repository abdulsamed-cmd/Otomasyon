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


class Telegram:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    def send_message(self, chat_id, text):
        if self.fail:
            raise RuntimeError("telegram unavailable")
        self.sent.append((chat_id, text))
        return {
            "message_id": 100 + len(self.sent),
            "date": 1786258800,
            "chat": {"id": int(chat_id)},
        }


class Understat:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def fetch_league(self, league, season):
        self.calls.append((league, season))
        if self.fail:
            raise RuntimeError("xg source unavailable")
        return []


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


def test_archive_success_notification_is_once_and_retryable(tmp_path):
    path = str(tmp_path / "archive.db")
    now = datetime(2026, 8, 8, 5, tzinfo=config.TIMEZONE)
    with Database(path) as db:
        db.set_setting("telegram_chat_id", "123")
    service.auto_history_archive(
        path, now=now, force=True, result_client=ArchiveClient()
    )
    failing = Telegram(fail=True)
    try:
        service.notify_completed_history_archives(path, failing, now=now)
    except RuntimeError:
        pass
    with Database(path) as db:
        assert db.get_setting("history_archive_notified:2026-08-07") is None

    telegram = Telegram()
    notified = service.notify_completed_history_archives(
        path, telegram, now=now
    )
    assert len(notified) == config.HISTORY_ARCHIVE_RETRY_DAYS
    assert "GECE VERİ ARŞİVİ TAMAMLANDI" in telegram.sent[0][1]
    assert service.notify_completed_history_archives(
        path, telegram, now=now
    ) == []


def test_model_status_push_runs_once_after_0945(tmp_path):
    path = str(tmp_path / "status.db")
    with Database(path) as db:
        db.set_setting("telegram_chat_id", "123")
    telegram = Telegram()
    early = datetime(2026, 8, 8, 9, 44, tzinfo=config.TIMEZONE)
    assert service.push_model_status(path, telegram, now=early) is None
    due = datetime(2026, 8, 8, 9, 45, tzinfo=config.TIMEZONE)
    assert service.push_model_status(path, telegram, now=due) == "123"
    assert "09:45 MODEL / PERFORMANS DURUMU" in telegram.sent[0][1]
    assert "ROI" in telegram.sent[0][1]
    assert "CLV" in telegram.sent[0][1]
    assert service.push_model_status(path, telegram, now=due) is None

    next_day_late = datetime(2026, 8, 9, 11, 0, tzinfo=config.TIMEZONE)
    assert service.push_model_status(path, telegram, now=next_day_late) is None


def test_xg_sync_and_model_refresh_follow_completed_archive(tmp_path):
    path = str(tmp_path / "pipeline.db")
    now = datetime(2026, 8, 9, 5, tzinfo=config.TIMEZONE)
    service.auto_history_archive(
        path, now=now, force=True, result_client=ArchiveClient()
    )
    sync = service.auto_xg_sync(
        path, now=now, force=True, client=Understat()
    )
    assert sync["skipped"] is False
    assert sync["errors"] == []
    refresh = service.auto_model_refresh(path, now=now)
    assert refresh["skipped"] is False
    assert refresh["run"]["history_matches"] > 0
    assert service.auto_model_refresh(path, now=now)["reason"] == "already_trained"
    status = service.model_status_text(path)
    assert "Son eğitim:" in status
    assert "Eğitim verisi:" in status


def test_failed_xg_sync_does_not_unlock_model_training(tmp_path):
    path = str(tmp_path / "pipeline.db")
    now = datetime(2026, 8, 9, 5, tzinfo=config.TIMEZONE)
    service.auto_history_archive(
        path, now=now, force=True, result_client=ArchiveClient()
    )
    sync = service.auto_xg_sync(
        path, now=now, force=True, client=Understat(fail=True)
    )
    assert sync["errors"]
    with Database(path) as db:
        assert db.get_setting("last_xg_sync_date") is None
    refresh = service.auto_model_refresh(path, now=now)
    assert refresh["skipped"] is True
    assert refresh["reason"] == "xg_not_ready"
