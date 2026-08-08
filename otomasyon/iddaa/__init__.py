"""iddaa JSON API client and data normalization."""

from .client import IddaaClient, IddaaError
from .markets import MarketResolver
from .normalize import (
    NormalizedEvent,
    NormalizedMarket,
    NormalizedSelection,
    normalize_events,
)

__all__ = [
    "IddaaClient",
    "IddaaError",
    "MarketResolver",
    "NormalizedEvent",
    "NormalizedMarket",
    "NormalizedSelection",
    "normalize_events",
]
