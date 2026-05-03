"""Earnings module — parses Q1 2026 earnings recap + stock-by-stock notes."""

from .loader import load_text_from_github, load_recap_text, load_stock_text
from .parsers import (
    parse_earnings_recap,
    parse_scout_tracker,
    parse_company_blocks,
    parse_segment_table,
    build_company_dataframe,
    IMPORTANCE_LEVELS,
    STATUS_LEVELS,
    STATE_TRANSITIONS,
)

__all__ = [
    "load_text_from_github",
    "load_recap_text",
    "load_stock_text",
    "parse_earnings_recap",
    "parse_scout_tracker",
    "parse_company_blocks",
    "parse_segment_table",
    "build_company_dataframe",
    "IMPORTANCE_LEVELS",
    "STATUS_LEVELS",
    "STATE_TRANSITIONS",
]
