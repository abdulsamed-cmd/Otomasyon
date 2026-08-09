"""Match Mackolik results to iddaa events.

Primary key: Mackolik ``iddaaCode`` == iddaa event id (exact).
Fallback: normalized home/away names + start-time proximity, with a strict
threshold. Ambiguous fallback matches are deliberately skipped; no result is
better than settling the wrong coupon.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .. import config
from ..settlement import MatchResult

_NOISE = {
    "fc", "fk", "sk", "cf", "afc", "bk", "club", "futbol", "football",
    "spor", "k", "w", "women", "kadinlar", "reserves", "rezerv",
}


def normalize_team(name: str) -> str:
    text = name.casefold().replace("ı", "i")
    text = "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    words = re.findall(r"[a-z0-9]+", text)
    return " ".join(word for word in words if word not in _NOISE)


def team_similarity(left: str, right: str) -> float:
    a, b = normalize_team(left), normalize_team(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _status(source) -> str:
    sub = source.substate.casefold()
    if sub == "postponed":
        return "postponed"
    if sub in ("cancelled", "canceled"):
        return "cancelled"
    return "final"


def _to_result(event_id: int, source) -> MatchResult:
    return MatchResult(
        event_id=event_id,
        ft_home=source.ft_home,
        ft_away=source.ft_away,
        ht_home=source.ht_home,
        ht_away=source.ht_away,
        status=_status(source),
        source=f"mackolik:{source.source_id}",
    )


def match_source_results(
    events: list[dict],
    source_matches: list,
    *,
    threshold: float = config.RESULT_FUZZY_THRESHOLD,
) -> tuple[dict[int, MatchResult], list[dict]]:
    """Return ``(matched_results, diagnostics)``.

    Event dicts require ``event_id``, ``home``, ``away``, ``start_ts``.
    Diagnostics identify exact/fuzzy/unmatched decisions without exposing any
    secret data.
    """
    targets = {int(event["event_id"]): event for event in events}
    matched: dict[int, MatchResult] = {}
    diagnostics: list[dict] = []

    # Exact iddaaCode matches first.
    for source in source_matches:
        if not source.is_decided:
            continue
        if source.iddaa_code in targets:
            event_id = int(source.iddaa_code)
            matched[event_id] = _to_result(event_id, source)
            diagnostics.append(
                {"event_id": event_id, "method": "iddaa_code", "score": 1.0}
            )

    # Strict fallback for records missing iddaaCode.
    unmatched_ids = [event_id for event_id in targets if event_id not in matched]
    for event_id in unmatched_ids:
        event = targets[event_id]
        candidates: list[tuple[float, object]] = []
        for source in source_matches:
            if not source.is_decided or source.iddaa_code is not None:
                continue
            # Same calendar feed, but require <= 3h to avoid same-name fixtures.
            hours = abs(source.start_ts - int(event["start_ts"])) / 3600
            if hours > 3:
                continue
            home = team_similarity(event["home"], source.home)
            away = team_similarity(event["away"], source.away)
            name_score = (home + away) / 2
            time_score = max(0.0, 1.0 - hours / 6)
            score = 0.9 * name_score + 0.1 * time_score
            if score >= threshold:
                candidates.append((score, source))
        candidates.sort(key=lambda item: item[0], reverse=True)
        # Require a clear winner over the second candidate.
        if candidates and (
            len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.05
        ):
            score, source = candidates[0]
            matched[event_id] = _to_result(event_id, source)
            diagnostics.append(
                {"event_id": event_id, "method": "fuzzy", "score": round(score, 3)}
            )
        else:
            diagnostics.append(
                {"event_id": event_id, "method": "unmatched", "score": 0.0}
            )

    return matched, diagnostics
