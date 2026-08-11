"""Independent background scheduler for persistent automation callbacks."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .storage import Database


@dataclass(frozen=True)
class ScheduledCallback:
    name: str
    callback: Callable[[], object]


class Scheduler:
    """Run one exception-isolated automation cycle at a fixed cadence."""

    def __init__(
        self,
        *,
        history_callback: Callable[[], object],
        xg_sync_callback: Callable[[], object],
        model_refresh_callback: Callable[[], object],
        model_status_callback: Callable[[], object],
        push_callback: Callable[[], object],
        result_callback: Callable[[], object],
        context_callback: Callable[[], object],
        weather_callback: Callable[[], object] | None = None,
        liveness_callback: Callable[[], object] | None = None,
        notification_callback: Callable[[], object] | None = None,
        interval: float,
        db_path: str | None = None,
    ) -> None:
        self.callbacks = (
            # Bot liveness runs first: if commands are going unanswered, the
            # user should hear about it before any slower job is attempted.
            *(
                (ScheduledCallback("bot liveness", liveness_callback),)
                if liveness_callback is not None
                else ()
            ),
            ScheduledCallback("history archive", history_callback),
            ScheduledCallback("xG sync", xg_sync_callback),
            ScheduledCallback("model refresh", model_refresh_callback),
            ScheduledCallback("model status", model_status_callback),
            ScheduledCallback("daily push", push_callback),
            ScheduledCallback("result polling", result_callback),
            ScheduledCallback("context capture", context_callback),
            *(
                (ScheduledCallback("weather", weather_callback),)
                if weather_callback is not None
                else ()
            ),
            # Draining runs last so anything queued earlier in this cycle,
            # including a just-settled coupon, goes out without waiting.
            *(
                (ScheduledCallback("notifications", notification_callback),)
                if notification_callback is not None
                else ()
            ),
        )
        self.interval = interval
        self.db_path = db_path

    def run_cycle(self) -> None:
        for item in self.callbacks:
            run_id = None
            if self.db_path is not None:
                with Database(self.db_path) as db:
                    run_id = db.start_scheduler_callback(
                        item.name, int(time.time())
                    )
            try:
                item.callback()
            except Exception as exc:
                if run_id is not None:
                    with Database(self.db_path) as db:
                        db.finish_scheduler_callback(
                            run_id,
                            outcome="error",
                            ended_ts=int(time.time()),
                            error=str(exc),
                        )
                print(f"Scheduled callback failed ({item.name}): {exc}")
            else:
                if run_id is not None:
                    with Database(self.db_path) as db:
                        db.finish_scheduler_callback(
                            run_id,
                            outcome="success",
                            ended_ts=int(time.time()),
                        )

    def run(self) -> None:
        print("Scheduler çalışıyor. Durdurmak için Ctrl-C.")
        try:
            while True:
                self.run_cycle()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("Scheduler durduruluyor.")
