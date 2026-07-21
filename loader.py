"""Text loaders for the earnings module.

Reads the two earnings text files for a given earnings season (quarter) from
a GitHub repository (raw URLs). Falls back to a local ``data/`` folder if the
network is unavailable, which makes local development and unit-testing
painless. The list of selectable seasons lives in ``AVAILABLE_QUARTERS``.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

try:  # Streamlit is optional at parse time (lets the module be unit-tested).
    import streamlit as st

    _cache_data = st.cache_data
except Exception:  # pragma: no cover - only triggered without streamlit
    def _cache_data(*_a, **_kw):  # type: ignore[no-redef]
        def deco(fn):
            return fn

        return deco


# Default raw GitHub locations. Override with env vars if you fork the repo.
GITHUB_OWNER = os.environ.get("EARNINGS_GH_OWNER", "jeangaga")
GITHUB_REPO = os.environ.get("EARNINGS_GH_REPO", "EQUITY")
GITHUB_BRANCH = os.environ.get("EARNINGS_GH_BRANCH", "main")
GITHUB_DATA_PATH = os.environ.get("EARNINGS_GH_DATA_PATH", "data")

# Earnings seasons available in the repo, newest first. The UI exposes these
# in a quarter toggle; DEFAULT_QUARTER is what every section shows on launch.
AVAILABLE_QUARTERS = ["Q2 2026", "Q1 2026"]
DEFAULT_QUARTER = "Q2 2026"


def _quarter_filenames(quarter: str) -> tuple[str, str]:
    """Return ``(recap_filename, stock_filename)`` for a season label.

    ``"Q2 2026"`` -> ``EARNINGS_SEASON_RECAP_Q2_2026.txt`` /
    ``EARNINGS_RELEASES_BY_STOCK_Q2_2026.txt``.
    """
    tag = quarter.strip().replace(" ", "_")
    return (
        f"EARNINGS_SEASON_RECAP_{tag}.txt",
        f"EARNINGS_RELEASES_BY_STOCK_{tag}.txt",
    )


def _raw_url(filename: str) -> str:
    return (
        f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
        f"{GITHUB_BRANCH}/{GITHUB_DATA_PATH}/{filename}"
    )


def _local_path(filename: str) -> Path:
    """Path to the bundled local copy (sibling ``data/`` folder)."""
    return Path(__file__).resolve().parent.parent / "data" / filename


@_cache_data(show_spinner=False, ttl=60 * 30)
def load_text_from_github(url: str) -> str:
    """Fetch a raw .txt file from GitHub. Cached for 30 min via Streamlit.

    Raises ``requests.HTTPError`` on 4xx/5xx so the caller can handle a fallback.
    """
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def _load_with_fallback(filename: str) -> tuple[str, str]:
    """Load ``filename`` from GitHub, falling back to a local copy.

    Returns ``(text, source_label)`` where source_label is one of ``"github"``
    or ``"local"`` so the UI can surface the source if it wants to.
    """
    url = _raw_url(filename)
    try:
        return load_text_from_github(url), "github"
    except Exception:
        local = _local_path(filename)
        if local.exists():
            return local.read_text(encoding="utf-8"), "local"
        raise FileNotFoundError(
            f"Could not load {filename} from GitHub ({url}) and no local copy "
            f"exists at {local}."
        )


def load_recap_text(quarter: str = DEFAULT_QUARTER) -> tuple[str, str]:
    """Return ``(text, source_label)`` for the season recap file of ``quarter``."""
    return _load_with_fallback(_quarter_filenames(quarter)[0])


def load_stock_text(quarter: str = DEFAULT_QUARTER) -> tuple[str, str]:
    """Return ``(text, source_label)`` for the stock-by-stock file of ``quarter``."""
    return _load_with_fallback(_quarter_filenames(quarter)[1])
