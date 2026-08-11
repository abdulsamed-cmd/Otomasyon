"""SQLite database access layer."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .. import config
from ..iddaa.normalize import (
    NormalizedEvent,
    NormalizedMarket,
    NormalizedSelection,
)

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
        after_columns = [
            row[1]
            for row in self.conn.execute(
                "PRAGMA table_info(telegram_delivery_receipts)"
            )
        ]
        self._migrate_telegram_delivery_receipts(after_columns)
        self._migrate_telegram_bot_runtime()
        self._migrate_market_mbs()
        self.conn.commit()

    def _migrate_market_mbs(self) -> None:
        """Add the minimum-bet-count column to rows stored before it."""
        for table in ("markets", "coupon_legs"):
            columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            if "mbs" not in columns:
                with self.conn:
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN mbs INTEGER NOT NULL DEFAULT 1"
                    )

    def _migrate_telegram_bot_runtime(self) -> None:
        """Add poll-health columns to runtime tables created before them."""
        columns = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(telegram_bot_runtime)")
        }
        additions = (
            ("last_poll_ts", "INTEGER"),
            ("poll_failures", "INTEGER NOT NULL DEFAULT 0"),
            ("last_poll_error", "TEXT"),
        )
        with self.conn:
            for name, definition in additions:
                if name not in columns:
                    self.conn.execute(
                        f"ALTER TABLE telegram_bot_runtime ADD COLUMN {name} {definition}"
                    )

    def _migrate_telegram_delivery_receipts(self, columns: list[str]) -> None:
        """Bring pre-notification-date receipt tables up to the current schema."""
        if "notification_date" in columns:
            return

        with self.conn:
            self.conn.execute(
                """
                ALTER TABLE telegram_delivery_receipts
                ADD COLUMN notification_date TEXT NOT NULL DEFAULT ''
                """
            )
            self.conn.execute(
                """
                UPDATE telegram_delivery_receipts
                SET notification_date =
                    substr(dedupe_key, length(kind) + 2)
                WHERE dedupe_key LIKE kind || ':%'
                """
            )

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
        events = list(events)
        cur = self.conn.cursor()
        stats = {"events": 0, "markets": 0, "selections": 0, "odds": 0}
        cur.execute(
            "INSERT INTO bulletin_captures (captured_ts) VALUES (?)", (now,)
        )
        capture_id = cur.lastrowid

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
            cur.execute(
                """
                INSERT INTO bulletin_capture_events (capture_id, event_id, status)
                VALUES (?, ?, ?)
                """,
                (capture_id, ev.event_id, ev.status),
            )
            stats["events"] += 1

            for mk in ev.markets:
                # SQLite treats NULLs as distinct in UNIQUE constraints, which
                # would break upserts for line-less markets (sov=None). Use an
                # empty string as the line key so ON CONFLICT works.
                sov_key = "" if mk.sov is None else mk.sov
                cur.execute(
                    """
                    INSERT INTO markets (event_id, t, st, sov, name, status, mbs)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id, t, st, sov) DO UPDATE SET
                        name = excluded.name,
                        status = excluded.status,
                        mbs = excluded.mbs
                    """,
                    (ev.event_id, mk.t, mk.st, sov_key, mk.name, mk.status, mk.mbs),
                )
                market_row = cur.execute(
                    "SELECT id FROM markets WHERE event_id=? AND t=? AND st=? AND sov=?",
                    (ev.event_id, mk.t, mk.st, sov_key),
                ).fetchone()
                market_id = market_row["id"]
                cur.execute(
                    """
                    INSERT INTO bulletin_capture_markets
                        (capture_id, market_id, status)
                    VALUES (?, ?, ?)
                    """,
                    (capture_id, market_id, mk.status),
                )
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
                    cur.execute(
                        """
                        INSERT INTO bulletin_capture_selections
                            (capture_id, selection_id, odd, web_odd)
                        VALUES (?, ?, ?, ?)
                        """,
                        (capture_id, selection_id, sel.odd, sel.web_odd),
                    )
                    stats["odds"] += 1

        self.conn.commit()
        return stats

    def load_bulletin_as_of(self, as_of_ts: int) -> list[NormalizedEvent]:
        """Rebuild the most recent complete bulletin captured by ``as_of_ts``."""
        capture = self.conn.execute(
            """
            SELECT id, captured_ts FROM bulletin_captures
            WHERE captured_ts <= ?
            ORDER BY captured_ts DESC, id DESC
            LIMIT 1
            """,
            (as_of_ts,),
        ).fetchone()
        if capture is None:
            return []
        rows = self.conn.execute(
            """
            SELECT
                e.id AS event_id, e.home, e.away, e.competition_id,
                e.sport_id, e.start_ts, ce.status AS event_status,
                c.name AS competition_name, c.country_code,
                m.id AS market_id, m.t, m.st, m.sov, m.name AS market_name,
                cm.status AS market_status,
                s.outcome_no, s.name AS outcome_name,
                cs.odd, cs.web_odd
            FROM bulletin_capture_events ce
            JOIN events e ON e.id = ce.event_id
            LEFT JOIN competitions c ON c.id = e.competition_id
            JOIN bulletin_capture_markets cm
                ON cm.capture_id = ce.capture_id
            JOIN markets m
                ON m.id = cm.market_id AND m.event_id = e.id
            JOIN bulletin_capture_selections cs
                ON cs.capture_id = ce.capture_id
            JOIN selections s
                ON s.id = cs.selection_id AND s.market_id = m.id
            WHERE ce.capture_id = ?
            ORDER BY e.id, m.id, s.outcome_no
            """,
            (capture["id"],),
        ).fetchall()
        events: dict[int, NormalizedEvent] = {}
        markets: dict[int, NormalizedMarket] = {}
        for row in rows:
            event = events.get(row["event_id"])
            if event is None:
                event = NormalizedEvent(
                    event_id=row["event_id"],
                    home=row["home"],
                    away=row["away"],
                    competition_id=row["competition_id"],
                    competition_name=row["competition_name"] or "",
                    country_code=row["country_code"],
                    sport_id=row["sport_id"],
                    start_ts=row["start_ts"],
                    status=row["event_status"],
                    markets=[],
                )
                events[row["event_id"]] = event
            market = markets.get(row["market_id"])
            if market is None:
                market = NormalizedMarket(
                    market_id=row["market_id"],
                    t=row["t"],
                    st=row["st"],
                    name=row["market_name"],
                    sov=row["sov"] or None,
                    status=row["market_status"],
                    selections=[],
                )
                markets[row["market_id"]] = market
                event.markets.append(market)
            market.selections.append(
                NormalizedSelection(
                    outcome_no=row["outcome_no"],
                    name=row["outcome_name"],
                    odd=row["odd"],
                    web_odd=row["web_odd"],
                )
            )
        return list(events.values())

    def save_coupon(self, coupon, for_date: str, *, now: int | None = None) -> int:
        """Persist a generated coupon and its legs; returns the coupon id.

        Coupons are stored so results can be settled and notified later.
        """
        now = now or int(time.time())
        cur = self.conn.cursor()
        if coupon.kind in ("daily_main", "daily_alt"):
            # A superseded coupon is no longer the day's coupon, so it must not
            # answer for one; otherwise the replacement is silently discarded.
            existing = cur.execute(
                """
                SELECT id FROM coupons
                WHERE kind=? AND for_date=?
                  AND (notes IS NULL OR notes <> 'superseded')
                ORDER BY id LIMIT 1
                """,
                (coupon.kind, for_date),
            ).fetchone()
            if existing is not None:
                return existing["id"]
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
                     fair_prob, mbs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    coupon_id, leg.event_id, leg.market_code[0], leg.market_code[1],
                    leg.sov, leg.market_name, leg.outcome_no, leg.outcome_name,
                    leg.odd, leg.fair_prob, leg.mbs,
                ),
            )
        self.conn.commit()
        return coupon_id

    def supersede_daily_coupons(self, for_date: str, *, now: int | None = None) -> int:
        """Retire a day's still-pending daily coupons before they are replaced.

        Only coupons whose matches have all yet to start may be retired: once
        a leg has kicked off, the coupon has been played and rewriting it
        would be rewriting history.

        A retired coupon gets its own status rather than being voided. Voiding
        is a settlement outcome, so it would announce a cancelled *result* for
        matches that are still going to be played.
        """
        rows = self.conn.execute(
            """
            SELECT c.id, MIN(e.start_ts) AS first_start
            FROM coupons c
            JOIN coupon_legs cl ON cl.coupon_id=c.id
            LEFT JOIN events e ON e.id=cl.event_id
            WHERE c.for_date=? AND c.status='pending'
              AND c.kind IN ('daily_main','daily_alt')
              AND (c.notes IS NULL OR c.notes <> 'superseded')
            GROUP BY c.id
            """,
            (for_date,),
        ).fetchall()
        now = now or int(time.time())
        retired = [
            row["id"]
            for row in rows
            if row["first_start"] is not None and row["first_start"] > now
        ]
        for coupon_id in retired:
            self.conn.execute(
                "UPDATE coupons SET status='superseded', notes='superseded' WHERE id=?",
                (coupon_id,),
            )
        self.conn.commit()
        return len(retired)

    def save_surprise_report(
        self, report, period_key: str, *, now: int | None = None
    ) -> int:
        """Persist one immutable weekly surprise shortlist and system set."""
        now = now or int(time.time())
        existing = self.conn.execute(
            "SELECT id FROM surprise_reports WHERE period_key=?", (period_key,)
        ).fetchone()
        if existing is not None:
            return existing["id"]
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO surprise_reports (period_key, created_ts, status)
            VALUES (?, ?, 'pending')
            """,
            (period_key, now),
        )
        report_id = cur.lastrowid
        system_keys = {
            (candidate.category, candidate.event_id)
            for candidate in report.system_set
        }
        for category, candidates in report.by_category.items():
            market_code, _, _ = config.SURPRISE_CATEGORIES[category]
            market_t, market_st = market_code
            for candidate in candidates:
                cur.execute(
                    """
                    INSERT INTO surprise_candidates
                        (report_id, event_id, category, market_t, market_st,
                         market_sov, outcome_name, odd, fair_prob, in_system)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report_id,
                        candidate.event_id,
                        category,
                        market_t,
                        market_st,
                        candidate.sov,
                        candidate.outcome_name,
                        candidate.odd,
                        candidate.fair_prob,
                        int((category, candidate.event_id) in system_keys),
                    ),
                )
        for scenario in report.scenarios:
            cur.execute(
                """
                INSERT INTO surprise_scenarios
                    (report_id, system_size, columns, unit_stake)
                VALUES (?, ?, ?, ?)
                """,
                (
                    report_id,
                    scenario.size,
                    scenario.columns,
                    scenario.unit_stake,
                ),
            )
        self.conn.commit()
        return report_id

    def pending_surprise_events(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT sc.event_id, e.home, e.away, e.start_ts
            FROM surprise_candidates sc
            JOIN surprise_reports sr ON sr.id=sc.report_id
            JOIN events e ON e.id=sc.event_id
            WHERE sr.status='pending' AND sc.result='pending'
            """
        ).fetchall()
        return [dict(row) for row in rows]

    # -- historical model data --------------------------------------------
    def save_historical_matches(self, matches) -> int:
        from ..results.matcher import normalize_team

        rows = []
        for match in matches:
            if (
                match.state != "post"
                or match.ft_home is None
                or match.ft_away is None
            ):
                continue
            match_date = datetime.fromtimestamp(
                match.start_ts, tz=config.TIMEZONE
            ).strftime("%Y-%m-%d")
            rows.append(
                (
                    match.source_id,
                    match.iddaa_code,
                    match.start_ts,
                    match_date,
                    match.competition_id,
                    match.competition_name,
                    match.home,
                    match.away,
                    normalize_team(match.home),
                    normalize_team(match.away),
                    match.ft_home,
                    match.ft_away,
                    match.ht_home,
                    match.ht_away,
                    match.odds_home,
                    match.odds_draw,
                    match.odds_away,
                    match.odds_under25,
                    match.odds_over25,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO historical_matches
                (source_id, iddaa_code, start_ts, match_date, competition_id,
                 competition, home, away, home_key, away_key, ft_home, ft_away,
                 ht_home, ht_away, odds_home, odds_draw, odds_away,
                 odds_under25, odds_over25)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                iddaa_code=excluded.iddaa_code,
                ft_home=excluded.ft_home, ft_away=excluded.ft_away,
                ht_home=excluded.ht_home, ht_away=excluded.ht_away,
                odds_home=excluded.odds_home, odds_draw=excluded.odds_draw,
                odds_away=excluded.odds_away,
                odds_under25=excluded.odds_under25,
                odds_over25=excluded.odds_over25
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def load_historical_matches(
        self, *, before_ts: int | None = None, after_ts: int | None = None
    ) -> list[dict]:
        clauses, params = [], []
        if before_ts is not None:
            clauses.append("hm.start_ts < ?")
            params.append(before_ts)
        if after_ts is not None:
            clauses.append("hm.start_ts >= ?")
            params.append(after_ts)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT hm.*, um.xg_home, um.xg_away,
                   um.league AS xg_league
            FROM historical_matches hm
            LEFT JOIN historical_xg_links xl
                ON xl.historical_source_id=hm.source_id
            LEFT JOIN understat_matches um
                ON um.source_id=xl.understat_source_id
            {where}
            ORDER BY hm.start_ts
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def save_understat_matches(self, matches) -> int:
        rows = [
            (
                item.source_id,
                item.league,
                item.season,
                item.start_ts,
                item.home,
                item.away,
                item.ft_home,
                item.ft_away,
                item.xg_home,
                item.xg_away,
            )
            for item in matches
        ]
        self.conn.executemany(
            """
            INSERT INTO understat_matches
                (source_id, league, season, start_ts, home, away,
                 ft_home, ft_away, xg_home, xg_away)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                league=excluded.league, season=excluded.season,
                start_ts=excluded.start_ts, home=excluded.home, away=excluded.away,
                ft_home=excluded.ft_home, ft_away=excluded.ft_away,
                xg_home=excluded.xg_home, xg_away=excluded.xg_away
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def load_understat_matches(self) -> list[dict]:
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM understat_matches ORDER BY start_ts"
            ).fetchall()
        ]

    def save_historical_xg_links(self, links: list[dict]) -> int:
        rows = [
            (
                item["understat_source_id"],
                item["historical_source_id"],
                item["match_score"],
            )
            for item in links
        ]
        self.conn.executemany(
            """
            INSERT INTO historical_xg_links
                (understat_source_id, historical_source_id, match_score)
            VALUES (?, ?, ?)
            ON CONFLICT(understat_source_id) DO UPDATE SET
                historical_source_id=excluded.historical_source_id,
                match_score=excluded.match_score
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def save_model_predictions(self, predictions: list[dict]) -> int:
        before = self.conn.total_changes
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO model_predictions
                (model_version, for_date, event_id, captured_ts, market,
                 outcome_name, odd, predicted_prob, market_fair, edge)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["model_version"],
                    item["for_date"],
                    item["event_id"],
                    item["captured_ts"],
                    item["market"],
                    item["outcome_name"],
                    item["odd"],
                    item["predicted_prob"],
                    item["market_fair"],
                    item["edge"],
                )
                for item in predictions
            ],
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def pending_model_prediction_events(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT mp.event_id, e.home, e.away, e.start_ts
            FROM model_predictions mp
            JOIN events e ON e.id=mp.event_id
            WHERE mp.result='pending'
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def save_walk_forward_predictions(self, predictions: list[dict]) -> int:
        before = self.conn.total_changes
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO walk_forward_predictions
                (model_version, historical_source_id, prediction_date,
                 cutoff_ts, market, outcome_name, odd, predicted_prob,
                 market_fair, edge, actual_result, won, profit,
                 xg_samples_home, xg_samples_away, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["model_version"],
                    item["historical_source_id"],
                    item["prediction_date"],
                    item["cutoff_ts"],
                    item["market"],
                    item["outcome_name"],
                    item["odd"],
                    item["predicted_prob"],
                    item["market_fair"],
                    item["edge"],
                    item["actual_result"],
                    int(item["won"]),
                    item["profit"],
                    item["xg_samples_home"],
                    item["xg_samples_away"],
                    item["created_ts"],
                )
                for item in predictions
            ],
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def load_walk_forward_predictions(
        self, model_version: str
    ) -> list[dict]:
        return [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT * FROM walk_forward_predictions
                WHERE model_version=?
                ORDER BY prediction_date, id
                """,
                (model_version,),
            ).fetchall()
        ]

    def save_model_artifact(
        self,
        *,
        run_date: str,
        model_version: str,
        trained_ts: int,
        cutoff_ts: int,
        sha256: str,
        payload: bytes,
        features: list[tuple],
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO model_artifacts
                    (run_date, model_version, trained_ts, cutoff_ts, sha256,
                     payload)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_date, model_version) DO UPDATE SET
                    trained_ts=excluded.trained_ts,
                    cutoff_ts=excluded.cutoff_ts,
                    sha256=excluded.sha256,
                    payload=excluded.payload
                """,
                (
                    run_date,
                    model_version,
                    trained_ts,
                    cutoff_ts,
                    sha256,
                    payload,
                ),
            )
            self.conn.execute(
                """
                DELETE FROM model_team_features
                WHERE run_date=? AND model_version=?
                """,
                (run_date, model_version),
            )
            self.conn.executemany(
                """
                INSERT INTO model_team_features
                    (run_date, model_version, team_key, venue, weight, scored,
                     conceded, matches, xg_matches, elo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                features,
            )

    def load_model_artifact(
        self, model_version: str, *, run_date: str | None = None
    ) -> dict | None:
        if run_date is None:
            row = self.conn.execute(
                """
                SELECT * FROM model_artifacts
                WHERE model_version=?
                ORDER BY trained_ts DESC LIMIT 1
                """,
                (model_version,),
            ).fetchone()
        else:
            row = self.conn.execute(
                """
                SELECT * FROM model_artifacts
                WHERE model_version=? AND run_date=?
                """,
                (model_version, run_date),
            ).fetchone()
        return dict(row) if row else None

    def historical_dates(self) -> set[str]:
        return {
            row["match_date"]
            for row in self.conn.execute(
                "SELECT DISTINCT match_date FROM historical_matches"
            ).fetchall()
        }

    def save_clubelo_ratings(self, ratings) -> int:
        from ..results.matcher import normalize_team

        rows = [
            (
                rating.date,
                normalize_team(rating.club),
                rating.club,
                rating.country,
                rating.level,
                rating.elo,
            )
            for rating in ratings
            if normalize_team(rating.club)
        ]
        self.conn.executemany(
            """
            INSERT INTO clubelo_ratings
                (rating_date, club_key, club, country, level, elo)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(rating_date, club_key) DO UPDATE SET
                club=excluded.club, country=excluded.country,
                level=excluded.level, elo=excluded.elo
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def save_venues(self, venues, *, now: int | None = None) -> int:
        now = now or int(time.time())
        rows = [
            (
                venue.team_key,
                venue.stadium,
                venue.city,
                venue.country,
                venue.latitude,
                venue.longitude,
                venue.source_match,
                now,
            )
            for venue in venues
        ]
        self.conn.executemany(
            """
            INSERT INTO venues
                (team_key, stadium, city, country, latitude, longitude,
                 source_match, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_key) DO UPDATE SET
                stadium=excluded.stadium, city=excluded.city,
                country=excluded.country, latitude=excluded.latitude,
                longitude=excluded.longitude,
                source_match=excluded.source_match,
                updated_ts=excluded.updated_ts
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def load_venues(self) -> dict[str, dict]:
        return {
            row["team_key"]: dict(row)
            for row in self.conn.execute("SELECT * FROM venues").fetchall()
        }

    def save_venue_weather(self, observations, *, now: int | None = None) -> int:
        now = now or int(time.time())
        rows = [
            (
                item.team_key,
                item.weather_date,
                item.precipitation,
                item.wind_speed,
                now,
            )
            for item in observations
        ]
        self.conn.executemany(
            """
            INSERT INTO venue_weather
                (team_key, weather_date, precipitation, wind_speed, fetched_ts)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(team_key, weather_date) DO UPDATE SET
                precipitation=excluded.precipitation,
                wind_speed=excluded.wind_speed,
                fetched_ts=excluded.fetched_ts
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def load_venue_weather(self) -> dict[tuple[str, str], tuple[float, float]]:
        """``(team_key, date) -> (precipitation, wind)`` for every complete day."""
        return {
            (row["team_key"], row["weather_date"]): (
                row["precipitation"],
                row["wind_speed"],
            )
            for row in self.conn.execute(
                """
                SELECT team_key, weather_date, precipitation, wind_speed
                FROM venue_weather
                WHERE precipitation IS NOT NULL AND wind_speed IS NOT NULL
                """
            ).fetchall()
        }

    def weather_covered_dates(self, team_key: str) -> set[str]:
        return {
            row["weather_date"]
            for row in self.conn.execute(
                "SELECT weather_date FROM venue_weather WHERE team_key = ?",
                (team_key,),
            ).fetchall()
        }

    def load_clubelo_ratings(self, start_date: str, end_date: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM clubelo_ratings
            WHERE rating_date BETWEEN ? AND ?
            ORDER BY rating_date, club_key
            """,
            (start_date, end_date),
        ).fetchall()
        return [dict(row) for row in rows]

    def clubelo_dates(self) -> set[str]:
        return {
            row["rating_date"]
            for row in self.conn.execute(
                "SELECT DISTINCT rating_date FROM clubelo_ratings"
            ).fetchall()
        }

    def save_fotmob_fixtures(self, fixtures, *, now: int | None = None) -> int:
        now = now or int(time.time())
        rows = [
            (
                item.match_id,
                item.league_id,
                item.league_name,
                item.home_id,
                item.home,
                item.away_id,
                item.away,
                item.start_ts,
                int(item.started),
                int(item.finished),
                int(item.cancelled),
                now,
            )
            for item in fixtures
        ]
        self.conn.executemany(
            """
            INSERT INTO fotmob_fixtures
                (match_id, league_id, league_name, home_id, home, away_id, away,
                 start_ts, started, finished, cancelled, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                league_id=excluded.league_id, league_name=excluded.league_name,
                home_id=excluded.home_id, home=excluded.home,
                away_id=excluded.away_id, away=excluded.away,
                start_ts=excluded.start_ts, started=excluded.started,
                finished=excluded.finished, cancelled=excluded.cancelled,
                updated_ts=excluded.updated_ts
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def save_fotmob_links(
        self, links: dict, diagnostics: list[dict], *, now: int | None = None
    ) -> int:
        now = now or int(time.time())
        scores = {
            item["event_id"]: item["score"]
            for item in diagnostics
            if item["method"] != "unmatched"
        }
        rows = [
            (event_id, fixture.match_id, scores[event_id], now)
            for event_id, fixture in links.items()
        ]
        self.conn.executemany(
            """
            INSERT INTO iddaa_fotmob_links
                (event_id, match_id, match_score, linked_ts)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                match_id=excluded.match_id, match_score=excluded.match_score,
                linked_ts=excluded.linked_ts
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def save_fotmob_contexts(self, contexts) -> int:
        rows = [
            (
                item.match_id,
                item.captured_ts,
                int(item.started),
                int(item.finished),
                item.coverage_level,
                item.xg_home,
                item.xg_away,
                int(item.lineup_available),
                item.home_starters,
                item.away_starters,
            )
            for item in contexts
        ]
        self.conn.executemany(
            """
            INSERT INTO fotmob_context_captures
                (match_id, captured_ts, started, finished, coverage_level,
                 xg_home, xg_away, lineup_available, home_starters, away_starters)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id, captured_ts) DO UPDATE SET
                started=excluded.started, finished=excluded.finished,
                coverage_level=excluded.coverage_level,
                xg_home=excluded.xg_home, xg_away=excluded.xg_away,
                lineup_available=excluded.lineup_available,
                home_starters=excluded.home_starters,
                away_starters=excluded.away_starters
            """,
            rows,
        )
        player_rows = []
        for item in contexts:
            for side, players in (
                ("home", item.home_players),
                ("away", item.away_players),
            ):
                player_rows.extend(
                    (
                        item.match_id,
                        item.captured_ts,
                        side,
                        player.player_id,
                        player.name,
                        player.position_id,
                        player.market_value,
                    )
                    for player in players
                )
        self.conn.executemany(
            """
            INSERT INTO fotmob_lineup_players
                (match_id, captured_ts, side, player_id, name, position_id,
                 market_value)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id, captured_ts, side, player_id) DO UPDATE SET
                name=excluded.name, position_id=excluded.position_id,
                market_value=excluded.market_value
            """,
            player_rows,
        )
        self.conn.commit()
        return len(rows)

    def lineup_features(self, match_id: int, captured_ts: int) -> dict:
        """Return point-in-time lineup value and continuity versus prior capture."""
        fixture = self.conn.execute(
            """
            SELECT home_id, away_id FROM fotmob_fixtures WHERE match_id=?
            """,
            (match_id,),
        ).fetchone()
        if fixture is None:
            return {}
        output = {}
        for side, team_id in (("home", fixture["home_id"]), ("away", fixture["away_id"])):
            current = self.conn.execute(
                """
                SELECT player_id, market_value FROM fotmob_lineup_players
                WHERE match_id=? AND captured_ts=? AND side=?
                """,
                (match_id, captured_ts, side),
            ).fetchall()
            previous = self.conn.execute(
                """
                SELECT lp.player_id, lp.market_value
                FROM fotmob_fixtures f
                JOIN fotmob_context_captures c ON c.match_id=f.match_id
                JOIN fotmob_lineup_players lp
                    ON lp.match_id=c.match_id AND lp.captured_ts=c.captured_ts
                WHERE (f.home_id=? OR f.away_id=?)
                  AND f.start_ts < (SELECT start_ts FROM fotmob_fixtures WHERE match_id=?)
                  AND c.captured_ts < (SELECT start_ts FROM fotmob_fixtures WHERE match_id=?)
                  AND c.lineup_available=1
                  AND lp.side=CASE WHEN f.home_id=? THEN 'home' ELSE 'away' END
                ORDER BY f.start_ts DESC, c.captured_ts DESC
                LIMIT 11
                """,
                (team_id, team_id, match_id, match_id, team_id),
            ).fetchall()
            current_ids = {row["player_id"] for row in current}
            previous_ids = {row["player_id"] for row in previous}
            output[side] = {
                "starters": len(current_ids),
                "total_market_value": sum(
                    row["market_value"] or 0.0 for row in current
                ),
                "returning_starters": (
                    len(current_ids & previous_ids) if previous_ids else None
                ),
                "changes": (
                    len(current_ids - previous_ids) if previous_ids else None
                ),
            }
        return output

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
        self.populate_closing_odds(coupon_id)
        self.conn.commit()

    def populate_closing_odds(self, coupon_id: int) -> int:
        """Store the final captured pre-kickoff price for each coupon leg."""
        legs = self.conn.execute(
            """
            SELECT cl.id, cl.event_id, cl.market_t, cl.market_st,
                   cl.market_sov, cl.outcome_no, e.start_ts
            FROM coupon_legs cl
            JOIN events e ON e.id=cl.event_id
            WHERE cl.coupon_id=?
            """,
            (coupon_id,),
        ).fetchall()
        updated = 0
        for leg in legs:
            closing = self.conn.execute(
                """
                SELECT os.odd
                FROM markets m
                JOIN selections s ON s.market_id=m.id
                JOIN odds_snapshots os ON os.selection_id=s.id
                WHERE m.event_id=? AND m.t=? AND m.st=?
                  AND COALESCE(m.sov, '')=COALESCE(?, '')
                  AND s.outcome_no=? AND os.captured_ts<?
                ORDER BY os.captured_ts DESC, os.id DESC
                LIMIT 1
                """,
                (
                    leg["event_id"],
                    leg["market_t"],
                    leg["market_st"],
                    leg["market_sov"],
                    leg["outcome_no"],
                    leg["start_ts"],
                ),
            ).fetchone()
            if closing is not None:
                self.conn.execute(
                    "UPDATE coupon_legs SET closing_odd=? WHERE id=?",
                    (closing["odd"], leg["id"]),
                )
                updated += 1
        return updated

    def decided_coupons_for_dates(self, for_dates: Iterable[str]) -> list[dict]:
        """Decided coupons with full leg detail, ready to be re-announced."""
        dates = list(for_dates)
        if not dates:
            return []
        placeholders = ",".join("?" for _ in dates)
        coupons = self.conn.execute(
            f"""
            SELECT * FROM coupons
            WHERE status IN ('won','lost','void') AND for_date IN ({placeholders})
            ORDER BY id
            """,
            dates,
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
                    "status": c["status"],
                    "for_date": c["for_date"],
                    "notes": c["notes"],
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
                            "result": leg["result"],
                        }
                        for leg in legs
                    ],
                }
            )
        return out

    def settled_coupons(self) -> list[dict]:
        """Decided coupons with their legs, for metrics."""
        coupons = self.conn.execute(
            "SELECT * FROM coupons WHERE status IN ('won','lost','void') ORDER BY id"
        ).fetchall()
        out = []
        for c in coupons:
            legs = self.conn.execute(
                """
                SELECT result, odd_at_creation, closing_odd
                FROM coupon_legs WHERE coupon_id = ?
                """,
                (c["id"],),
            ).fetchall()
            out.append(
                {
                    "id": c["id"],
                    "kind": c["kind"],
                    "status": c["status"],
                    "for_date": c["for_date"],
                    "notes": c["notes"],
                    "legs": [
                        {
                            "result": leg["result"],
                            "odd": leg["odd_at_creation"],
                            "closing_odd": leg["closing_odd"],
                        }
                        for leg in legs
                    ],
                }
            )
        return out

    def dashboard_coupon_summaries(
        self, *, limit: int = 100, settled_only: bool = False
    ) -> list[dict]:
        where = (
            "WHERE c.status IN ('won','lost','void')" if settled_only else ""
        )
        rows = self.conn.execute(
            f"""
            SELECT c.id, c.kind, c.created_ts, c.for_date, c.total_odds,
                   c.combined_prob, c.status, COUNT(cl.id) AS leg_count
            FROM coupons c
            LEFT JOIN coupon_legs cl ON cl.coupon_id=c.id
            {where}
            GROUP BY c.id
            ORDER BY c.created_ts DESC, c.id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def dashboard_coupon(self, coupon_id: int) -> dict | None:
        coupon = self.conn.execute(
            "SELECT * FROM coupons WHERE id=?", (coupon_id,)
        ).fetchone()
        if coupon is None:
            return None
        legs = self.conn.execute(
            """
            SELECT cl.*, e.home, e.away, e.start_ts
            FROM coupon_legs cl
            LEFT JOIN events e ON e.id=cl.event_id
            WHERE cl.coupon_id=?
            ORDER BY e.start_ts, cl.id
            """,
            (coupon_id,),
        ).fetchall()
        result = dict(coupon)
        result["legs"] = [dict(row) for row in legs]
        return result

    def dashboard_surprise_reports(self, *, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT sr.*, COUNT(sc.id) AS candidate_count,
                   SUM(CASE WHEN sc.in_system=1 THEN 1 ELSE 0 END) AS system_count
            FROM surprise_reports sr
            LEFT JOIN surprise_candidates sc ON sc.report_id=sr.id
            GROUP BY sr.id
            ORDER BY sr.created_ts DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def dashboard_surprise_report(self, report_id: int) -> dict | None:
        report = self.conn.execute(
            "SELECT * FROM surprise_reports WHERE id=?", (report_id,)
        ).fetchone()
        if report is None:
            return None
        result = dict(report)
        result["candidates"] = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT sc.*, e.home, e.away, e.start_ts
                FROM surprise_candidates sc
                JOIN events e ON e.id=sc.event_id
                WHERE sc.report_id=?
                ORDER BY sc.in_system DESC, sc.fair_prob DESC
                """,
                (report_id,),
            ).fetchall()
        ]
        result["system_count"] = sum(
            candidate["in_system"] for candidate in result["candidates"]
        )
        result["scenarios"] = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT * FROM surprise_scenarios
                WHERE report_id=? ORDER BY system_size
                """,
                (report_id,),
            ).fetchall()
        ]
        return result

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

    # -- Telegram delivery receipts ----------------------------------------
    def save_telegram_delivery_receipt(
        self,
        *,
        kind: str,
        notification_date: str,
        dedupe_key: str,
        chat_id: int | str,
        message_id: int,
        telegram_date: int | None,
        sent_ts: int,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO telegram_delivery_receipts
                (kind, notification_date, dedupe_key, chat_id, message_id,
                 telegram_date, sent_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                notification_date,
                dedupe_key,
                str(chat_id),
                int(message_id),
                int(telegram_date) if telegram_date is not None else None,
                int(sent_ts),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def record_telegram_delivery(
        self,
        *,
        kind: str,
        notification_dates: Iterable[str],
        chat_id: int | str,
        message_id: int,
        telegram_date: int | None,
        sent_ts: int,
        markers: dict[str, str],
    ) -> None:
        """Atomically persist delivery receipts and their dedupe markers."""
        dates = list(notification_dates)
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO telegram_delivery_receipts
                    (kind, notification_date, dedupe_key, chat_id, message_id,
                     telegram_date, sent_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        kind,
                        notification_date,
                        f"{kind}:{notification_date}",
                        str(chat_id),
                        int(message_id),
                        (
                            int(telegram_date)
                            if telegram_date is not None
                            else None
                        ),
                        int(sent_ts),
                    )
                    for notification_date in dates
                ],
            )
            self.conn.executemany(
                """
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                [
                    (key, value)
                    for key, value in markers.items()
                ],
            )

    def get_telegram_delivery_receipt(self, dedupe_key: str) -> dict | None:
        row = self.conn.execute(
            """
            SELECT id, kind, notification_date, dedupe_key, chat_id, message_id,
                   telegram_date, sent_ts
            FROM telegram_delivery_receipts
            WHERE dedupe_key=?
            """,
            (dedupe_key,),
        ).fetchone()
        return dict(row) if row is not None else None

    # -- durable Telegram command transport --------------------------------
    def persist_telegram_updates(self, updates: Iterable[dict], received_ts: int) -> int:
        """Persist a poll batch and its next offset in one transaction."""
        updates = list(updates)
        if not updates:
            value = self.get_setting("telegram_poll_offset")
            return int(value) if value is not None else 0
        rows = []
        for update in updates:
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                raise ValueError("Telegram update_id must be an integer")
            rows.append(
                (
                    update_id,
                    json.dumps(update, ensure_ascii=False, separators=(",", ":")),
                    int(received_ts),
                )
            )
        next_offset = max(row[0] for row in rows) + 1
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO telegram_command_inbox
                    (update_id, payload_json, received_ts)
                VALUES (?, ?, ?)
                """,
                rows,
            )
            current = self.conn.execute(
                "SELECT value FROM app_settings WHERE key='telegram_poll_offset'"
            ).fetchone()
            persisted = int(current["value"]) if current is not None else 0
            next_offset = max(persisted, next_offset)
            self.conn.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES ('telegram_poll_offset', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(next_offset),),
            )
        return next_offset

    def telegram_poll_offset(self) -> int | None:
        value = self.get_setting("telegram_poll_offset")
        return int(value) if value is not None else None

    def recover_telegram_commands(self, now: int) -> None:
        """Make interrupted renders retryable and uncertain sends explicit."""
        with self.conn:
            self.conn.execute(
                """
                UPDATE telegram_command_inbox
                SET status='pending', error='interrupted during render'
                WHERE status='processing'
                """
            )
            self.conn.execute(
                """
                UPDATE telegram_command_outbox
                SET status='ambiguous',
                    error='process stopped after send began; delivery unknown',
                    next_attempt_ts=?
                WHERE status='sending'
                """,
                (int(now),),
            )

    def pending_telegram_inbox(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT update_id, payload_json, status, received_ts
            FROM telegram_command_inbox
            WHERE status IN ('pending', 'render_error')
            ORDER BY update_id
            """
        ).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def begin_telegram_command(self, update_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE telegram_command_inbox
                SET status='processing', error=NULL
                WHERE update_id=? AND status IN ('pending', 'render_error')
                """,
                (int(update_id),),
            )
        return cur.rowcount == 1

    def reject_telegram_command(self, update_id: int, now: int, detail: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE telegram_command_inbox
                SET status='completed', authorized=0, authorized_ts=?,
                    completed_ts=?
                WHERE update_id=?
                """,
                (int(now), int(now), int(update_id)),
            )
            self._telegram_audit(update_id, "authorization", "rejected", now, detail)

    def authorize_telegram_command(
        self, update_id: int, chat_id: int | str, command: str, now: int
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE telegram_command_inbox
                SET authorized=1, authorized_ts=?, chat_id=?, command=?
                WHERE update_id=?
                """,
                (int(now), str(chat_id), command, int(update_id)),
            )
            self.conn.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES ('telegram_chat_id', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(chat_id),),
            )
            self._telegram_audit(update_id, "authorization", "allowed", now, command)

    def audit_telegram_command(
        self,
        update_id: int,
        stage: str,
        outcome: str,
        now: int,
        detail: str | None = None,
    ) -> None:
        with self.conn:
            self._telegram_audit(update_id, stage, outcome, now, detail)

    def _telegram_audit(
        self,
        update_id: int,
        stage: str,
        outcome: str,
        now: int,
        detail: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO telegram_command_audit
                (update_id, stage, outcome, occurred_ts, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(update_id), stage, outcome, int(now), detail),
        )

    def enqueue_telegram_reply(
        self, update_id: int, chat_id: int | str, reply_text: str, now: int
    ) -> None:
        """Atomically finish rendering and create the unique outbox row."""
        with self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO telegram_command_outbox
                    (update_id, chat_id, reply_text, next_attempt_ts, created_ts)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(update_id), str(chat_id), reply_text, int(now), int(now)),
            )
            self.conn.execute(
                """
                UPDATE telegram_command_inbox
                SET status='outbox_ready', dispatched_ts=COALESCE(dispatched_ts, ?),
                    rendered_ts=?, error=NULL
                WHERE update_id=?
                """,
                (int(now), int(now), int(update_id)),
            )
            self._telegram_audit(update_id, "render", "completed", now, None)

    def fail_telegram_render(self, update_id: int, error: str, now: int) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE telegram_command_inbox
                SET status='render_error', error=?, dispatched_ts=COALESCE(dispatched_ts, ?)
                WHERE update_id=?
                """,
                (error, int(now), int(update_id)),
            )
            self._telegram_audit(update_id, "render", "error", now, error)

    def enqueue_telegram_failure_reply(
        self, update_id: int, chat_id: int | str, reply_text: str, now: int
    ) -> None:
        """Queue an honest failure notice, keeping the render error on record.

        Silence is indistinguishable from a dead bot, so a command that cannot
        be answered is still answered.
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO telegram_command_outbox
                    (update_id, chat_id, reply_text, next_attempt_ts, created_ts)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(update_id), str(chat_id), reply_text, int(now), int(now)),
            )
            self.conn.execute(
                """
                UPDATE telegram_command_inbox
                SET status='outbox_ready',
                    dispatched_ts=COALESCE(dispatched_ts, ?),
                    rendered_ts=?
                WHERE update_id=?
                """,
                (int(now), int(now), int(update_id)),
            )
            self._telegram_audit(update_id, "render", "fallback", now, None)

    def due_telegram_outbox(self, now: int) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM telegram_command_outbox
            WHERE status IN ('pending', 'retry_wait', 'ambiguous')
              AND next_attempt_ts<=?
            ORDER BY update_id
            """,
            (int(now),),
        ).fetchall()
        return [dict(row) for row in rows]

    def begin_telegram_send(self, outbox_id: int, now: int) -> dict | None:
        """Persist the uncertainty boundary before invoking sendMessage."""
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE telegram_command_outbox
                SET status='sending', attempts=attempts+1,
                    first_attempt_ts=COALESCE(first_attempt_ts, ?),
                    last_attempt_ts=?, error=NULL
                WHERE id=? AND status IN ('pending', 'retry_wait', 'ambiguous')
                  AND next_attempt_ts<=?
                """,
                (int(now), int(now), int(outbox_id), int(now)),
            )
            if cur.rowcount != 1:
                return None
            row = self.conn.execute(
                "SELECT * FROM telegram_command_outbox WHERE id=?",
                (int(outbox_id),),
            ).fetchone()
            self._telegram_audit(
                row["update_id"], "send", "attempted", now, f"attempt={row['attempts']}"
            )
        return dict(row)

    def retry_telegram_send(
        self, outbox_id: int, error: str, next_attempt_ts: int, now: int
    ) -> None:
        with self.conn:
            row = self.conn.execute(
                "SELECT update_id FROM telegram_command_outbox WHERE id=?",
                (int(outbox_id),),
            ).fetchone()
            self.conn.execute(
                """
                UPDATE telegram_command_outbox
                SET status='retry_wait', error=?, next_attempt_ts=?
                WHERE id=? AND status='sending'
                """,
                (error, int(next_attempt_ts), int(outbox_id)),
            )
            self._telegram_audit(
                row["update_id"], "send", "retry_scheduled", now, error
            )

    def fail_telegram_send(self, outbox_id: int, error: str, now: int) -> None:
        with self.conn:
            row = self.conn.execute(
                "SELECT update_id FROM telegram_command_outbox WHERE id=?",
                (int(outbox_id),),
            ).fetchone()
            self.conn.execute(
                """
                UPDATE telegram_command_outbox
                SET status='failed', error=?, next_attempt_ts=?
                WHERE id=? AND status='sending'
                """,
                (error, int(now), int(outbox_id)),
            )
            self._telegram_audit(row["update_id"], "send", "failed", now, error)

    def mark_telegram_send_ambiguous(
        self, outbox_id: int, error: str, now: int, next_attempt_ts: int | None = None
    ) -> None:
        """Record that delivery is unproven and schedule another attempt.

        The outcome is genuinely unknown, so a retry may repeat a reply the
        user already saw. That is the better error: this is an answer the user
        explicitly asked for, and staying silent is indistinguishable from a
        dead bot.
        """
        with self.conn:
            row = self.conn.execute(
                "SELECT update_id FROM telegram_command_outbox WHERE id=?",
                (int(outbox_id),),
            ).fetchone()
            self.conn.execute(
                """
                UPDATE telegram_command_outbox
                SET status='ambiguous', error=?, next_attempt_ts=?
                WHERE id=? AND status='sending'
                """,
                (
                    error,
                    int(now if next_attempt_ts is None else next_attempt_ts),
                    int(outbox_id),
                ),
            )
            self._telegram_audit(row["update_id"], "send", "ambiguous", now, error)

    def complete_telegram_send(
        self,
        outbox_id: int,
        *,
        message_id: int,
        chat_id: int | str,
        telegram_date: int | None,
        now: int,
    ) -> None:
        """Atomically store acknowledgement, receipt, and terminal states."""
        with self.conn:
            row = self.conn.execute(
                """
                SELECT o.*, i.received_ts, i.authorized_ts, i.dispatched_ts,
                       i.rendered_ts
                FROM telegram_command_outbox o
                JOIN telegram_command_inbox i ON i.update_id=o.update_id
                WHERE o.id=? AND o.status='sending'
                """,
                (int(outbox_id),),
            ).fetchone()
            if row is None:
                raise sqlite3.IntegrityError("outbox send is not active")
            self.conn.execute(
                """
                INSERT INTO telegram_command_reply_receipts
                    (update_id, outbox_id, chat_id, message_id, telegram_date,
                     received_ts, authorized_ts, dispatched_ts, rendered_ts,
                     first_attempt_ts, acknowledged_ts, sent_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["update_id"],
                    int(outbox_id),
                    str(chat_id),
                    int(message_id),
                    int(telegram_date) if telegram_date is not None else None,
                    row["received_ts"],
                    row["authorized_ts"],
                    row["dispatched_ts"],
                    row["rendered_ts"],
                    row["first_attempt_ts"],
                    int(now),
                    int(now),
                ),
            )
            self.conn.execute(
                """
                UPDATE telegram_command_outbox
                SET status='sent', sent_ts=?, error=NULL
                WHERE id=?
                """,
                (int(now), int(outbox_id)),
            )
            self.conn.execute(
                """
                UPDATE telegram_command_inbox
                SET status='completed', completed_ts=?, error=NULL
                WHERE update_id=?
                """,
                (int(now), row["update_id"]),
            )
            self._telegram_audit(
                row["update_id"], "send", "acknowledged", now, f"message_id={message_id}"
            )

    def acquire_telegram_bot_lease(
        self, owner_id: str, now: int, lease_seconds: int
    ) -> bool:
        expires = int(now) + int(lease_seconds)
        with self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO telegram_bot_runtime
                    (singleton_id, owner_id, started_ts, heartbeat_ts,
                     lease_expires_ts)
                VALUES (1, ?, ?, ?, ?)
                """,
                (owner_id, int(now), int(now), expires),
            )
            cur = self.conn.execute(
                """
                UPDATE telegram_bot_runtime
                SET owner_id=?,
                    started_ts=CASE WHEN owner_id=? THEN started_ts ELSE ? END,
                    heartbeat_ts=?, lease_expires_ts=?
                WHERE singleton_id=1
                  AND (owner_id=? OR lease_expires_ts<=?)
                """,
                (
                    owner_id,
                    owner_id,
                    int(now),
                    int(now),
                    expires,
                    owner_id,
                    int(now),
                ),
            )
        return cur.rowcount == 1

    def telegram_bot_runtime(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM telegram_bot_runtime WHERE singleton_id=1"
        ).fetchone()
        return dict(row) if row is not None else None

    def log_telegram_poll(
        self,
        owner_id: str,
        *,
        started_ts: float,
        ended_ts: float,
        outcome: str,
        update_count: int = 0,
        error: str | None = None,
    ) -> None:
        """Trace one getUpdates attempt so stalls can be reconstructed later."""
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO telegram_poll_log
                    (owner_id, started_ts, ended_ts, duration_ms, outcome,
                     update_count, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_id,
                    float(started_ts),
                    float(ended_ts),
                    int((ended_ts - started_ts) * 1000),
                    outcome,
                    int(update_count),
                    error,
                ),
            )

    # -- Durable outbound notifications -------------------------------------
    def queue_notification(
        self,
        *,
        dedupe_key: str,
        kind: str,
        chat_id: int | str,
        body: str,
        now: int,
    ) -> bool:
        """Queue a notification exactly once. Returns False if already queued."""
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO telegram_notification_outbox
                    (dedupe_key, kind, chat_id, body, next_attempt_ts,
                     created_ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (dedupe_key, kind, str(chat_id), body, int(now), int(now)),
            )
        return cur.rowcount == 1

    def due_notifications(self, now: int) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM telegram_notification_outbox
            WHERE status IN ('pending', 'retry_wait') AND next_attempt_ts<=?
            ORDER BY id
            """,
            (int(now),),
        ).fetchall()
        return [dict(row) for row in rows]

    def begin_notification_send(self, notification_id: int, now: int) -> dict | None:
        """Claim a queued notification so only one sender can transmit it."""
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE telegram_notification_outbox
                SET status='sending', attempts=attempts + 1,
                    first_attempt_ts=COALESCE(first_attempt_ts, ?),
                    last_attempt_ts=?
                WHERE id=? AND status IN ('pending', 'retry_wait')
                """,
                (int(now), int(now), int(notification_id)),
            )
            if cur.rowcount != 1:
                return None
            row = self.conn.execute(
                "SELECT * FROM telegram_notification_outbox WHERE id=?",
                (int(notification_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def complete_notification_send(
        self, notification_id: int, *, message_id: int, now: int
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE telegram_notification_outbox
                SET status='sent', sent_ts=?, message_id=?, error=NULL
                WHERE id=? AND status='sending'
                """,
                (int(now), int(message_id), int(notification_id)),
            )

    def retry_notification_send(
        self, notification_id: int, error: str, next_attempt_ts: int
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE telegram_notification_outbox
                SET status='retry_wait', error=?, next_attempt_ts=?
                WHERE id=? AND status='sending'
                """,
                (error, int(next_attempt_ts), int(notification_id)),
            )

    def fail_notification_send(self, notification_id: int, error: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE telegram_notification_outbox
                SET status='failed', error=?
                WHERE id=? AND status='sending'
                """,
                (error, int(notification_id)),
            )

    def recover_notifications(self, now: int) -> int:
        """Requeue sends interrupted mid-flight by a restart."""
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE telegram_notification_outbox
                SET status='retry_wait',
                    error='process stopped while sending',
                    next_attempt_ts=?
                WHERE status='sending'
                """,
                (int(now),),
            )
        return cur.rowcount

    def notification(self, dedupe_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM telegram_notification_outbox WHERE dedupe_key=?",
            (dedupe_key,),
        ).fetchone()
        return dict(row) if row is not None else None

    def telegram_poll_log(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM telegram_poll_log ORDER BY id LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(row) for row in rows]

    def prune_telegram_poll_log(self, keep_seconds: int, now: float) -> int:
        """Keep the trace bounded; it is diagnostic, not a permanent record."""
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM telegram_poll_log WHERE started_ts < ?",
                (float(now) - float(keep_seconds),),
            )
        return cur.rowcount

    def record_telegram_poll(
        self, owner_id: str, now: int, *, ok: bool, error: str | None = None
    ) -> None:
        """Record the outcome of one getUpdates round trip for the lease owner."""
        with self.conn:
            if ok:
                self.conn.execute(
                    """
                    UPDATE telegram_bot_runtime
                    SET last_poll_ts=?, poll_failures=0, last_poll_error=NULL
                    WHERE singleton_id=1 AND owner_id=?
                    """,
                    (int(now), owner_id),
                )
            else:
                self.conn.execute(
                    """
                    UPDATE telegram_bot_runtime
                    SET poll_failures=poll_failures + 1, last_poll_error=?
                    WHERE singleton_id=1 AND owner_id=?
                    """,
                    (error, owner_id),
                )

    def release_telegram_bot_lease(self, owner_id: str, now: int) -> bool:
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE telegram_bot_runtime
                SET heartbeat_ts=?, lease_expires_ts=?
                WHERE singleton_id=1 AND owner_id=?
                """,
                (int(now), int(now), owner_id),
            )
        return cur.rowcount == 1

    def telegram_command_inbox(self, update_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM telegram_command_inbox WHERE update_id=?",
            (int(update_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def telegram_command_outbox(self, update_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM telegram_command_outbox WHERE update_id=?",
            (int(update_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def telegram_command_receipt(self, update_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM telegram_command_reply_receipts WHERE update_id=?",
            (int(update_id),),
        ).fetchone()
        return dict(row) if row is not None else None

    def telegram_command_audit(self, update_id: int) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT stage, outcome, occurred_ts, detail
            FROM telegram_command_audit WHERE update_id=? ORDER BY id
            """,
            (int(update_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- scheduler callback audit ------------------------------------------
    def start_scheduler_callback(self, callback_name: str, started_ts: int) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO scheduler_callback_runs (callback_name, started_ts)
            VALUES (?, ?)
            """,
            (callback_name, int(started_ts)),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_scheduler_callback(
        self,
        run_id: int,
        *,
        outcome: str,
        ended_ts: int,
        error: str | None = None,
    ) -> None:
        if outcome not in ("success", "error"):
            raise ValueError(f"invalid scheduler callback outcome: {outcome}")
        self.conn.execute(
            """
            UPDATE scheduler_callback_runs
            SET ended_ts=?, outcome=?, error=?, error_ts=?
            WHERE id=?
            """,
            (
                int(ended_ts),
                outcome,
                error,
                int(ended_ts) if error is not None else None,
                int(run_id),
            ),
        )
        self.conn.commit()

    def scheduler_callback_runs(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT id, callback_name, started_ts, ended_ts, outcome, error,
                   error_ts
            FROM scheduler_callback_runs
            ORDER BY id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    # -- reads --------------------------------------------------------------
    def count(self, table: str) -> int:
        # table name is internal/controlled, not user input
        return self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
