"""Measure how long the Telegram bot can go without reaching Telegram.

The gap between completed getUpdates round trips is the exact window in which
a user's message can sit unseen, so it is the number that decides whether the
bot feels alive. Run against the live database while the bot is polling.
"""

from __future__ import annotations

import argparse

from otomasyon import config
import sqlite3
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=config.DB_PATH)
    parser.add_argument("--minutes", type=float, default=20.0)
    parser.add_argument("--sample-seconds", type=float, default=3.0)
    parser.add_argument(
        "--stall-seconds",
        type=float,
        default=30.0,
        help="Report any silence longer than this",
    )
    args = parser.parse_args()

    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    started = time.time()
    worst = 0
    worst_at = None
    completed_polls = 0
    last_poll = None
    max_failures = 0
    stalls: list[tuple[str, int, str | None]] = []

    print(f"Yoklama kararlılığı ölçülüyor ({args.minutes:.0f} dakika)...")
    while time.time() - started < args.minutes * 60:
        row = db.execute(
            "SELECT last_poll_ts, poll_failures, last_poll_error"
            " FROM telegram_bot_runtime"
        ).fetchone()
        if row is not None and row["last_poll_ts"]:
            age = int(time.time()) - row["last_poll_ts"]
            if age > worst:
                worst, worst_at = age, time.strftime("%H:%M:%S")
            if age > args.stall_seconds:
                stalls.append(
                    (time.strftime("%H:%M:%S"), age, row["last_poll_error"])
                )
            if last_poll != row["last_poll_ts"]:
                completed_polls += 1
                last_poll = row["last_poll_ts"]
            max_failures = max(max_failures, row["poll_failures"])
        time.sleep(args.sample_seconds)

    elapsed = (time.time() - started) / 60
    print(f"\nSONUC ({elapsed:.0f} dakika)")
    print(f"  tamamlanan yoklama          : {completed_polls}")
    print(f"  en uzun sessizlik           : {worst} sn (saat {worst_at})")
    print(f"  {args.stall_seconds:.0f} sn uzeri duraklama : {len(stalls)}")
    print(f"  en yuksek ardisik hata      : {max_failures}")
    for stall in stalls[:10]:
        print(f"    {stall[0]}  {stall[1]} sn  {stall[2] or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
