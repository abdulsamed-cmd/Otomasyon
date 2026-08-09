"""Detection of the machine itself not running.

The bot and the scheduler are separate processes on separate timers. Neither
can pause the other, so an identical silence in both is the host being
suspended. That window is exactly when a Telegram message goes unanswered, and
it has to be reported as such instead of looking like a bot failure.
"""

from otomasyon import config, service
from otomasyon.storage import Database

POLL_EVERY = 15
CYCLE_EVERY = 25


def _running(path, *, start, minutes, freeze_at=None, freeze_seconds=0):
    """Record a healthy run, optionally interrupted by a host freeze."""
    now = start
    end = start + minutes * 60
    poll_ts, cycle_ts = now, now
    with Database(path) as db:
        while poll_ts < end:
            if freeze_at and poll_ts == freeze_at:
                poll_ts += freeze_seconds
                continue
            db.log_telegram_poll(
                "owner",
                started_ts=poll_ts - POLL_EVERY,
                ended_ts=poll_ts,
                outcome="ok",
            )
            poll_ts += POLL_EVERY
        while cycle_ts < end:
            if freeze_at and cycle_ts == freeze_at:
                cycle_ts += freeze_seconds
                continue
            db.start_scheduler_callback("result polling", cycle_ts)
            cycle_ts += CYCLE_EVERY


def test_a_healthy_host_reports_no_downtime(tmp_path):
    path = str(tmp_path / "up.db")
    _running(path, start=1_786_000_000, minutes=30)

    report = service.host_downtime(path, since_ts=1_785_000_000)

    assert report["count"] == 0
    assert "kesinti görünmüyor" in service.host_downtime_text(
        path, since_ts=1_785_000_000
    )


def test_a_shared_silence_is_reported_as_host_downtime(tmp_path):
    """Both processes losing the same stretch can only be the machine."""
    path = str(tmp_path / "frozen.db")
    start = 1_786_000_000
    freeze = start + 600
    _running(
        path, start=start, minutes=40, freeze_at=freeze, freeze_seconds=12 * 60
    )

    report = service.host_downtime(path, since_ts=start - 1000)

    assert report["count"] == 1
    assert report["longest_seconds"] >= 12 * 60
    text = service.host_downtime_text(path, since_ts=start - 1000)
    assert "durdu" in text
    assert "12 dk" in text


def test_a_gap_in_only_one_process_is_not_blamed_on_the_host(tmp_path):
    """A stalled poll while the scheduler keeps ticking is an app problem."""
    path = str(tmp_path / "app.db")
    start = 1_786_000_000
    with Database(path) as db:
        for offset in (0, POLL_EVERY, POLL_EVERY + 900):
            db.log_telegram_poll(
                "owner",
                started_ts=start + offset - POLL_EVERY,
                ended_ts=start + offset,
                outcome="ok",
            )
        cycle = start
        while cycle < start + 1200:
            db.start_scheduler_callback("result polling", cycle)
            cycle += CYCLE_EVERY

    report = service.host_downtime(path, since_ts=start - 1000)

    assert report["poll_gaps"] == 1
    assert report["count"] == 0


def test_brief_hiccups_are_not_counted_as_downtime(tmp_path):
    path = str(tmp_path / "hiccup.db")
    start = 1_786_000_000
    _running(
        path,
        start=start,
        minutes=20,
        freeze_at=start + 300,
        freeze_seconds=config.HOST_STALL_SECONDS - 20,
    )

    assert service.host_downtime(path, since_ts=start - 1000)["count"] == 0
