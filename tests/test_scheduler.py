from otomasyon import scheduler as scheduler_module
from otomasyon.scheduler import Scheduler


def _scheduler(calls, **overrides):
    callbacks = {
        "model_status_callback": lambda: calls.append("model"),
        "push_callback": lambda: calls.append("push"),
        "result_callback": lambda: calls.append("result"),
        "context_callback": lambda: calls.append("context"),
        "history_callback": lambda: calls.append("history"),
    }
    callbacks.update(overrides)
    return Scheduler(**callbacks, interval=0)


def test_callback_failure_is_isolated():
    calls = []

    def failing_result():
        calls.append("result")
        raise RuntimeError("source unavailable")

    _scheduler(calls, result_callback=failing_result).run_cycle()

    assert calls == ["model", "push", "result", "context", "history"]


def test_model_status_runs_before_daily_push():
    calls = []

    _scheduler(calls).run_cycle()

    assert calls[:2] == ["model", "push"]


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
        "model", "push", "result", "context", "history",
        "model", "push", "result", "context", "history",
    ]
    assert sleeps == 2
