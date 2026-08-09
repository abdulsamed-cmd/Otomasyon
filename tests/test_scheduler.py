from otomasyon import scheduler as scheduler_module
from otomasyon.scheduler import Scheduler


def _scheduler(calls, **overrides):
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
    return Scheduler(**callbacks, interval=0)


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
