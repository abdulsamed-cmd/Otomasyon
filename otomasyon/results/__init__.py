"""Automatic football result sources and event matching."""

from .mackolik import MackolikClient, SourceMatch
from .matcher import match_source_results

__all__ = ["MackolikClient", "SourceMatch", "match_source_results"]
