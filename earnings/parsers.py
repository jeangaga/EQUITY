"""Parsers for Q1 2026 earnings recap + stock-by-stock notes (v2.3).

The parsers are intentionally tolerant of whitespace and extra blank lines but
strict about the documented block markers (e.g. ``<<TICKER_EARNINGS_BEGIN>>``).
They never invent or summarize content — they only re-shape what the text
already says.

Both v2.3 and the older block layout are supported: see
``LEGACY_SECTION_ALIASES`` and the ``PUBLICATION_DATE`` / ``PUBLI_DATE``
fallback in ``_parse_one_company_block``.
"""

from __future__ import annotations

import re
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

# Canonical sections that may appear inside a company block (v2.3 + legacy).
# Order here is the *recognized* order; the renderer chooses display order.
# REPORTED FACTS is kept for legacy detection only — at parse time it is
# normalized to OFFICIAL EARNINGS DETAIL so the rest of the app sees one name.
COMPANY_SECTIONS: list[str] = [
    "EXEC SUMMARY",
    "HEADLINE EARNINGS TABLE",          # v2.3
    "SECTOR KPI TABLE",                 # v2.3
    "OFFICIAL EARNINGS DETAIL",         # v2.3 (replaces REPORTED FACTS)
    "REPORTED FACTS",                   # legacy alias -> normalized
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

# Legacy section header -> canonical (v2.3) name.
LEGACY_SECTION_ALIASES: dict[str, str] = {
    "REPORTED FACTS": "OFFICIAL EARNINGS DETAIL",
}

# Metadata keys at the top of a company block, before any section header.
# Both v2.3 and legacy keys are recognized; consumers should read the
# v2.3 names from the parsed dict and only fall back to the legacy keys
# when the v2.3 ones are absent.
COMPANY_META_KEYS: list[str] = [
    "TICKER",
    "COMPANY",
    "SECTOR",
    "SUBSECTOR",                # v2.3
    "COUNTRY",
    "PUBLICATION_DATE",         # v2.3 (legacy: PUBLI_DATE)
    "PUBLI_DATE",               # legacy fallback
    "EVENT",
    "STATE_TRANSITION",
    "IMPORTANCE",
    "MARKET_REACTION",          # v2.3
    "MARKET_REACTION_DETAIL",   # v2.3
    "PM_READ",                  # v2.3
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

# Bracket lines inside <<SECTOR_BEGIN>> blocks. Two layouts are accepted:
#
#   v2.4 (current — 5 fields between brackets, em-dash separated):
#     [TICKER — Company — Sector — Subsector — Country
#      | PUBLICATION_DATE: YYYY-MM-DD | IMPORTANCE: *** ]
#
#   legacy (4 fields, no subsector, "PUBLI_DATE" key):
#     [TICKER — Company — Sector — Country
#      | PUBLI_DATE: YYYY-MM-DD | IMPORTANCE: *** ]
#
# We try the 5-field shape first; the parser falls back to the 4-field shape
# below. Both forms accept either ``PUBLICATION_DATE`` or ``PUBLI_DATE`` as the
# date label. Em dashes are tolerated as ``—`` or ``-``.
_SECTOR_BRACKET_RE_V24 = re.compile(
    r"^\s*\[\s*(?P<ticker>[^\—|\[\]]+?)\s+[—\-]\s+"
    r"(?P<company>.+?)\s+[—\-]\s+"
    r"(?P<sector>.+?)\s+[—\-]\s+"
    r"(?P<subsector>.+?)\s+[—\-]\s+"
    r"(?P<country>.+?)\s*\|\s*"
    r"(?:PUBLICATION_DATE|PUBLI_DATE)\s*:\s*(?P<date>[^|]+?)\s*\|\s*"
    r"IMPORTANCE\s*:\s*(?P<importance>\*+|\*?)\s*\]\s*$"
)
_SECTOR_BRACKET_RE_LEGACY = re.compile(
    r"^\s*\[\s*(?P<ticker>[^\—|\[\]]+?)\s+[—\-]\s+"
    r"(?P<company>.+?)\s+[—\-]\s+"
    r"(?P<sector>.+?)\s+[—\-]\s+"
    r"(?P<country>.+?)\s*\|\s*"
    r"(?:PUBLICATION_DATE|PUBLI_DATE)\s*:\s*(?P<date>[^|]+?)\s*\|\s*"
    r"IMPORTANCE\s*:\s*(?P<importance>\*+|\*?)\s*\]\s*$"
)


def _match_sector_bracket(line: str) -> dict[str, str] | None:
    """Return groupdict for a sector bracket line, or ``None`` if no match.

    The dict always contains ``subsector`` (empty string in the legacy 4-field
    layout) so downstream code does not need to special-case it.
    """
    m = _SECTOR_BRACKET_RE_V24.match(line)
    if m:
        return m.groupdict()
    m = _SECTOR_BRACKET_RE_LEGACY.match(line)
    if m:
        d = m.groupdict()
        d["subsector"] = ""
        return d
    return None


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
        sectors : list[dict]    -- {sector, state_transition, importance,
                                   importance_n, evidence_tickers, summary}
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
            header_idx.append((i, line))

    for k, (start, name) in enumerate(header_idx):
        end = header_idx[k + 1][0] if k + 1 < len(header_idx) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        if body:
            out[name] = body
    return out


# Field labels inside a v3 <<SECTOR_BEGIN>> block, in document order.
_SECTOR_FIELD_LABELS: list[str] = [
    "SECTOR",
    "SECTOR_STATE_TRANSITION",
    "SECTOR_IMPORTANCE",
    "EVIDENCE_TICKERS",
    "SUMMARY",
]


def _parse_sector_block(text: str) -> dict[str, Any]:
    """Parse one ``<<SECTOR_BEGIN>> ... <<SECTOR_END>>`` body (v3 recap format).

    The block is a flat set of labelled fields::

        SECTOR: <name>
        SECTOR_STATE_TRANSITION: <state>
        SECTOR_IMPORTANCE: <stars>
        EVIDENCE_TICKERS: <comma-separated tickers>
        SUMMARY: <free-text paragraph, may span lines>

    Labels may all share one line or be split across lines; each value runs
    until the next recognised label. The per-stock bracket entries of the old
    format are gone -- the Sector Dashboard now lists stocks from the company
    blocks and uses this block only for the sector-level header.

    Returns a dict with keys: ``sector``, ``state_transition``, ``importance``,
    ``importance_n``, ``evidence_tickers`` (list[str]), ``summary``.
    """
    flat = " ".join(ln.strip() for ln in text.splitlines())
    flat = " ".join(flat.split())  # collapse runs of whitespace

    # Locate each label; first occurrence wins. Values run to the next label.
    positions: list[tuple[int, int, str]] = []
    for label in _SECTOR_FIELD_LABELS:
        m = re.search(rf"\b{label}\s*:", flat)
        if m:
            positions.append((m.start(), m.end(), label))
    positions.sort()

    fields: dict[str, str] = {}
    for i, (_start, label_end, label) in enumerate(positions):
        value_end = positions[i + 1][0] if i + 1 < len(positions) else len(flat)
        fields[label] = flat[label_end:value_end].strip()

    importance = fields.get("SECTOR_IMPORTANCE", "").strip()
    evidence = [
        t.strip()
        for t in re.split(r"[,;]", fields.get("EVIDENCE_TICKERS", ""))
        if t.strip()
    ]
    return {
        "sector": fields.get("SECTOR", "").strip(),
        "state_transition": fields.get("SECTOR_STATE_TRANSITION", "").strip(),
        "importance": importance,
        "importance_n": _stars_to_count(importance),
        "evidence_tickers": evidence,
        "summary": fields.get("SUMMARY", "").strip(),
    }


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
        if cur is not None and cur_field == "commentary":
            cur["commentary"] = (cur["commentary"] + " " + s).strip()

    if cur:
        themes.append(cur)
    return themes


# ---------------------------------------------------------------------------
# Stock file
# ---------------------------------------------------------------------------


def parse_scout_tracker(text: str) -> pd.DataFrame:
    """Parse ``<<SCOUT_TRACKER_BEGIN>>`` rows into a DataFrame."""
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
    """Detect every ``<<TICKER_EARNINGS_BEGIN>>`` block and parse it."""
    blocks: list[dict[str, Any]] = []
    for m in _COMPANY_BLOCK_RE.finditer(text):
        ticker = m.group(1).strip()
        body = m.group(2)
        block = _parse_one_company_block(body, ticker_hint=ticker)
        blocks.append(block)
    return blocks


def _parse_one_company_block(body: str, *, ticker_hint: str) -> dict[str, Any]:
    """Parse the body of one company block (v2.3 with legacy fallback)."""
    lines = body.splitlines()

    section_start = _find_first_section_start(lines)
    meta_lines = lines[:section_start]
    section_lines = lines[section_start:]

    meta_kv = _parse_kv_lines("\n".join(meta_lines))

    ticker = meta_kv.get("TICKER", ticker_hint).strip()
    themes_raw = meta_kv.get("THEMES", "")
    themes = [t.strip() for t in re.split(r"[,;]", themes_raw) if t.strip()]

    sections = _split_sections(section_lines)

    # ----- Pull out the segment table (if any) ------------------------------
    seg_section_text = sections.get("SEGMENT / BUSINESS BREAKDOWN", "")
    seg_table_df, seg_commentary = _split_segment_table_and_commentary(
        seg_section_text
    )
    if seg_table_df is not None and not seg_table_df.empty:
        sections["SEGMENT / BUSINESS BREAKDOWN"] = seg_commentary

    # ----- Pull out the v2.3 markdown tables (if present) -------------------
    headline_table = parse_markdown_table(
        sections.get("HEADLINE EARNINGS TABLE", "")
    )
    sector_kpi_table = parse_markdown_table(
        sections.get("SECTOR KPI TABLE", "")
    )

    importance = meta_kv.get("IMPORTANCE", "").strip()

    # ----- v2.3 metadata with legacy fallback -------------------------------
    publication_date = (
        meta_kv.get("PUBLICATION_DATE", "").strip()
        or meta_kv.get("PUBLI_DATE", "").strip()
    )
    sector_raw = meta_kv.get("SECTOR", "").strip()
    subsector = meta_kv.get("SUBSECTOR", "").strip()
    display_sector, display_subsector = _split_legacy_sector(sector_raw, subsector)

    return {
        "ticker": ticker,
        "company": meta_kv.get("COMPANY", "").strip(),
        "sector": sector_raw,
        "subsector": subsector,
        "display_sector": display_sector,
        "display_subsector": display_subsector,
        "country": meta_kv.get("COUNTRY", "").strip(),
        "publication_date": publication_date,
        # legacy alias retained so older callers / UI bits keep working
        "publi_date": publication_date,
        "event": meta_kv.get("EVENT", "").strip(),
        "state_transition": meta_kv.get("STATE_TRANSITION", "").strip(),
        "importance": importance,
        "importance_n": _stars_to_count(importance),
        "market_reaction": meta_kv.get("MARKET_REACTION", "").strip(),
        "market_reaction_detail": meta_kv.get("MARKET_REACTION_DETAIL", "").strip(),
        "pm_read": meta_kv.get("PM_READ", "").strip(),
        "themes": themes,
        "segment_table_status": meta_kv.get("SEGMENT_TABLE_STATUS", "").strip(),
        "source_role": meta_kv.get("SOURCE_ROLE", "").strip(),
        "sections": sections,
        "segment_table": seg_table_df,
        "headline_table": headline_table,
        "sector_kpi_table": sector_kpi_table,
        "is_legacy_block": "PUBLI_DATE" in meta_kv and "PUBLICATION_DATE" not in meta_kv,
    }


def _split_legacy_sector(sector_raw: str, subsector: str) -> tuple[str, str]:
    """Derive display sector / subsector from a legacy combined SECTOR field.

    If a SUBSECTOR is already present, the v2.3 fields win and nothing is
    inferred. Otherwise, if SECTOR contains ``" / "`` (as in the legacy
    "Financials / Banking" form), the first part is treated as the sector and
    the remainder as the subsector for *display purposes only*. The raw
    SECTOR value is left untouched in the parsed dict.
    """
    if subsector:
        return sector_raw, subsector
    if " / " in sector_raw:
        head, tail = sector_raw.split(" / ", 1)
        return head.strip(), tail.strip()
    return sector_raw, ""


def _find_first_section_start(lines: list[str]) -> int:
    """Return the index of the first section header line in ``lines``."""
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
    upper-case). Legacy section names defined in
    :data:`LEGACY_SECTION_ALIASES` are normalized to their v2.3 equivalents
    so the rest of the app sees a single canonical key set.
    """
    canon = {h.upper(): h for h in COMPANY_SECTIONS}

    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        nonlocal buf, current
        if current is not None and current not in sections:
            text = "\n".join(buf).strip("\n")
            sections[current] = text.rstrip()
        buf = []

    for raw in lines:
        s = raw.strip()
        upper = s.upper()
        if upper in canon and current != canon[upper]:
            _flush()
            header = canon[upper]
            current = LEGACY_SECTION_ALIASES.get(header, header)
            continue
        if current is None:
            continue
        buf.append(raw)

    _flush()
    return sections


# ---------------------------------------------------------------------------
# Markdown / pipe tables
# ---------------------------------------------------------------------------


def parse_markdown_table(text: str) -> pd.DataFrame | None:
    """Extract a Markdown-style pipe table from arbitrary section text.

    Recognized shape::

        | Header 1 | Header 2 | Header 3 |
        |---|---:|:---:|              <- alignment row, ignored
        | cell    | cell    | cell    |
        ...

    Tolerant of:
    - leading and trailing whitespace
    - extra blank lines between table rows
    - alignment markers ``:---``, ``---:``, ``:---:``
    - non-table text appearing before / after the table

    Returns
    -------
    pandas.DataFrame  or  ``None`` if no usable table is found.
    All cells are strings.  The em dash ``—`` is preserved verbatim.
    """
    if not text:
        return None

    rows: list[list[str]] = []
    in_table = False
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln.startswith("|") or not ln.endswith("|"):
            if in_table:
                break
            continue
        in_table = True
        cells = [c.strip() for c in ln.strip("|").split("|")]
        # Skip the GitHub-style alignment row: each cell is only `-` and `:`.
        if cells and all(set(c) <= {"-", ":"} and c for c in cells):
            continue
        rows.append(cells)

    if len(rows) < 2:
        return None

    header = [h.strip() for h in rows[0]]
    width = len(header)
    data: list[list[str]] = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        if len(r) < width:
            r = r + [""] * (width - len(r))
        elif len(r) > width:
            r = r[: width - 1] + [" | ".join(r[width - 1 :])]
        data.append(r)

    if not data:
        return None
    return pd.DataFrame(data, columns=header)


# ---------------------------------------------------------------------------
# Segment table (legacy + v2.3)
# ---------------------------------------------------------------------------

_SEGMENT_HEADER_RE = re.compile(
    r"^\s*\|?\s*Segment\s*\|\s*%\s*of\s*Total\s*\|\s*Revenue\s*\|\s*"
    r"YoY\s*Growth\s*\|\s*Margin\s*Trend\s*\|\s*Key\s*Commentary\s*\|?\s*$",
    re.IGNORECASE,
)


def parse_segment_table(section_text: str) -> pd.DataFrame | None:
    """Extract the segment table from a 'SEGMENT / BUSINESS BREAKDOWN' body.

    Works on both the v2.3 markdown form and the legacy bare-pipe form.
    Returns a DataFrame, or ``None`` if no table is present.
    """
    df, _ = _split_segment_table_and_commentary(section_text)
    return df


def _split_segment_table_and_commentary(
    section_text: str,
) -> tuple[pd.DataFrame | None, str]:
    """Locate the segment table and split off the commentary text.

    Handles both:
    - v2.3 Markdown-style table (``| ... |`` rows with a ``---`` separator)
    - legacy bare pipe table (``Segment | % of Total | ...`` header, no pipes
      on the outside, no separator row)

    Returns ``(dataframe_or_None, commentary_text)``. The commentary is the
    text *after* the table (typically introduced by a 'COMMENTARY' header).
    """
    if not section_text or "|" not in section_text:
        return None, section_text or ""

    lines = section_text.splitlines()
    header_idx = _find_segment_header_idx(lines)
    if header_idx == -1:
        return None, section_text

    # Walk forward consuming all contiguous pipe-rows (and any alignment row).
    j = header_idx
    table_lines: list[str] = []
    while j < len(lines):
        ln = lines[j]
        s = ln.strip()
        if not s:
            j += 1
            break
        if "|" not in s:
            break
        table_lines.append(ln)
        j += 1

    df = parse_markdown_table("\n".join(table_lines))
    if df is None:
        df = _parse_legacy_segment_pipe_table(table_lines)

    # Commentary: everything after the table region, with an optional
    # 'COMMENTARY' header line stripped.
    tail_lines = lines[j:]
    while tail_lines and not tail_lines[0].strip():
        tail_lines.pop(0)
    if tail_lines and tail_lines[0].strip().upper() == "COMMENTARY":
        tail_lines.pop(0)
        while tail_lines and not tail_lines[0].strip():
            tail_lines.pop(0)
    commentary = "\n".join(tail_lines).rstrip()

    # Defensive: keep any preface text that appeared between the section
    # header and the SEGMENT TABLE label.
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


def _find_segment_header_idx(lines: list[str]) -> int:
    """Return the index of the segment-table header row (or -1)."""
    for i, raw in enumerate(lines):
        if _SEGMENT_HEADER_RE.match(raw):
            return i
    return -1


def _parse_legacy_segment_pipe_table(
    table_lines: list[str],
) -> pd.DataFrame | None:
    """Parse the legacy bare-pipe segment table (no leading/trailing pipes)."""
    if not table_lines:
        return None
    columns = [
        "Segment",
        "% of Total",
        "Revenue",
        "YoY Growth",
        "Margin Trend",
        "Key Commentary",
    ]
    data_rows: list[list[str]] = []
    for ln in table_lines[1:]:  # skip header row
        s = ln.strip()
        if not s or "|" not in s:
            continue
        parts = [c.strip() for c in s.strip("|").split("|")]
        if len(parts) < 6:
            continue
        if len(parts) > 6:
            parts = parts[:5] + [" | ".join(parts[5:])]
        data_rows.append(parts)
    if not data_rows:
        return None
    return pd.DataFrame(data_rows, columns=columns)


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
                "Subsector",
                "Country",
                "Publication Date",
                "Event",
                "Importance",
                "Importance N",
                "State Transition",
                "Market Reaction",
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
                "Sector": c.get("display_sector") or c["sector"],
                "Subsector": c.get("display_subsector", ""),
                "Country": c["country"],
                # Use the v2.3 canonical name everywhere possible
                "Publication Date": c.get("publication_date") or c.get("publi_date", ""),
                "Event": c["event"],
                "Importance": c["importance"],
                "Importance N": c["importance_n"],
                "State Transition": c["state_transition"],
                "Market Reaction": c.get("market_reaction", ""),
                "Themes": ", ".join(c["themes"]),
                "Themes List": c["themes"],
                "Segment Table Status": c["segment_table_status"],
                "Source Role": c["source_role"],
            }
        )
    df = pd.DataFrame(rows)
    df["Publication Date"] = pd.to_datetime(df["Publication Date"], errors="coerce")
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


def first_paragraph(text: str) -> str:
    """Return the first paragraph of a section body (split on blank line)."""
    if not text:
        return ""
    for para in text.split("\n\n"):
        p = para.strip()
        if p:
            return p
    return text.strip()
