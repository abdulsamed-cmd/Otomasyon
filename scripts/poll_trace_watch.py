"""Summarise the recorded getUpdates trace.

Reports the gap between successful polls, which is the window in which an
incoming message stays invisible to the bot, plus every failed attempt.
"""

from __future__ import annotations

import argparse
import sqlite3
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/otomasyon.db")
    parser.add_argument("--minutes", type=float, default=0.0,
                        help="Watch for this long; 0 reports once and exits")
    parser.add_argument("--interval", type=float, default=60.0)
    args = parser.parse_args()

    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    deadline = time.time() + args.minutes * 60

    while True:
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM telegram_poll_log ORDER BY id"
            )
        ]
        ok = [row for row in rows if row["outcome"] == "ok"]
        errors = [row for row in rows if row["outcome"] != "ok"]
        gaps = [b["ended_ts"] - a["ended_ts"] for a, b in zip(ok, ok[1:])]

        stamp = time.strftime("%H:%M:%S")
        worst = max(gaps) if gaps else 0.0
        print(
            f"[{stamp}] yoklama {len(rows)} | basarili {len(ok)} | "
            f"hatali {len(errors)} | en buyuk gorunmezlik {worst:.0f} sn"
        )
        for row in errors[-3:]:
            when = time.strftime("%H:%M:%S", time.gmtime(row["started_ts"]))
            print(f"    hata {when} +{row['duration_ms']}ms {row['error'][:100]}")

        if time.time() >= deadline:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
