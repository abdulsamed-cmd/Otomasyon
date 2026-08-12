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
# Open-Meteo charges by response size and answers 429 when pushed, so a venue
# is worth waiting for rather than dropping.
WEATHER_RETRIES = 6
WEATHER_BACKOFF = 5.0  # seconds, exponential base
# Politeness gap between venue requests during a backfill.
WEATHER_REQUEST_GAP = 1.0
# A forecast a day or two out barely moves, so collecting it is a few-times-a-day
# job rather than a per-cycle one.
WEATHER_POLL_INTERVAL_SECONDS = 6 * 3600
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

# The same questions asked of a half rather than of the whole match, and of one
# team rather than of both. A half-time score settles all of them, and iddaa
# prices them at the same ~18% margin as the full-match markets above.
MARKET_HT_RESULT = (2, 88)         # 1. Yarı Sonucu (1 / 0 / 2)
MARKET_HT_DOUBLE_CHANCE = (2, 77)  # 1. Yarı Çifte Şans
MARKET_HT_OVER_UNDER = (2, 60)     # 1. Yarı Alt/Üst {line}
MARKET_HT_BTTS = (2, 720)          # 1. Yarı Karşılıklı Gol (Var/Yok)
MARKET_SECOND_HALF_RESULT = (2, 36)  # 2. Yarı Sonucu (1 / 0 / 2)
MARKET_HIGHER_SCORING_HALF = (2, 6)  # Hangi Yarıda Daha Fazla Gol Olur
MARKET_HOME_OVER_UNDER = (2, 603)     # Ev Sahibi Alt/Üst {line}
MARKET_AWAY_OVER_UNDER = (2, 604)     # Deplasman Alt/Üst {line}
MARKET_HOME_HT_OVER_UNDER = (2, 722)  # Ev Sahibi 1. Yarı Altı/Üstü {line}
MARKET_AWAY_HT_OVER_UNDER = (2, 723)  # Deplasman 1. Yarı Altı/Üstü {line}

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
#
# Asking the same match about its halves and about each team separately is what
# widens the board: a coupon takes at most one leg per match, so the extra
# families are not extra legs, they are better-priced answers to the one leg
# that match is allowed to contribute.
#
# Tek/Çift is settleable and priced at the same margin, but it is deliberately
# absent: goal parity landed 50.0% against a 50.0% price over 1,638 archived
# legs, which is a coin no amount of team knowledge can call.
DAILY_COUPON_MARKETS = (
    MARKET_DOUBLE_CHANCE,
    MARKET_OVER_UNDER,
    MARKET_BTTS,
    MARKET_MATCH_RESULT,
    MARKET_HT_RESULT,
    MARKET_HT_DOUBLE_CHANCE,
    MARKET_HT_OVER_UNDER,
    MARKET_HT_BTTS,
    MARKET_SECOND_HALF_RESULT,
    MARKET_HIGHER_SCORING_HALF,
    MARKET_HOME_OVER_UNDER,
    MARKET_AWAY_OVER_UNDER,
    MARKET_HOME_HT_OVER_UNDER,
    MARKET_AWAY_HT_OVER_UNDER,
)
# Hard ceiling on the margin of any market a leg may come from, so a market
# that is repriced upwards drops out on its own.
MARKET_MAX_MARGIN = 0.20

# --- Coupon rules ----------------------------------------------------------
# Each coupon is asked for the highest win probability it can reach at or above
# a minimum payout. There is no upper bound and no preferred leg count: the
# main coupon exists to land often, the alternative to reach a 2.00+ return, and
# the search decides how many matches each of those takes.
DAILY_MAIN_MIN_ODDS = 1.50
DAILY_ALT_MIN_ODDS = 2.00
DAILY_MIN_LEGS = 1
DAILY_MAX_LEGS = 5

# Sanity floor for a leg: below this the outcome is simply unlikely, whatever
# the search thinks of it. It is deliberately low, because the objective
# already punishes weak legs - a single leg paying 2.00 is a ~0.44 shot and
# must stay eligible.
LEG_MIN_FAIR_PROB = 0.35
# Under this price a leg barely moves the total, so it only adds another market
# margin. There is no ceiling: a single leg is allowed to carry a coupon.
LEG_MIN_ODD = 1.15
LEG_MAX_ODD = 4.00
# Singles and pairs are searched over the whole pool. Three legs and up are
# searched over the likeliest legs only, because the number of combinations
# explodes; the cap shrinks as the build grows so the work stays bounded.
COMBO_CAPS = {3: 64, 4: 40, 5: 28}
# How many candidate partners each leg keeps while pairing. One match can offer
# a dozen legs, so this stays wide enough that discarding a whole match still
# leaves a real partner behind.
PAIR_SUFFIX_WIDTH = 16

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

# Once the day's coupon has kicked off, the matches that are left can carry a
# follow-up. Looking for one costs a bulletin fetch, and on days when nothing
# eligible is left there is nothing to find, so the search is spaced out.
DAILY_FOLLOW_UP_RETRY_SECONDS = 15 * 60

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
# Memory settings measured on a June-August 2026 holdout. A 60-day half-life
# left the median team with only 3.4 effective matches, but simply lengthening
# it barely moved out-of-sample log loss; what actually helped was shrinking
# each team harder toward its league baseline. The pair below was the best
# combination tried (1X2 log loss 1.0545 and O/U 0.6825, against 1.0567 and
# 0.6905 for the old settings).
MODEL_LOOKBACK_DAYS = 540
MODEL_HALF_LIFE_DAYS = 240.0
MODEL_PRIOR_MATCHES = 35.0
MODEL_MIN_TEAM_MATCHES = 6
MODEL_MIN_EDGE = 0.04
# Confidence is the share of a team's estimate that comes from its own matches
# rather than the league prior, so its scale moves whenever the prior does.
# Gates are therefore stated as a match count and converted, which keeps them
# meaning the same thing after a shrinkage change.
MODEL_MIN_CONFIDENCE_MATCHES = 6
MODEL_ELO_K = 20.0
MODEL_ELO_HOME_ADVANTAGE = 60.0
MODEL_ELO_BLEND = 0.70
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

# --- Calibration layer -----------------------------------------------------
# The layer pools the market price and the model on the log-odds scale and
# measures what each is worth. Fitting needs a decent sample per market family
# before its coefficients mean anything.
CALIBRATION_MIN_SAMPLES = 2000
# Days of history the nightly fit trains on, and the holdout it is scored on.
CALIBRATION_FIT_DAYS = 400
CALIBRATION_HOLDOUT_DAYS = 60
# The model may only start influencing the stated probability once the fit
# shows it saving at least this much log loss over the calibrated market on
# its own holdout. Until then the layer reproduces the market on purpose.
CALIBRATION_MIN_MODEL_GAIN = 0.002

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
