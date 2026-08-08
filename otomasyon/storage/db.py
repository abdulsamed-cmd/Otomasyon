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

    # -- results & settlement ----------------------------------------------
    def save_result(self, result, *, now: int | None = None) -> None:
        now = now or int(time.time())
        self.conn.execute(
            """
            INSERT INTO results
                (event_id, home_score, away_score, ht_home, ht_away,
                 status, source, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                home_score = excluded.home_score,
                away_score = excluded.away_score,
                ht_home = excluded.ht_home,
                ht_away = excluded.ht_away,
                status = excluded.status,
                source = excluded.source,
                updated_ts = excluded.updated_ts
            """,
            (
                result.event_id, result.ft_home, result.ft_away,
                result.ht_home, result.ht_away, result.status,
                getattr(result, "source", None), now,
            ),
        )
        self.conn.commit()

    def get_result(self, event_id: int):
        from ..settlement import MatchResult

        row = self.conn.execute(
            "SELECT * FROM results WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        return MatchResult(
            event_id=row["event_id"],
            ft_home=row["home_score"],
            ft_away=row["away_score"],
            ht_home=row["ht_home"],
            ht_away=row["ht_away"],
            status=row["status"],
            source=row["source"],
        )

    def get_pending_coupons(self) -> list[dict]:
        """Pending coupons with their legs (leg dicts ready for settle_coupon)."""
        coupons = self.conn.execute(
            "SELECT * FROM coupons WHERE status = 'pending' ORDER BY id"
        ).fetchall()
        out = []
        for c in coupons:
            legs = self.conn.execute(
                """
                SELECT cl.*, e.home AS home, e.away AS away,
                       e.start_ts AS start_ts
                FROM coupon_legs cl
                LEFT JOIN events e ON e.id = cl.event_id
                WHERE cl.coupon_id = ?
                """,
                (c["id"],),
            ).fetchall()
            out.append(
                {
                    "id": c["id"],
                    "kind": c["kind"],
                    "for_date": c["for_date"],
                    "total_odds": c["total_odds"],
                    "legs": [
                        {
                            "id": leg["id"],
                            "event_id": leg["event_id"],
                            "home": leg["home"],
                            "away": leg["away"],
                            "start_ts": leg["start_ts"],
                            "market_t": leg["market_t"],
                            "market_st": leg["market_st"],
                            "market_sov": leg["market_sov"],
                            "market_name": leg["market_name"],
                            "outcome_name": leg["outcome_name"],
                            "odd": leg["odd_at_creation"],
                        }
                        for leg in legs
                    ],
                }
            )
        return out

    def apply_settlement(self, coupon_id: int, leg_ids: list[int], settlement) -> None:
        for leg_id, leg_res in zip(leg_ids, settlement.legs):
            self.conn.execute(
                "UPDATE coupon_legs SET result = ? WHERE id = ?",
                (leg_res.result, leg_id),
            )
        self.conn.execute(
            "UPDATE coupons SET status = ? WHERE id = ?",
            (settlement.status, coupon_id),
        )
        self.conn.commit()

    def settled_coupons(self) -> list[dict]:
        """Decided coupons with their legs, for metrics."""
        coupons = self.conn.execute(
            "SELECT * FROM coupons WHERE status IN ('won','lost','void') ORDER BY id"
        ).fetchall()
        out = []
        for c in coupons:
            legs = self.conn.execute(
                "SELECT result, odd_at_creation FROM coupon_legs WHERE coupon_id = ?",
                (c["id"],),
            ).fetchall()
            out.append(
                {
                    "id": c["id"],
                    "kind": c["kind"],
                    "status": c["status"],
                    "for_date": c["for_date"],
                    "legs": [
                        {"result": leg["result"], "odd": leg["odd_at_creation"]}
                        for leg in legs
                    ],
                }
            )
        return out

    # -- settings -----------------------------------------------------------
    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.conn.commit()

    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    # -- reads --------------------------------------------------------------
    def count(self, table: str) -> int:
        # table name is internal/controlled, not user input
        return self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
