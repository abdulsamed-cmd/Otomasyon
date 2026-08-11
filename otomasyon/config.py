"""Central configuration and domain constants for Otomasyon."""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo

# --- Locale / time ---------------------------------------------------------
TIMEZONE = ZoneInfo("Europe/Istanbul")

# --- iddaa JSON API --------------------------------------------------------
# Public, unauthenticated JSON endpoints used by the iddaa web/mobile client.
SPORTSBOOK_BASE = "https://sportsbookv2.iddaa.com/sportsbook"

SPORT_FOOTBALL = 1  # iddaa sport id for football

# HTTP client behaviour
HTTP_TIMEOUT = 25  # seconds
HTTP_RETRIES = 3
HTTP_BACKOFF = 1.5  # seconds, exponential base
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# --- Storage ---------------------------------------------------------------
DB_PATH = os.environ.get("OTOMASYON_DB", os.path.join("data", "otomasyon.db"))

# --- Market codes ----------------------------------------------------------
# Each iddaa market is identified by a (type, subtype) pair == config key
# ``f"{t}_{st}"``. These are the FULL-MATCH football markets we rely on. Names
# are always resolved at runtime from ``get_market_config`` so they stay
# correct even if iddaa relabels a market; these codes only pin *which* market.
#
# Verified against a live get_market_config snapshot.
MARKET_MATCH_RESULT = (1, 1)      # Maç Sonucu (1X2)
MARKET_DOUBLE_CHANCE = (2, 92)    # Çifte Şans (1X / 12 / X2)
MARKET_OVER_UNDER = (2, 101)      # Alt/Üst {line}  (line in market.sov)
MARKET_BTTS = (2, 89)             # Karşılıklı Gol (Var/Yok)
MARKET_TOTAL_GOALS_BAND = (2, 4)  # Toplam Gol (0-1 / 2-3 / 4-5 / 6+)
MARKET_HTFT = (2, 90)             # İlk Yarı / Maç Sonucu
MARKET_ODD_EVEN = (2, 91)         # Tek / Çift
MARKET_HT_DOUBLE_CHANCE = (2, 77)  # 1. Yarı Çifte Şans

# Each double chance selection wins on two of the three results, so its three
# fair probabilities sum to 2 rather than 1. Normalising them to 1 halves every
# double chance probability, which silently hides the safest market in the
# bulletin behind any probability floor.
MARKET_OUTCOME_COVERAGE = {
    MARKET_DOUBLE_CHANCE: 2,
    MARKET_HT_DOUBLE_CHANCE: 2,
}

# Markets the daily coupon may draw a leg from. Measured on a live bulletin,
# these all price at a ~18% margin, the cheapest iddaa offers. Combination
# markets (score+goals, handicap, goal bands, half/full time) charge 21-24% for
# the same money and are excluded: every extra point of margin comes straight
# out of the hit rate. Only markets ``settlement.settle_leg`` can decide are
# listed, so a coupon can never contain a leg we cannot grade.
DAILY_COUPON_MARKETS = (
    MARKET_DOUBLE_CHANCE,
    MARKET_OVER_UNDER,
    MARKET_BTTS,
    MARKET_MATCH_RESULT,
)
# Hard ceiling on the margin of any market a leg may come from, so a market
# that is repriced upwards drops out on its own.
MARKET_MAX_MARGIN = 0.20

# --- Coupon rules ----------------------------------------------------------
# The coupon targets a ~2.00 return. The band is what the engine is allowed to
# land on while it hunts for the highest win probability at that price.
DAILY_MIN_TOTAL_ODDS = 1.85
DAILY_MAX_TOTAL_ODDS = 2.15
# One leg is allowed, and preferred: every additional leg multiplies another
# market margin into the coupon, so at the same total odds a 2-leg build wins
# far less often than a single. Measured on 75k archived matches, a single
# selection priced 1.85-2.15 landed 41.6% of the time, while two legs of ~1.41
# paying the same 2.00 landed 32.7%.
DAILY_MIN_LEGS = 1
DAILY_MAX_LEGS = 4

# Sanity floor for a leg: below this the outcome is simply unlikely, whatever
# the search thinks of it. It is deliberately low, because the objective
# already punishes weak legs - a single leg paying 2.00 is a ~0.44 shot and
# must stay eligible.
LEG_MIN_FAIR_PROB = 0.35
# A leg is useless outside this band: under the floor it barely moves the
# total, over the ceiling it alone overshoots the target price.
LEG_MIN_ODD = 1.15
LEG_MAX_ODD = DAILY_MAX_TOTAL_ODDS
# Triples/quads are searched over the top-N safest legs (perf guard). Singles
# and pairs are always searched over the full pool.
COMBO_CAP = 64

# --- Surprise lab ----------------------------------------------------------
# High-goal category, mapped to (market_code, outcome_name, line).
SURPRISE_CATEGORIES = {
    "goals_6plus": (MARKET_TOTAL_GOALS_BAND, "6+ gol", None),
}
# On-demand surprise scans a wider window (covers the upcoming weekend).
SURPRISE_WINDOW_HOURS = 72
# How many candidates per category to shortlist, and how many go into the
# system-play set.
SURPRISE_PER_CATEGORY = 6
SURPRISE_SYSTEM_SIZE = 6
# Theoretical unit stake per column (TL) used to show minimum system cost.
SURPRISE_UNIT_STAKE = 20.0
# Canonical scenario used for the surprise process evidence gate. Other system
# sizes are still persisted and reported, but are not pooled into this ROI.
SURPRISE_TRACK_SYSTEM_SIZE = 2

# --- Telegram polling liveness ---------------------------------------------
# A long poll holds an idle connection open, which NAT gateways drop silently.
# A short poll bounds how long a dropped connection can hide new messages, and
# keep-alive probes on the socket surface the drop sooner still.
TELEGRAM_POLL_TIMEOUT_SECONDS = 15
# A failed poll is retried almost immediately: the user is already waiting.
TELEGRAM_POLL_RETRY_BASE_SECONDS = 0.5
TELEGRAM_POLL_RETRY_MAX_SECONDS = 5.0
# Consecutive poll failures before the connection pool is rebuilt outright.
TELEGRAM_POLL_RESET_AFTER_FAILURES = 3
# Transport-level retries are deliberately disabled. Each internal retry
# restarts the timeout from zero, so a stalled poll multiplies into minutes of
# invisibility. The bot redials itself after every failure instead, which keeps
# a single attempt bounded by the timeouts below.
TELEGRAM_POLL_CONNECT_RETRIES = 0
# Connecting must fail fast; only the long poll itself is allowed to be slow.
TELEGRAM_CONNECT_TIMEOUT_SECONDS = 10
# The bot polls every ~15s and the scheduler wakes every ~25s. A shared silence
# longer than this means the host stopped executing, not that a request failed.
HOST_STALL_SECONDS = 90
# Poll traces are diagnostic; keep a bounded window.
TELEGRAM_POLL_LOG_RETENTION_SECONDS = 3 * 24 * 60 * 60

# Outbound notifications (settled coupons, summaries) are queued and retried
# until Telegram acknowledges them, so a transient failure delays a result
# instead of losing it.
NOTIFICATION_MAX_ATTEMPTS = 8
NOTIFICATION_RETRY_BASE_SECONDS = 5
NOTIFICATION_RETRY_MAX_SECONDS = 300
# Lease held by the live bot process. A crashed process cannot answer, so the
# lease must expire quickly enough for a restart to take over unnoticed.
TELEGRAM_BOT_LEASE_SECONDS = 45
# Supervisor restart backoff after an unexpected bot exit.
TELEGRAM_SUPERVISOR_BACKOFF_SECONDS = 2.0
TELEGRAM_SUPERVISOR_MAX_BACKOFF_SECONDS = 30.0
# The scheduler warns the user when the bot stops polling for this long, so a
# dead bot is announced instead of silently swallowing commands.
TELEGRAM_WATCHDOG_STALE_SECONDS = 5 * 60

# --- Proactive delivery ----------------------------------------------------
# Local hour (Europe/Istanbul) at which the daily coupon is pushed to the user
# even if they never type "bugün". Chosen to be well before typical kickoffs.
DAILY_PUSH_HOUR = 10

# Poll Mackolik for completed matches while the bot is running. The persisted
# last-poll timestamp prevents duplicate work across bot restarts.
RESULT_POLL_INTERVAL_SECONDS = 15 * 60
CONTEXT_POLL_INTERVAL_SECONDS = 30 * 60
SCHEDULER_INTERVAL_SECONDS = 25
HISTORY_ARCHIVE_HOUR = 4
HISTORY_ARCHIVE_POLL_INTERVAL_SECONDS = 60 * 60
HISTORY_ARCHIVE_RETRY_DAYS = 7
MODEL_STATUS_PUSH_HOUR = 9
MODEL_STATUS_PUSH_MINUTE = 45
XG_SYNC_POLL_INTERVAL_SECONDS = 60 * 60
# Only used when a Mackolik record lacks ``iddaaCode``. Exact event-id matching
# is always preferred.
RESULT_FUZZY_THRESHOLD = 0.84

# --- Contextual model / backtest ------------------------------------------
MODEL_LOOKBACK_DAYS = 365
MODEL_HALF_LIFE_DAYS = 60.0
MODEL_PRIOR_MATCHES = 8.0
MODEL_MIN_TEAM_MATCHES = 6
MODEL_MIN_EDGE = 0.04
MODEL_ELO_K = 20.0
MODEL_ELO_HOME_ADVANTAGE = 60.0
MODEL_ELO_BLEND = 0.45
# Blend observed goals with pre-existing match xG when available. xG belongs
# only to training rows before the prediction cutoff.
MODEL_XG_BLEND = 0.90
# Safety gate: model remains report/backtest-only until explicitly enabled
# after sufficient out-of-sample evidence.
MODEL_LIVE_ENABLED = False
MODEL_SHADOW_VERSION = "xg-ou-v1"
MODEL_GOAL_SHADOW_VERSION = "goal-elo-ou-v1"
MODEL_WALK_FORWARD_VERSION = "xg-ou-v1-wf"
MODEL_GATE_MIN_BETS = 200
MODEL_GATE_MIN_ROI_CI_LOW = 0.0

# Independent evidence gates. Main, alternative and surprise results are never
# pooled because they represent different risk processes.
# Evidence is counted in settled *matches*, not coupons. Each leg is one
# prediction we made, so a 2-4 leg coupon contributes 2-4 data points and the
# sample grows several times faster than one-per-day.
PERFORMANCE_GATE_MIN_MATCHES = {
    "daily_main": 200,
    "daily_alt": 200,
    "surprise": 100,
}

# --- Private dashboard -----------------------------------------------------
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")
DASHBOARD_SECRET_KEY = os.environ.get("DASHBOARD_SECRET_KEY")


def telegram_bot_token() -> str | None:
    """Telegram bot token, read from the environment (never committed)."""
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def telegram_allowed_username() -> str | None:
    """The single username permitted to interact with the bot (no leading @)."""
    raw = os.environ.get("TELEGRAM_ALLOWED_USERNAME")
    return raw.lstrip("@") if raw else None
