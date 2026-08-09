"""Independent background scheduler for persistent automation callbacks."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


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
        interval: float,
    ) -> None:
        self.callbacks = (
            ScheduledCallback("history archive", history_callback),
            ScheduledCallback("xG sync", xg_sync_callback),
            ScheduledCallback("model refresh", model_refresh_callback),
            ScheduledCallback("model status", model_status_callback),
            ScheduledCallback("daily push", push_callback),
            ScheduledCallback("result polling", result_callback),
            ScheduledCallback("context capture", context_callback),
        )
        self.interval = interval

    def run_cycle(self) -> None:
        for item in self.callbacks:
            try:
                item.callback()
            except Exception as exc:
                print(f"Scheduled callback failed ({item.name}): {exc}")

    def run(self) -> None:
        print("Scheduler çalışıyor. Durdurmak için Ctrl-C.")
        try:
            while True:
                self.run_cycle()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("Scheduler durduruluyor.")
