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


def telegram_bot_token() -> str | None:
    """Telegram bot token, read from the environment (never committed)."""
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def telegram_allowed_username() -> str | None:
    """The single username permitted to interact with the bot (no leading @)."""
    raw = os.environ.get("TELEGRAM_ALLOWED_USERNAME")
    return raw.lstrip("@") if raw else None
