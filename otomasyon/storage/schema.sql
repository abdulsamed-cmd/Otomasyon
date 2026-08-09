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

CREATE TABLE IF NOT EXISTS understat_matches (
    source_id     TEXT PRIMARY KEY,
    league        TEXT NOT NULL,
    season        INTEGER NOT NULL,
    start_ts      INTEGER NOT NULL,
    home          TEXT NOT NULL,
    away          TEXT NOT NULL,
    ft_home       INTEGER NOT NULL,
    ft_away       INTEGER NOT NULL,
    xg_home       REAL NOT NULL,
    xg_away       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_understat_start ON understat_matches(start_ts);

CREATE TABLE IF NOT EXISTS historical_xg_links (
    understat_source_id  TEXT PRIMARY KEY,
    historical_source_id TEXT NOT NULL UNIQUE,
    match_score          REAL NOT NULL,
    FOREIGN KEY (understat_source_id)
        REFERENCES understat_matches(source_id) ON DELETE CASCADE,
    FOREIGN KEY (historical_source_id)
        REFERENCES historical_matches(source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS model_predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version   TEXT NOT NULL,
    for_date        TEXT NOT NULL,
    event_id        INTEGER NOT NULL,
    captured_ts     INTEGER NOT NULL,
    market          TEXT NOT NULL,
    outcome_name    TEXT NOT NULL,
    odd             REAL NOT NULL,
    predicted_prob  REAL NOT NULL,
    market_fair     REAL NOT NULL,
    edge            REAL NOT NULL,
    result          TEXT NOT NULL DEFAULT 'pending',
    UNIQUE (model_version, for_date, event_id, market),
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_model_predictions_status
    ON model_predictions(model_version, result);

CREATE TABLE IF NOT EXISTS walk_forward_predictions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version     TEXT NOT NULL,
    historical_source_id TEXT NOT NULL,
    prediction_date   TEXT NOT NULL,
    cutoff_ts         INTEGER NOT NULL,
    market            TEXT NOT NULL,
    outcome_name      TEXT NOT NULL,
    odd               REAL NOT NULL,
    predicted_prob    REAL NOT NULL,
    market_fair       REAL NOT NULL,
    edge              REAL NOT NULL,
    actual_result     TEXT NOT NULL,
    won               INTEGER NOT NULL,
    profit            REAL NOT NULL,
    xg_samples_home   INTEGER NOT NULL,
    xg_samples_away   INTEGER NOT NULL,
    created_ts        INTEGER NOT NULL,
    UNIQUE (model_version, historical_source_id, market),
    FOREIGN KEY (historical_source_id)
        REFERENCES historical_matches(source_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_walk_forward_model_date
    ON walk_forward_predictions(model_version, prediction_date);

CREATE TABLE IF NOT EXISTS model_training_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date          TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    trained_ts        INTEGER NOT NULL,
    cutoff_ts         INTEGER NOT NULL,
    history_matches   INTEGER NOT NULL,
    xg_matches        INTEGER NOT NULL,
    team_scopes       INTEGER NOT NULL,
    status            TEXT NOT NULL,
    UNIQUE (run_date, model_version)
);

CREATE TABLE IF NOT EXISTS model_artifacts (
    run_date          TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    trained_ts        INTEGER NOT NULL,
    cutoff_ts         INTEGER NOT NULL,
    sha256            TEXT NOT NULL,
    payload           BLOB NOT NULL,
    PRIMARY KEY (run_date, model_version)
);

CREATE TABLE IF NOT EXISTS model_team_features (
    run_date          TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    team_key          TEXT NOT NULL,
    venue             TEXT NOT NULL,
    weight            REAL NOT NULL,
    scored            REAL NOT NULL,
    conceded          REAL NOT NULL,
    matches           INTEGER NOT NULL,
    xg_matches        INTEGER NOT NULL,
    elo               REAL,
    PRIMARY KEY (run_date, model_version, team_key, venue)
);
CREATE INDEX IF NOT EXISTS idx_model_features_team
    ON model_team_features(model_version, team_key, run_date);

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

-- Auditable acknowledgement returned by Telegram after a successful send.
CREATE TABLE IF NOT EXISTS telegram_delivery_receipts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,
    notification_date TEXT NOT NULL,
    dedupe_key     TEXT NOT NULL UNIQUE,
    chat_id        TEXT NOT NULL,
    message_id     INTEGER NOT NULL,
    telegram_date  INTEGER,
    sent_ts        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telegram_delivery_kind_sent
    ON telegram_delivery_receipts(kind, sent_ts);

-- Telegram command transport is intentionally independent from scheduler
-- delivery receipts.  Updates are acknowledged to Telegram only after the raw
-- payload is durable, then rendered replies move through a persistent outbox.
CREATE TABLE IF NOT EXISTS telegram_command_inbox (
    update_id       INTEGER PRIMARY KEY,
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'processing', 'outbox_ready', 'completed',
                        'render_error'
                    )),
    authorized      INTEGER,
    command         TEXT,
    chat_id         TEXT,
    received_ts     INTEGER NOT NULL,
    authorized_ts   INTEGER,
    dispatched_ts   INTEGER,
    rendered_ts     INTEGER,
    completed_ts    INTEGER,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_telegram_command_inbox_status
    ON telegram_command_inbox(status, update_id);

CREATE TABLE IF NOT EXISTS telegram_command_outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id       INTEGER NOT NULL UNIQUE,
    chat_id         TEXT NOT NULL,
    reply_text      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'sending', 'retry_wait', 'sent',
                        'ambiguous', 'failed'
                    )),
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_ts INTEGER NOT NULL,
    created_ts      INTEGER NOT NULL,
    first_attempt_ts INTEGER,
    last_attempt_ts INTEGER,
    sent_ts         INTEGER,
    error           TEXT,
    FOREIGN KEY (update_id)
        REFERENCES telegram_command_inbox(update_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_telegram_command_outbox_due
    ON telegram_command_outbox(status, next_attempt_ts, update_id);

CREATE TABLE IF NOT EXISTS telegram_command_reply_receipts (
    update_id       INTEGER PRIMARY KEY,
    outbox_id       INTEGER NOT NULL UNIQUE,
    chat_id         TEXT NOT NULL,
    message_id      INTEGER NOT NULL,
    telegram_date   INTEGER,
    received_ts     INTEGER NOT NULL,
    authorized_ts   INTEGER NOT NULL,
    dispatched_ts   INTEGER NOT NULL,
    rendered_ts     INTEGER NOT NULL,
    first_attempt_ts INTEGER NOT NULL,
    acknowledged_ts INTEGER NOT NULL,
    sent_ts         INTEGER NOT NULL,
    FOREIGN KEY (update_id)
        REFERENCES telegram_command_inbox(update_id) ON DELETE CASCADE,
    FOREIGN KEY (outbox_id)
        REFERENCES telegram_command_outbox(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS telegram_command_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id       INTEGER NOT NULL,
    stage           TEXT NOT NULL
                    CHECK (stage IN (
                        'authorization', 'dispatch', 'render', 'send'
                    )),
    outcome         TEXT NOT NULL,
    occurred_ts     INTEGER NOT NULL,
    detail          TEXT,
    FOREIGN KEY (update_id)
        REFERENCES telegram_command_inbox(update_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_telegram_command_audit_update
    ON telegram_command_audit(update_id, id);

-- One bot process may own the polling lease.  Heartbeats make stale ownership
-- recoverable without coupling the independently deployed scheduler process.
-- ``last_poll_ts`` advances only on a completed getUpdates round trip, so a bot
-- that is running but unable to reach Telegram is distinguishable from a
-- healthy one and can be reported as stale.
CREATE TABLE IF NOT EXISTS telegram_bot_runtime (
    singleton_id    INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    owner_id        TEXT NOT NULL,
    started_ts      INTEGER NOT NULL,
    heartbeat_ts    INTEGER NOT NULL,
    lease_expires_ts INTEGER NOT NULL,
    last_poll_ts    INTEGER,
    poll_failures   INTEGER NOT NULL DEFAULT 0,
    last_poll_error TEXT
);

-- Durable lifecycle audit for each scheduler callback invocation.
CREATE TABLE IF NOT EXISTS scheduler_callback_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    callback_name  TEXT NOT NULL,
    started_ts     INTEGER NOT NULL,
    ended_ts       INTEGER,
    outcome        TEXT NOT NULL DEFAULT 'running'
                   CHECK (outcome IN ('running', 'success', 'error')),
    error          TEXT,
    error_ts       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_scheduler_callback_name_started
    ON scheduler_callback_runs(callback_name, started_ts);

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
