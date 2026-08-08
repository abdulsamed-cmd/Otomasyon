-- Otomasyon SQLite schema.
-- Odds change over time, so odds live in a snapshot table (append-only) to
-- support closing-line value (CLV) and calibration analysis later.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS competitions (
    id            INTEGER PRIMARY KEY,   -- iddaa competition id (ci)
    name          TEXT NOT NULL,
    country_code  TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY,   -- iddaa event id
    home            TEXT NOT NULL,
    away            TEXT NOT NULL,
    competition_id  INTEGER,
    sport_id        INTEGER NOT NULL,
    start_ts        INTEGER NOT NULL,      -- unix seconds (UTC)
    status          INTEGER NOT NULL,
    first_seen_ts   INTEGER NOT NULL,
    last_seen_ts    INTEGER NOT NULL,
    FOREIGN KEY (competition_id) REFERENCES competitions(id)
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_ts);

CREATE TABLE IF NOT EXISTS markets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL,
    t           INTEGER NOT NULL,
    st          INTEGER NOT NULL,
    sov         TEXT,
    name        TEXT NOT NULL,
    status      INTEGER NOT NULL,
    UNIQUE (event_id, t, st, sov),
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS selections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   INTEGER NOT NULL,
    outcome_no  INTEGER NOT NULL,
    name        TEXT NOT NULL,
    UNIQUE (market_id, outcome_no),
    FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE CASCADE
);

-- Append-only odds history for CLV / movement analysis.
CREATE TABLE IF NOT EXISTS odds_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    selection_id  INTEGER NOT NULL,
    odd           REAL NOT NULL,
    web_odd       REAL,
    captured_ts   INTEGER NOT NULL,
    FOREIGN KEY (selection_id) REFERENCES selections(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_odds_selection ON odds_snapshots(selection_id, captured_ts);

-- Complete point-in-time bulletin captures. Unlike the entity tables above,
-- these membership tables preserve exactly which events, markets and priced
-- selections were visible in one fetch, enabling true forward replay.
CREATE TABLE IF NOT EXISTS bulletin_captures (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_ts   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bulletin_capture_ts
    ON bulletin_captures(captured_ts);

CREATE TABLE IF NOT EXISTS bulletin_capture_events (
    capture_id    INTEGER NOT NULL,
    event_id      INTEGER NOT NULL,
    status        INTEGER NOT NULL,
    PRIMARY KEY (capture_id, event_id),
    FOREIGN KEY (capture_id) REFERENCES bulletin_captures(id) ON DELETE CASCADE,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bulletin_capture_markets (
    capture_id    INTEGER NOT NULL,
    market_id     INTEGER NOT NULL,
    status        INTEGER NOT NULL,
    PRIMARY KEY (capture_id, market_id),
    FOREIGN KEY (capture_id) REFERENCES bulletin_captures(id) ON DELETE CASCADE,
    FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bulletin_capture_selections (
    capture_id    INTEGER NOT NULL,
    selection_id  INTEGER NOT NULL,
    odd           REAL NOT NULL,
    web_odd       REAL,
    PRIMARY KEY (capture_id, selection_id),
    FOREIGN KEY (capture_id) REFERENCES bulletin_captures(id) ON DELETE CASCADE,
    FOREIGN KEY (selection_id) REFERENCES selections(id) ON DELETE CASCADE
);

-- Historical results and basic pre-match odds from Mackolik's archive feed.
-- Used for chronological model training/backtesting; source_id prevents
-- duplicate backfill rows.
CREATE TABLE IF NOT EXISTS historical_matches (
    source_id       TEXT PRIMARY KEY,
    iddaa_code      INTEGER,
    start_ts        INTEGER NOT NULL,
    match_date      TEXT NOT NULL,
    competition_id  TEXT,
    competition     TEXT,
    home            TEXT NOT NULL,
    away            TEXT NOT NULL,
    home_key        TEXT NOT NULL,
    away_key        TEXT NOT NULL,
    ft_home         INTEGER NOT NULL,
    ft_away         INTEGER NOT NULL,
    ht_home         INTEGER,
    ht_away         INTEGER,
    odds_home       REAL,
    odds_draw       REAL,
    odds_away       REAL,
    odds_under25    REAL,
    odds_over25     REAL
);
CREATE INDEX IF NOT EXISTS idx_history_start ON historical_matches(start_ts);
CREATE INDEX IF NOT EXISTS idx_history_home ON historical_matches(home_key, start_ts);
CREATE INDEX IF NOT EXISTS idx_history_away ON historical_matches(away_key, start_ts);

CREATE TABLE IF NOT EXISTS clubelo_ratings (
    rating_date  TEXT NOT NULL,
    club_key     TEXT NOT NULL,
    club         TEXT NOT NULL,
    country      TEXT,
    level        INTEGER,
    elo          REAL NOT NULL,
    PRIMARY KEY (rating_date, club_key)
);
CREATE INDEX IF NOT EXISTS idx_clubelo_date ON clubelo_ratings(rating_date);

-- External context is stored separately and never changes coupon selection
-- unless its own chronological validation gate is explicitly passed.
CREATE TABLE IF NOT EXISTS fotmob_fixtures (
    match_id       INTEGER PRIMARY KEY,
    league_id      INTEGER,
    league_name    TEXT,
    home_id        INTEGER,
    home           TEXT NOT NULL,
    away_id        INTEGER,
    away           TEXT NOT NULL,
    start_ts       INTEGER NOT NULL,
    started        INTEGER NOT NULL,
    finished       INTEGER NOT NULL,
    cancelled      INTEGER NOT NULL,
    updated_ts     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS iddaa_fotmob_links (
    event_id       INTEGER PRIMARY KEY,
    match_id       INTEGER NOT NULL,
    match_score    REAL NOT NULL,
    linked_ts      INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
    FOREIGN KEY (match_id) REFERENCES fotmob_fixtures(match_id)
);

CREATE TABLE IF NOT EXISTS fotmob_context_captures (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id          INTEGER NOT NULL,
    captured_ts       INTEGER NOT NULL,
    started           INTEGER NOT NULL,
    finished          INTEGER NOT NULL,
    coverage_level    TEXT,
    xg_home           REAL,
    xg_away           REAL,
    lineup_available  INTEGER NOT NULL,
    home_starters     INTEGER NOT NULL,
    away_starters     INTEGER NOT NULL,
    UNIQUE (match_id, captured_ts),
    FOREIGN KEY (match_id) REFERENCES fotmob_fixtures(match_id)
);
CREATE INDEX IF NOT EXISTS idx_fotmob_context_match_time
    ON fotmob_context_captures(match_id, captured_ts);

CREATE TABLE IF NOT EXISTS fotmob_lineup_players (
    match_id       INTEGER NOT NULL,
    captured_ts    INTEGER NOT NULL,
    side           TEXT NOT NULL CHECK (side IN ('home', 'away')),
    player_id      INTEGER NOT NULL,
    name           TEXT NOT NULL,
    position_id    INTEGER,
    market_value   REAL,
    PRIMARY KEY (match_id, captured_ts, side, player_id),
    FOREIGN KEY (match_id, captured_ts)
        REFERENCES fotmob_context_captures(match_id, captured_ts)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fotmob_lineup_player
    ON fotmob_lineup_players(player_id, captured_ts);

-- Generated coupons (daily main/alt, surprise). No auto-play; informational.
CREATE TABLE IF NOT EXISTS coupons (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    kind             TEXT NOT NULL,        -- daily_main | daily_alt | surprise
    created_ts       INTEGER NOT NULL,
    for_date         TEXT NOT NULL,        -- YYYY-MM-DD (Europe/Istanbul)
    total_odds       REAL NOT NULL,
    combined_prob    REAL,
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending|won|lost|void
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS coupon_legs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    coupon_id         INTEGER NOT NULL,
    event_id          INTEGER NOT NULL,
    market_t          INTEGER NOT NULL,
    market_st         INTEGER NOT NULL,
    market_sov        TEXT,
    market_name       TEXT NOT NULL,
    outcome_no        INTEGER NOT NULL,
    outcome_name      TEXT NOT NULL,
    odd_at_creation   REAL NOT NULL,
    fair_prob         REAL,
    closing_odd       REAL,
    result            TEXT NOT NULL DEFAULT 'pending',  -- pending|win|lose|void
    FOREIGN KEY (coupon_id) REFERENCES coupons(id) ON DELETE CASCADE,
    FOREIGN KEY (event_id) REFERENCES events(id)
);

-- Simple key/value app settings (e.g. the Telegram chat id to notify).
CREATE TABLE IF NOT EXISTS app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- Final match results used to settle coupons (source: Mackolik archive, etc.).
CREATE TABLE IF NOT EXISTS results (
    event_id     INTEGER PRIMARY KEY,
    home_score   INTEGER,
    away_score   INTEGER,
    ht_home      INTEGER,
    ht_away      INTEGER,
    status       TEXT NOT NULL DEFAULT 'final',  -- final|postponed|cancelled
    source       TEXT,
    updated_ts   INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(id)
);

-- Weekly surprise laboratory is intentionally separate from daily coupons.
CREATE TABLE IF NOT EXISTS surprise_reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    period_key    TEXT NOT NULL UNIQUE, -- ISO year-week
    created_ts    INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS surprise_candidates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id     INTEGER NOT NULL,
    event_id      INTEGER NOT NULL,
    category      TEXT NOT NULL,
    market_t      INTEGER NOT NULL,
    market_st     INTEGER NOT NULL,
    market_sov    TEXT,
    outcome_name  TEXT NOT NULL,
    odd           REAL NOT NULL,
    fair_prob     REAL,
    in_system     INTEGER NOT NULL DEFAULT 0,
    result        TEXT NOT NULL DEFAULT 'pending',
    UNIQUE (report_id, event_id, category),
    FOREIGN KEY (report_id) REFERENCES surprise_reports(id) ON DELETE CASCADE,
    FOREIGN KEY (event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS surprise_scenarios (
    report_id     INTEGER NOT NULL,
    system_size   INTEGER NOT NULL,
    columns       INTEGER NOT NULL,
    unit_stake    REAL NOT NULL,
    profit        REAL,
    roi           REAL,
    PRIMARY KEY (report_id, system_size),
    FOREIGN KEY (report_id) REFERENCES surprise_reports(id) ON DELETE CASCADE
);
