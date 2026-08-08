"""Resolve iddaa market ``(t, st)`` codes to human-readable names.

Market names come from ``get_market_config`` (keyed by ``f"{t}_{st}"``). Names
may contain a ``{0}`` placeholder for the line value, which we fill from the
market's ``sov`` field (e.g. ``"Alt/Üst {0}"`` + sov ``"2.5"`` -> ``"Alt/Üst 2.5"``).
"""

from __future__ import annotations

from typing import Any


class MarketResolver:
    def __init__(self, market_config: dict[str, Any]) -> None:
        # market_config is the ``data`` dict from get_market_config; markets
        # live under key ``m``.
        self._markets: dict[str, Any] = market_config.get("m", {})

    @classmethod
    def from_client(cls, client) -> "MarketResolver":
        return cls(client.get_market_config())

    def name(self, t: int, st: int, sov: str | None = None) -> str:
        """Human-readable market name, with the line substituted when present."""
        entry = self._markets.get(f"{t}_{st}")
        template = entry.get("n") if entry else None
        if not template:
            return f"market {t}_{st}"
        if "{0}" in template:
            return template.replace("{0}", str(sov) if sov not in (None, "") else "").strip()
        return template

    def description(self, t: int, st: int) -> str | None:
        entry = self._markets.get(f"{t}_{st}")
        return entry.get("d") if entry else None

    def known(self, t: int, st: int) -> bool:
        return f"{t}_{st}" in self._markets
