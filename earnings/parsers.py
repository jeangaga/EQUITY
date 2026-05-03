"""Parsers for Q1 2026 earnings recap + stock-by-stock notes.

The parsers are intentionally tolerant of whitespace and extra blank lines but
strict about the documented block markers (e.g. ``<<TICKER_EARNINGS_BEGIN>>``).
They never invent or summarize content — they only re-shape what the text
already says.
"""

from __future__ import annotations

import re
from io import StringIO
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Vocabularies (reference values from the spec). These are NOT enforced by the
# parsers — the underlying text wins — but the UI uses them for filter ordering
# and for color/badge mapping.
# ---------------------------------------------------------------------------

IMPORTANCE_LEVELS: list[str] = ["*", "**", "***", "****"]
STATUS_LEVELS: list[str] = [
    "Pending",
    "Scouted",
    "Brief",
    "Done",
    "Out_of_window",
    "Excluded",
]
STATE_TRANSITIONS: list[str] = [
    "Improvement",
    "Continuation",
    "Stabilization",
    "Reversal",
    "Deterioration",
    "False dawn",
    "Mixed",
]

# Sections that may appear inside a company block, in canonical order.
COMPANY_SECTIONS: list[str] = [
    "EXEC SUMMARY",
    "REPORTED FACTS",
    "SEGMENT / BUSINESS BREAKDOWN",
    "GUIDANCE / CAPITAL ALLOCATION",
    "PROFESSIONAL MARKET COMMENTARY",
    "COMMENT — ANALYST LAYER",
    "CONTEXT FROM PRIOR QUARTER / PRIOR DEBATE",
    "HF TAKE — STRATEGIST LAYER",
    "MARKET REACTION / PEER READ-ACROSS",
    "KEY ISSUES TO MONITOR",
    "BOTTOM LINE",
]

# Metadata keys at the top of a company block, before any section header.
COMPANY_META_KEYS: list[str] = [
    "TICKER",
    "COMPANY",
    "SECTOR",
    "COUNTRY",
    "PUBLI_DATE",
    "EVENT",
    "STATE_TRANSITION",
    "IMPORTANCE",
    "THEMES",
    "SEGMENT_TABLE_STATUS",
    "SOURCE_ROLE",
]

# Macro PM Read-Across sub-sections (uppercase headers in the recap text).
MACRO_SECTIONS: list[str] = [
    "US GROWTH",
    "US CONSUMER",
    "FINANCIAL CONDITIONS / CREDIT",
    "AI / CAPEX / INFRASTRUCTURE",
    "EUROPE",
    "ASIA",
    "CROSS-ASSET PM TAKE",
    "BOTTOM LINE",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BLOCK_RE = re.compile(
    r"<<\s*([A-Z0-9_\.\-]+?)_BEGIN\s*>>(.*?)<<\s*\1_END\s*>>",
    re.DOTALL,
)
_COMPANY_BLOCK_RE = re.compile(
    r"<<\s*([A-Z0-9\.\-]+)_EARNINGS_BEGIN\s*>>(.*?)<<\s*\1_EARNINGS_END\s*>>",
    re.DOTALL,
)
_KV_LINE_RE = re.compile(r"^\s*([A-Z][A-Z0-9 _/\-]*?)\s*:\s*(.+?)\s*$")
_SECTOR_BRACKET_RE = re.compile(
    r"^\s*\[\s*(?P<ticker>[^\—|\[\]]+?)\s+[—\-]\s+"
    r"(?P<company>.+?)\s+[—\-]\s+"
    r"(?P<sector>.+?)\s+[—\-]\s+"
    r"(?P<country>.+?)\s*\|\s*"
    r"PUBLI_DATE\s*:\s*(?P<date>[^|]+?)\s*\|\s*"
    r"IMPORTANCE\s*:\s*(?P<importance>\*+|\*?)\s*\]\s*$"
)


def _extract_block(text: str, name: str) -> str | None:
    """Return the body of ``<<NAME_BEGIN>> ... <<NAME_END>>`` or ``None``."""
    pat = re.compile(
        rf"<<\s*{re.escape(name)}_BEGIN\s*>>(.*?)<<\s*{re.escape(name)}_END\s*>>",
        re.DOTALL,
    )
    m = pat.search(text)
    return m.group(1).strip("\n") if m else None


def _extract_all_blocks(text: str, name: str) -> list[str]:
    """Return every body of ``<<NAME_BEGIN>> ... <<NAME_END>>`` (in order)."""
    pat = re.compile(
        rf"<<\s*{re.escape(name)}_BEGIN\s*>>(.*?)<<\s*{re.escape(name)}_END\s*>>",
        re.DOTALL,
    )
    return [m.group(1).strip("\n") for m in pat.finditer(text)]


def _parse_kv_lines(text: str) -> dict[str, str]:
    """Parse top-of-block ``KEY: value`` lines into a dict (preserves order)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _KV_LINE_RE.match(line)
        if not m:
            continue
        key, val = m.group(1).strip(), m.group(2).strip()
        out[key] = val
    return out


def _stars_to_count(s: str) -> int:
    """``'***'`` -> 3.  Tolerant of unicode look-alikes."""
    s = (s or "").strip()
    return sum(1 for c in s if c == "*")


# ---------------------------------------------------------------------------
# Recap file
# ---------------------------------------------------------------------------


def parse_earnings_recap(text: str) -> dict[str, Any]:
    """Parse the season recap file into a structured dict.

    Returns
    -------
    dict with keys:
        meta : dict             -- season-level KV header (SEASON, AS_OF, ...)
        macro : dict[str, str]  -- {section_name: paragraph_text}
        sectors : list[dict]    -- {sector, stocks: [{...}]}
        themes : list[dict]     -- {theme, commentary}
    """
    body = _extract_block(text, "EARNINGS_SEASON_RECAP")
    if body is None:
        # Tolerate a file that was supplied without the outer wrapper.
        body = text

    # ---- meta header (lines before the first sub-block) --------------------
    meta_text = body.split("<<", 1)[0]
    meta = _parse_kv_lines(meta_text)

    # ---- global macro PM read-across --------------------------------------
    macro_body = _extract_block(body, "GLOBAL_MACRO_PM_READ_ACROSS") or ""
    macro = _parse_macro_sections(macro_body)

    # ---- sector recap ------------------------------------------------------
    sector_recap = _extract_block(body, "SECTOR_RECAP") or ""
    sectors = [
        _parse_sector_block(b)
        for b in _extract_all_blocks(sector_recap, "SECTOR")
    ]

    # ---- key cross-themes --------------------------------------------------
    themes_body = _extract_block(body, "KEY_CROSS_THEMES") or ""
    themes = _parse_themes_block(themes_body)

    return {"meta": meta, "macro": macro, "sectors": sectors, "themes": themes}


def _parse_macro_sections(text: str) -> dict[str, str]:
    """Split the macro read-across body into {HEADER: paragraph}.

    Headers are ALL-CAPS lines (incl. spaces, slashes, dashes) on their own
    line; the body is everything until the next header.
    """
    out: dict[str, str] = {}
    if not text.strip():
        return out

    lines = text.splitlines()
    # find index of every header line
    header_idx: list[tuple[int, str]] = []
    header_re = re.compile(r"^[A-Z][A-Z0-9 /\-\(\)\&\.]+$")
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        # Header heuristic: ALL-CAPS short line, no terminal punctuation.
        if header_re.match(line) and len(line) <= 60 and not line.endswith("."):
            # also: must not contain lowercase letters (already ensured)
            header_idx.append((i, line))

    for k, (start, name) in enumerate(header_idx):
        end = header_idx[k + 1][0] if k + 1 < len(header_idx) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        if body:
            out[name] = body
    return out


def _parse_sector_block(text: str) -> dict[str, Any]:
    """Parse one ``<<SECTOR_BEGIN>> ... <<SECTOR_END>>`` body."""
    sector_name = ""
    stocks: list[dict[str, Any]] = []

    # Walk lines: SECTOR: header, then alternating bracket-line + paragraph.
    lines = text.splitlines()
    current_meta: dict[str, Any] | None = None
    current_buf: list[str] = []

    def _flush() -> None:
        nonlocal current_meta, current_buf
        if current_meta is not None:
            current_meta["commentary"] = " ".join(
                ln.strip() for ln in current_buf if ln.strip()
            ).strip()
            stocks.append(current_meta)
        current_meta = None
        current_buf = []

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if not s:
            # blank line — keep accumulating, blanks delimit paragraphs visually
            continue
        if s.upper().startswith("SECTOR:"):
            sector_name = s.split(":", 1)[1].strip()
            continue
        m = _SECTOR_BRACKET_RE.match(s)
        if m:
            _flush()
            d = m.groupdict()
            current_meta = {
                "ticker": d["ticker"].strip(),
                "company": d["company"].strip(),
                "sector": d["sector"].strip(),
                "country": d["country"].strip(),
                "publi_date": d["date"].strip(),
                "importance": d["importance"].strip(),
                "importance_n": _stars_to_count(d["importance"]),
                "sector_group": sector_name,
                "commentary": "",
            }
            current_buf = []
            continue
        # part of the current commentary paragraph
        if current_meta is not None:
            current_buf.append(s)

    _flush()
    return {"sector": sector_name, "stocks": stocks}


def _parse_themes_block(text: str) -> list[dict[str, str]]:
    """Parse repeating ``THEME: ...`` / ``COMMENTARY: ...`` pairs."""
    themes: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    cur_field: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s:
            cur_field = None
            continue
        if s.upper().startswith("THEME:"):
            if cur:
                themes.append(cur)
            cur = {"theme": s.split(":", 1)[1].strip(), "commentary": ""}
            cur_field = "theme"
            continue
        if s.upper().startswith("COMMENTARY:"):
            if cur is None:
                cur = {"theme": "", "commentary": ""}
            cur["commentary"] = s.split(":", 1)[1].strip()
            cur_field = "commentary"
            continue
        # continuation line
        if cur is not None and cur_field == "commentary":
            cur["commentary"] = (cur["commentary"] + " " + s).strip()

    if cur:
        themes.append(cur)
    return themes


# ---------------------------------------------------------------------------
# Stock file
# ---------------------------------------------------------------------------


def parse_scout_tracker(text: str) -> pd.DataFrame:
    """Parse ``<<SCOUT_TRACKER_BEGIN>>`` rows into a DataFrame.

    Each row has the form::

        TICKER | Sector | Country | PUBLI_DATE: YYYY-MM-DD | IMPORTANCE: *** | STATUS: Done
    """
    body = _extract_block(text, "SCOUT_TRACKER") or ""
    rows: list[dict[str, Any]] = []
    window = ""
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("WINDOW:"):
            window = line.split(":", 1)[1].strip()
            continue
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 6:
            continue
        ticker, sector, country = parts[0], parts[1], parts[2]
        kv: dict[str, str] = {}
        for p in parts[3:]:
            if ":" in p:
                k, v = p.split(":", 1)
                kv[k.strip().upper()] = v.strip()
        rows.append(
            {
                "Ticker": ticker,
                "Sector": sector,
                "Country": country,
                "Publi Date": kv.get("PUBLI_DATE", ""),
                "Importance": kv.get("IMPORTANCE", ""),
                "Importance N": _stars_to_count(kv.get("IMPORTANCE", "")),
                "Status": kv.get("STATUS", ""),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df.attrs["window"] = window
    return df


def parse_company_blocks(text: str) -> list[dict[str, Any]]:
    """Detect every ``<<TICKER_EARNINGS_BEGIN>>`` block and parse it.

    Returns a list of company dicts in document order. Tickers are NOT
    hard-coded — any block matching the ``[TICKER]_EARNINGS_BEGIN`` shape is
    picked up.
    """
    blocks: list[dict[str, Any]] = []
    for m in _COMPANY_BLOCK_RE.finditer(text):
        ticker = m.group(1).strip()
        body = m.group(2)
        block = _parse_one_company_block(body, ticker_hint=ticker)
        blocks.append(block)
    return blocks


def _parse_one_company_block(body: str, *, ticker_hint: str) -> dict[str, Any]:
    """Parse the body of one company block."""
    lines = body.splitlines()

    # Step 1: split off the metadata header (consecutive KEY: value lines, then
    # the first all-caps section header marks the start of the sections).
    section_start = _find_first_section_start(lines)
    meta_lines = lines[:section_start]
    section_lines = lines[section_start:]

    meta_kv = _parse_kv_lines("\n".join(meta_lines))

    ticker = meta_kv.get("TICKER", ticker_hint).strip()
    themes_raw = meta_kv.get("THEMES", "")
    themes = [t.strip() for t in re.split(r"[,;]", themes_raw) if t.strip()]

    sections = _split_sections(section_lines)

    # Pull out the segment table (if any) from the SEGMENT/BUSINESS section.
    seg_section_text = sections.get("SEGMENT / BUSINESS BREAKDOWN", "")
    seg_table_df, seg_commentary = _split_segment_table_and_commentary(
        seg_section_text
    )
    if seg_table_df is not None and not seg_table_df.empty:
        # Replace the section text with just the commentary part — the table is
        # rendered separately in the UI.
        sections["SEGMENT / BUSINESS BREAKDOWN"] = seg_commentary

    importance = meta_kv.get("IMPORTANCE", "").strip()

    return {
        "ticker": ticker,
        "company": meta_kv.get("COMPANY", "").strip(),
        "sector": meta_kv.get("SECTOR", "").strip(),
        "country": meta_kv.get("COUNTRY", "").strip(),
        "publi_date": meta_kv.get("PUBLI_DATE", "").strip(),
        "event": meta_kv.get("EVENT", "").strip(),
        "state_transition": meta_kv.get("STATE_TRANSITION", "").strip(),
        "importance": importance,
        "importance_n": _stars_to_count(importance),
        "themes": themes,
        "segment_table_status": meta_kv.get("SEGMENT_TABLE_STATUS", "").strip(),
        "source_role": meta_kv.get("SOURCE_ROLE", "").strip(),
        "sections": sections,
        "segment_table": seg_table_df,
    }


def _find_first_section_start(lines: list[str]) -> int:
    """Return the index of the first section header line in ``lines``.

    A section header is one of the canonical headers in :data:`COMPANY_SECTIONS`
    (matched by upper-cased equality after stripping). If none are found,
    returns ``len(lines)``.
    """
    canon = {h.upper() for h in COMPANY_SECTIONS}
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        if s.upper() in canon:
            return i
    return len(lines)


def _split_sections(lines: list[str]) -> dict[str, str]:
    """Split the post-metadata body into ``{SECTION_HEADER: body_text}``.

    Headers are only those listed in :data:`COMPANY_SECTIONS` (compared
    upper-case). Anything that is not a recognized header is treated as content
    of the current section.
    """
    canon = {h.upper(): h for h in COMPANY_SECTIONS}

    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        nonlocal buf, current
        if current is not None:
            text = "\n".join(buf).strip("\n")
            # collapse trailing whitespace but keep paragraph breaks
            sections[current] = text.rstrip()
        buf = []

    for raw in lines:
        s = raw.strip()
        upper = s.upper()
        if upper in canon and current != canon[upper]:
            _flush()
            current = canon[upper]
            continue
        if current is None:
            # content before any header — ignore (shouldn't happen after split)
            continue
        buf.append(raw)

    _flush()
    return sections


# ---------------------------------------------------------------------------
# Segment table
# ---------------------------------------------------------------------------

_SEGMENT_HEADER_RE = re.compile(
    r"^\s*Segment\s*\|\s*%\s*of\s*Total\s*\|\s*Revenue\s*\|\s*"
    r"YoY\s*Growth\s*\|\s*Margin\s*Trend\s*\|\s*Key\s*Commentary\s*$",
    re.IGNORECASE,
)


def parse_segment_table(section_text: str) -> pd.DataFrame | None:
    """Extract the segment table from a 'SEGMENT / BUSINESS BREAKDOWN' body.

    Returns a DataFrame, or ``None`` if no table is present.
    """
    df, _ = _split_segment_table_and_commentary(section_text)
    return df


def _split_segment_table_and_commentary(
    section_text: str,
) -> tuple[pd.DataFrame | None, str]:
    """Locate the pipe-delimited segment table and split off the commentary.

    Returns ``(dataframe_or_None, commentary_text)``. The commentary is the
    text *after* the table (typically introduced by a 'COMMENTARY' header).
    """
    if not section_text or "|" not in section_text:
        return None, section_text or ""

    lines = section_text.splitlines()
    header_idx = -1
    for i, raw in enumerate(lines):
        if _SEGMENT_HEADER_RE.match(raw):
            header_idx = i
            break
    if header_idx == -1:
        return None, section_text

    # Collect contiguous pipe-rows after the header. Stop at first non-pipe,
    # non-blank line (that's where the commentary begins).
    data_rows: list[list[str]] = []
    j = header_idx + 1
    while j < len(lines):
        row = lines[j]
        if not row.strip():
            j += 1
            continue
        if "|" not in row:
            break
        parts = [c.strip() for c in row.split("|")]
        if len(parts) < 6:
            break
        # take exactly 6 columns; merge any extras into Key Commentary
        if len(parts) > 6:
            parts = parts[:5] + [" | ".join(parts[5:])]
        data_rows.append(parts)
        j += 1

    columns = [
        "Segment",
        "% of Total",
        "Revenue",
        "YoY Growth",
        "Margin Trend",
        "Key Commentary",
    ]
    df = pd.DataFrame(data_rows, columns=columns) if data_rows else None

    # Commentary: everything after the table, with an optional 'COMMENTARY'
    # header line stripped.
    tail_lines = lines[j:]
    # Drop leading blanks and an optional 'COMMENTARY' label
    while tail_lines and not tail_lines[0].strip():
        tail_lines.pop(0)
    if tail_lines and tail_lines[0].strip().upper() == "COMMENTARY":
        tail_lines.pop(0)
        while tail_lines and not tail_lines[0].strip():
            tail_lines.pop(0)
    commentary = "\n".join(tail_lines).rstrip()

    # Also keep anything that appeared *before* the SEGMENT TABLE label
    # (rare, but defensive). We treat the lines between the start and the
    # 'SEGMENT TABLE' label as part of the section preface.
    preface_lines: list[str] = []
    for raw in lines[:header_idx]:
        s = raw.strip()
        if not s or s.upper() == "SEGMENT TABLE":
            continue
        preface_lines.append(raw)
    if preface_lines:
        preface = "\n".join(preface_lines).rstrip()
        commentary = (preface + "\n\n" + commentary).strip() if commentary else preface

    return df, commentary


# ---------------------------------------------------------------------------
# Aggregations for the UI
# ---------------------------------------------------------------------------


def build_company_dataframe(
    company_blocks: list[dict[str, Any]],
) -> pd.DataFrame:
    """Flatten company blocks into a DataFrame for filtering / sorting."""
    if not company_blocks:
        return pd.DataFrame(
            columns=[
                "Ticker",
                "Company",
                "Sector",
                "Country",
                "Publi Date",
                "Event",
                "Importance",
                "Importance N",
                "State Transition",
                "Themes",
                "Segment Table Status",
                "Source Role",
            ]
        )

    rows = []
    for c in company_blocks:
        rows.append(
            {
                "Ticker": c["ticker"],
                "Company": c["company"],
                "Sector": c["sector"],
                "Country": c["country"],
                "Publi Date": c["publi_date"],
                "Event": c["event"],
                "Importance": c["importance"],
                "Importance N": c["importance_n"],
                "State Transition": c["state_transition"],
                "Themes": ", ".join(c["themes"]),
                "Themes List": c["themes"],
                "Segment Table Status": c["segment_table_status"],
                "Source Role": c["source_role"],
            }
        )
    df = pd.DataFrame(rows)
    # Try to coerce Publi Date to datetime for sorting; ignore on failure.
    df["Publi Date"] = pd.to_datetime(df["Publi Date"], errors="coerce")
    return df


def collect_unique_themes(company_blocks: list[dict[str, Any]]) -> list[str]:
    """Return the sorted unique set of themes across all company blocks."""
    themes: set[str] = set()
    for c in company_blocks:
        for t in c["themes"]:
            themes.add(t)
    return sorted(themes, key=str.casefold)


def companies_for_theme(
    theme: str, company_blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return company blocks whose THEMES metadata contains ``theme`` (ci)."""
    needle = theme.strip().casefold()
    return [c for c in company_blocks if any(needle == t.casefold() for t in c["themes"])]
