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

# Markets prioritised when building LOW-RISK daily coupons.
LOW_RISK_MARKETS = (
    MARKET_DOUBLE_CHANCE,
    MARKET_OVER_UNDER,
    MARKET_BTTS,
    MARKET_MATCH_RESULT,  # only when a strong favourite exists (enforced by engine)
)

# --- Coupon rules ----------------------------------------------------------
DAILY_MIN_TOTAL_ODDS = 2.00
DAILY_MAX_TOTAL_ODDS = 3.00
DAILY_MIN_LEGS = 2
DAILY_MAX_LEGS = 4

# A leg is only "safe" enough for the daily coupon if its fair (margin-free)
# probability clears this floor.
LEG_MIN_FAIR_PROB = 0.55
# Match Result (1X2) is riskier, so only allow it for a strong favourite.
FAVORITE_MIN_FAIR_PROB = 0.60
# Only legs whose odds sit in this band are useful for building a 2-4 leg
# coupon inside the 2.00-3.00 window: below the floor a leg barely moves the
# product; the ceiling keeps individual legs reasonably safe.
LEG_MIN_ODD = 1.20
LEG_MAX_ODD = 1.90
# Triples/quads are searched over the top-N safest legs (perf guard). Pairs are
# always searched over the full pool so good higher-odd pairs are never missed.
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
PERFORMANCE_GATE_MIN_COUPONS = {
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
