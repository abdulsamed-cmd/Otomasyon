"""Keep the Telegram bot process alive across unexpected failures.

A bot that exits stops answering silently: the user keeps typing and nothing
comes back. Restarting rebuilds the Telegram session and the database handle,
which is the only recovery path for a poisoned connection or an exhausted
socket pool.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .. import config

# An instance that stayed up this long recovered on its own, so the next
# failure starts again from the shortest delay instead of a punitive one.
HEALTHY_RUNTIME_SECONDS = 120.0


def supervise(
    start: Callable[[], object],
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    max_restarts: int | None = None,
    on_restart: Callable[[int, float, str], None] | None = None,
) -> int:
    """Run ``start`` forever, restarting it whenever it returns or raises.

    ``max_restarts`` bounds the loop for tests; production leaves it unset.
    """
    restarts = 0
    backoff = config.TELEGRAM_SUPERVISOR_BACKOFF_SECONDS
    while True:
        started = clock()
        try:
            start()
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
        else:
            reason = "bot beklenmedik şekilde sonlandı"

        if clock() - started >= HEALTHY_RUNTIME_SECONDS:
            backoff = config.TELEGRAM_SUPERVISOR_BACKOFF_SECONDS

        restarts += 1
        if max_restarts is not None and restarts > max_restarts:
            return 1

        if on_restart is not None:
            on_restart(restarts, backoff, reason)
        else:
            print(
                f"Bot yeniden başlatılıyor ({restarts}. deneme, "
                f"{backoff:.1f} sn sonra): {reason}"
            )
        sleep(backoff)
        backoff = min(backoff * 2, config.TELEGRAM_SUPERVISOR_MAX_BACKOFF_SECONDS)
