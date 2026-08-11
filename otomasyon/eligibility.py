"""Competition-quality filters for the low-risk daily process.

Friendly and development/reserve competitions have unstable motivation,
lineups and substitution patterns. Amateur and regional tiers are excluded on
top of that: the daily coupon is meant to run on senior competitive football,
and those divisions are neither. They are all excluded from the *daily safe*
coupon regardless of how attractive the market odds look. The surprise lab
remains a separate process and can apply its own policy.
"""

from __future__ import annotations

import re
import unicodedata

_EXCLUDED_TOKENS = (
    "hazirlik",
    "friendly",
    "friendlies",
    "amator",
    "amateur",
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


# Dutch reserve sides are named "Jong <club>" with no token the suffix rules
# would catch, so they reach the daily coupon as if they were senior teams.
_RESERVE_PREFIXES = ("jong ",)

# Senior clubs whose real name ends the way a reserve side would. Without this
# the suffix rule throws away legitimate top-flight fixtures.
_SENIOR_DESPITE_SUFFIX = frozenset(
    {
        "willem ii",
        "juan pablo ii",
    }
)


def team_exclusion_reason(name: str) -> str | None:
    folded = _fold(name).strip()
    if any(
        token in folded
        for token in ("academy", "akademi", "reserves", "rezerv", "youth")
    ):
        return "development team"
    if folded.startswith(_RESERVE_PREFIXES):
        return "reserve team prefix"
    if re.search(r"\b(?:u|under|sub)[ -]?\d{2}\b", folded):
        return "age-group team"
    if folded in _SENIOR_DESPITE_SUFFIX:
        return None
    if re.search(r"(?:\s|-)(?:ii|b|2)$", folded):
        return "reserve team suffix"
    return None


def is_event_eligible(competition_name: str, home: str, away: str) -> bool:
    return (
        is_daily_eligible(competition_name)
        and team_exclusion_reason(home) is None
        and team_exclusion_reason(away) is None
    )
