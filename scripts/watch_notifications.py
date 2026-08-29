"""Observe what the automation delivers on its own.

Reports only; it never sends anything. Used to confirm that scheduled
notifications and command replies go out without manual intervention.
"""

from __future__ import annotations

import argparse

from otomasyon import config
import sqlite3
import time


def _stamp(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%H:%M:%S", time.localtime(ts))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=config.DB_PATH)
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=60.0)
    args = parser.parse_args()

    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    deadline = time.time() + args.minutes * 60
    seen: set[str] = set()

    while True:
        now = int(time.time())
        runtime = db.execute(
            "SELECT last_poll_ts, poll_failures FROM telegram_bot_runtime"
        ).fetchone()
        cycle = db.execute(
            "SELECT MAX(started_ts) AS m FROM scheduler_callback_runs"
        ).fetchone()["m"]
        pending = db.execute(
            """
            SELECT COUNT(*) AS c FROM telegram_notification_outbox
            WHERE status NOT IN ('sent', 'failed')
            """
        ).fetchone()["c"]

        print(
            f"[{_stamp(now)}] bot {now - runtime['last_poll_ts']}sn once | "
            f"scheduler {now - cycle}sn once | bekleyen bildirim {pending}"
        )

        for row in db.execute(
            """
            SELECT kind, message_id, sent_ts FROM telegram_delivery_receipts
            WHERE sent_ts >= ? ORDER BY sent_ts
            """,
            (now - 86400,),
        ):
            key = f"receipt:{row['message_id']}"
            if key not in seen:
                seen.add(key)
                print(
                    f"    -> SISTEM GONDERDI: {row['kind']} "
                    f"msg {row['message_id']} ({_stamp(row['sent_ts'])})"
                )
        for row in db.execute(
            """
            SELECT kind, message_id, sent_ts FROM telegram_notification_outbox
            WHERE status='sent' AND sent_ts >= ? ORDER BY sent_ts
            """,
            (now - 86400,),
        ):
            key = f"outbox:{row['message_id']}"
            if key not in seen:
                seen.add(key)
                print(
                    f"    -> SISTEM GONDERDI: {row['kind']} "
                    f"msg {row['message_id']} ({_stamp(row['sent_ts'])})"
                )

        if time.time() >= deadline:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
