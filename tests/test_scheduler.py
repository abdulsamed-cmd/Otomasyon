from otomasyon import scheduler as scheduler_module
from otomasyon.scheduler import Scheduler
from otomasyon.storage import Database


def _scheduler(calls, **overrides):
    db_path = overrides.pop("db_path", None)
    callbacks = {
        "history_callback": lambda: calls.append("history"),
        "xg_sync_callback": lambda: calls.append("xg"),
        "model_refresh_callback": lambda: calls.append("refresh"),
        "model_status_callback": lambda: calls.append("model"),
        "push_callback": lambda: calls.append("push"),
        "result_callback": lambda: calls.append("result"),
        "context_callback": lambda: calls.append("context"),
    }
    callbacks.update(overrides)
    return Scheduler(**callbacks, interval=0, db_path=db_path)


def test_callback_failure_is_isolated():
    calls = []

    def failing_result():
        calls.append("result")
        raise RuntimeError("source unavailable")

    _scheduler(calls, result_callback=failing_result).run_cycle()

    assert calls == [
        "history", "xg", "refresh", "model", "push", "result", "context"
    ]


def test_training_pipeline_runs_before_model_status_and_daily_push():
    calls = []

    _scheduler(calls).run_cycle()

    assert calls[:5] == ["history", "xg", "refresh", "model", "push"]


def test_callback_audit_records_success_and_failure(tmp_path, monkeypatch):
    calls = []
    timestamps = iter(range(100, 114))
    monkeypatch.setattr(scheduler_module.time, "time", lambda: next(timestamps))

    def failing_result():
        calls.append("result")
        raise RuntimeError("source unavailable")

    path = str(tmp_path / "scheduler.db")
    _scheduler(
        calls,
        db_path=path,
        result_callback=failing_result,
    ).run_cycle()

    with Database(path) as db:
        runs = db.scheduler_callback_runs()
    assert len(runs) == 7
    assert runs[0] == {
        "id": runs[0]["id"],
        "callback_name": "history archive",
        "started_ts": 100,
        "ended_ts": 101,
        "outcome": "success",
        "error": None,
        "error_ts": None,
    }
    failed = runs[5]
    assert failed["callback_name"] == "result polling"
    assert failed["started_ts"] == 110
    assert failed["ended_ts"] == 111
    assert failed["outcome"] == "error"
    assert failed["error"] == "source unavailable"
    assert failed["error_ts"] == 111
    assert runs[6]["outcome"] == "success"


def test_continuous_loop_stops_cleanly_on_interrupt(monkeypatch):
    calls = []
    sleeps = 0

    def interrupt_after_two_cycles(_interval):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(scheduler_module.time, "sleep", interrupt_after_two_cycles)

    _scheduler(calls).run()

    assert calls == [
        "history", "xg", "refresh", "model", "push", "result", "context",
        "history", "xg", "refresh", "model", "push", "result", "context",
    ]
    assert sleeps == 2
