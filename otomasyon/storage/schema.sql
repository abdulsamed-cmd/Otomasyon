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
