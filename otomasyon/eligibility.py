"""Competition-quality filters for the low-risk daily process.

Friendly and development/reserve competitions have unstable motivation,
lineups and substitution patterns. They are excluded from the *daily safe*
coupon, regardless of how attractive the market odds look. The surprise lab
remains a separate process and can apply its own policy.
"""

from __future__ import annotations

import unicodedata

_EXCLUDED_TOKENS = (
    "hazirlik",
    "friendly",
    "friendlies",
    "u19",
    "u20",
    "u21",
    "u23",
    "youth",
    "gencler",
    "rezerv",
    "reserve",
    "development",
    "academy",
)


def _fold(text: str) -> str:
    text = (text or "").casefold().replace("ı", "i")
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )


def competition_exclusion_reason(name: str) -> str | None:
    folded = _fold(name)
    for token in _EXCLUDED_TOKENS:
        if token in folded:
            return f"excluded competition token: {token}"
    return None


def is_daily_eligible(competition_name: str) -> bool:
    return competition_exclusion_reason(competition_name) is None
