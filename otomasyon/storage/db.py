"""SQLite database access layer."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Iterable

from .. import config
from ..iddaa.normalize import NormalizedEvent

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, path: str | os.PathLike = config.DB_PATH) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writes -------------------------------------------------------------
    def upsert_competitions(self, competitions: dict[int, dict]) -> int:
        rows = [
            (cid, c.get("name", ""), c.get("country_code"))
            for cid, c in competitions.items()
        ]
        self.conn.executemany(
            """
            INSERT INTO competitions (id, name, country_code)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                country_code = excluded.country_code
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def save_events(self, events: Iterable[NormalizedEvent], *, now: int | None = None) -> dict:
        """Upsert events, their markets/selections, and append odds snapshots.

        Returns counts useful for logging/verification.
        """
        now = now or int(time.time())
        cur = self.conn.cursor()
        stats = {"events": 0, "markets": 0, "selections": 0, "odds": 0}

        for ev in events:
            cur.execute(
                """
                INSERT INTO events
                    (id, home, away, competition_id, sport_id, start_ts,
                     status, first_seen_ts, last_seen_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    home = excluded.home,
                    away = excluded.away,
                    competition_id = excluded.competition_id,
                    start_ts = excluded.start_ts,
                    status = excluded.status,
                    last_seen_ts = excluded.last_seen_ts
                """,
                (
                    ev.event_id, ev.home, ev.away,
                    ev.competition_id if ev.competition_id != -1 else None,
                    ev.sport_id, ev.start_ts, ev.status, now, now,
                ),
            )
            stats["events"] += 1

            for mk in ev.markets:
                # SQLite treats NULLs as distinct in UNIQUE constraints, which
                # would break upserts for line-less markets (sov=None). Use an
                # empty string as the line key so ON CONFLICT works.
                sov_key = "" if mk.sov is None else mk.sov
                cur.execute(
                    """
                    INSERT INTO markets (event_id, t, st, sov, name, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id, t, st, sov) DO UPDATE SET
                        name = excluded.name,
                        status = excluded.status
                    """,
                    (ev.event_id, mk.t, mk.st, sov_key, mk.name, mk.status),
                )
                market_row = cur.execute(
                    "SELECT id FROM markets WHERE event_id=? AND t=? AND st=? AND sov=?",
                    (ev.event_id, mk.t, mk.st, sov_key),
                ).fetchone()
                market_id = market_row["id"]
                stats["markets"] += 1

                for sel in mk.selections:
                    if sel.odd is None:
                        continue
                    cur.execute(
                        """
                        INSERT INTO selections (market_id, outcome_no, name)
                        VALUES (?, ?, ?)
                        ON CONFLICT(market_id, outcome_no) DO UPDATE SET
                            name = excluded.name
                        """,
                        (market_id, sel.outcome_no, sel.name),
                    )
                    sel_row = cur.execute(
                        "SELECT id FROM selections WHERE market_id=? AND outcome_no=?",
                        (market_id, sel.outcome_no),
                    ).fetchone()
                    selection_id = sel_row["id"]
                    stats["selections"] += 1

                    cur.execute(
                        """
                        INSERT INTO odds_snapshots
                            (selection_id, odd, web_odd, captured_ts)
                        VALUES (?, ?, ?, ?)
                        """,
                        (selection_id, sel.odd, sel.web_odd, now),
                    )
                    stats["odds"] += 1

        self.conn.commit()
        return stats

    def save_coupon(self, coupon, for_date: str, *, now: int | None = None) -> int:
        """Persist a generated coupon and its legs; returns the coupon id.

        Coupons are stored so results can be settled and notified later.
        """
        now = now or int(time.time())
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO coupons
                (kind, created_ts, for_date, total_odds, combined_prob, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (coupon.kind, now, for_date, coupon.total_odds, coupon.combined_prob),
        )
        coupon_id = cur.lastrowid
        for leg in coupon.legs:
            cur.execute(
                """
                INSERT INTO coupon_legs
                    (coupon_id, event_id, market_t, market_st, market_sov,
                     market_name, outcome_no, outcome_name, odd_at_creation,
                     fair_prob)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    coupon_id, leg.event_id, leg.market_code[0], leg.market_code[1],
                    leg.sov, leg.market_name, leg.outcome_no, leg.outcome_name,
                    leg.odd, leg.fair_prob,
                ),
            )
        self.conn.commit()
        return coupon_id

    # -- reads --------------------------------------------------------------
    def count(self, table: str) -> int:
        # table name is internal/controlled, not user input
        return self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
