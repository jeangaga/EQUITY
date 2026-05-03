"""Streamlit rendering for the Earnings module.

The single public entry point is :func:`render_earnings_tab`. Everything else
is internal helpers for the five sub-tabs.
"""

from __future__ import annotations

import html
from typing import Any

import pandas as pd
import streamlit as st

from . import styles
from .loader import load_recap_text, load_stock_text
from .parsers import (
    IMPORTANCE_LEVELS,
    STATE_TRANSITIONS,
    STATUS_LEVELS,
    build_company_dataframe,
    collect_unique_themes,
    parse_company_blocks,
    parse_earnings_recap,
    parse_scout_tracker,
)

# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def render_earnings_tab() -> None:
    """Render the full 'Earnings' tab. Call this from your main app."""
    styles.inject()

    # Load + parse (cached at the loader layer)
    try:
        recap_text, recap_src = load_recap_text()
        stock_text, stock_src = load_stock_text()
    except FileNotFoundError as e:
        st.error(str(e))
        return

    recap = parse_earnings_recap(recap_text)
    company_blocks = parse_company_blocks(stock_text)
    scout_df = parse_scout_tracker(stock_text)
    company_df = build_company_dataframe(company_blocks)

    _render_header(recap["meta"], recap_src, stock_src)

    sub = st.tabs(
        [
            "PM Read-Across",
            "Sector Dashboard",
            "Scout Tracker",
            "Company Notes",
            "Themes",
        ]
    )
    with sub[0]:
        _render_pm_read_across(recap)
    with sub[1]:
        _render_sector_dashboard(recap, company_blocks, company_df)
    with sub[2]:
        _render_scout_tracker(scout_df)
    with sub[3]:
        _render_company_notes(company_blocks, company_df)
    with sub[4]:
        _render_themes(recap, company_blocks)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def _render_header(meta: dict[str, str], recap_src: str, stock_src: str) -> None:
    season = meta.get("SEASON", "")
    as_of = meta.get("AS_OF", "")
    style = meta.get("STYLE", "")
    src = "GitHub" if recap_src == "github" and stock_src == "github" else "local fallback"
    st.caption(
        f"**Season:** {season}  •  **As of:** {as_of}  •  **Style:** {style}  •  **Source:** {src}"
    )


# ---------------------------------------------------------------------------
# Tab 1 — PM Read-Across
# ---------------------------------------------------------------------------


def _render_pm_read_across(recap: dict[str, Any]) -> None:
    macro: dict[str, str] = recap.get("macro", {})
    if not macro:
        st.info("No GLOBAL_MACRO_PM_READ_ACROSS block found.")
        return

    bottom = macro.get("BOTTOM LINE", "")
    if bottom:
        st.markdown(
            f'<div class="e-bottom-line"><h4>Bottom Line</h4>'
            f'<p>{html.escape(bottom)}</p></div>',
            unsafe_allow_html=True,
        )

    # Render in canonical order; fall back to insertion order for anything else.
    canonical = [
        "US GROWTH",
        "US CONSUMER",
        "FINANCIAL CONDITIONS / CREDIT",
        "AI / CAPEX / INFRASTRUCTURE",
        "EUROPE",
        "ASIA",
        "CROSS-ASSET PM TAKE",
    ]
    seen: set[str] = set()
    cols = st.columns(2)
    for i, key in enumerate(canonical + [k for k in macro if k not in canonical and k != "BOTTOM LINE"]):
        if key in seen or key not in macro or key == "BOTTOM LINE":
            continue
        seen.add(key)
        body = macro[key]
        with cols[i % 2]:
            st.markdown(
                f'<div class="e-macro"><h4>{html.escape(key.title())}</h4>'
                f'<p>{html.escape(body)}</p></div>',
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Tab 2 — Sector Dashboard
# ---------------------------------------------------------------------------


def _render_sector_dashboard(
    recap: dict[str, Any],
    company_blocks: list[dict[str, Any]],
    company_df: pd.DataFrame,
) -> None:
    sectors = recap.get("sectors", [])
    if not sectors:
        st.info("No sector recap blocks found.")
        return

    # Build a flat list of (sector, stock_dict) for filtering
    flat: list[dict[str, Any]] = []
    for sec in sectors:
        for s in sec["stocks"]:
            flat.append(s)

    if not flat:
        st.info("No stock entries inside sector recap.")
        return

    # ---- filters ---------------------------------------------------------
    f1, f2, f3, f4 = st.columns([1.4, 1.0, 1.0, 1.0])
    with f1:
        search = st.text_input(
            "Search", "", placeholder="Ticker, company, commentary…",
            key="sec_search",
        )
    with f2:
        sector_opts = sorted({s["sector_group"] for s in flat if s["sector_group"]})
        sectors_sel = st.multiselect("Sector", sector_opts, key="sec_sectors")
    with f3:
        country_opts = sorted({s["country"] for s in flat if s["country"]})
        countries_sel = st.multiselect("Country", country_opts, key="sec_countries")
    with f4:
        imp_opts = [i for i in reversed(IMPORTANCE_LEVELS)]
        imp_sel = st.multiselect("Importance", imp_opts, key="sec_importance")

    # State transition is from company file metadata; merge it in if present
    company_state: dict[str, str] = {
        c["ticker"]: c["state_transition"] for c in company_blocks
    }
    state_opts = STATE_TRANSITIONS
    state_sel = st.multiselect(
        "State transition (from full notes)",
        state_opts,
        key="sec_state",
    )

    # ---- apply filters ---------------------------------------------------
    def keep(stock: dict[str, Any]) -> bool:
        if sectors_sel and stock["sector_group"] not in sectors_sel:
            return False
        if countries_sel and stock["country"] not in countries_sel:
            return False
        if imp_sel and stock["importance"] not in imp_sel:
            return False
        if state_sel:
            st_state = company_state.get(stock["ticker"], "")
            if st_state not in state_sel:
                return False
        if search:
            blob = (
                f"{stock['ticker']} {stock['company']} {stock['sector']} "
                f"{stock['commentary']}"
            ).lower()
            if search.lower() not in blob:
                return False
        return True

    # Group by sector for rendering
    by_sector: dict[str, list[dict[str, Any]]] = {}
    for sec in sectors:
        kept = [s for s in sec["stocks"] if keep(s)]
        if kept:
            by_sector[sec["sector"]] = kept

    if not by_sector:
        st.info("No matches.")
        return

    company_lookup = {c["ticker"]: c for c in company_blocks}

    for sec_name, stocks in by_sector.items():
        st.markdown(f"### {sec_name}")
        for s in stocks:
            _render_sector_card(s, company_state.get(s["ticker"], ""), company_lookup)


def _render_sector_card(
    s: dict[str, Any],
    state: str,
    company_lookup: dict[str, dict[str, Any]],
) -> None:
    ticker = html.escape(s["ticker"])
    company = html.escape(s["company"])
    sector = html.escape(s["sector"])
    country = html.escape(s["country"])
    date = html.escape(s["publi_date"])
    importance_n = s.get("importance_n", 0)
    commentary = html.escape(s["commentary"])

    state_html = styles.state_badge_html(state) if state else ""
    stars = styles.stars_html(importance_n)

    card = f"""
<div class="e-card">
  <div class="row1">
    <span class="ticker">{ticker}</span>
    <span class="company">{company}</span>
    {stars}
    {state_html}
  </div>
  <div class="meta">{sector} • {country} • {date}</div>
  <div class="commentary">{commentary}</div>
</div>
"""
    st.markdown(card, unsafe_allow_html=True)

    # If a full note exists, show an expander linking out to it
    cb = company_lookup.get(s["ticker"])
    if cb is not None:
        with st.expander(f"Full note — {ticker}", expanded=False):
            _render_company_block(cb, in_expander=True)


# ---------------------------------------------------------------------------
# Tab 3 — Scout Tracker
# ---------------------------------------------------------------------------


def _render_scout_tracker(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("No SCOUT_TRACKER rows found.")
        return

    window = df.attrs.get("window", "")
    if window:
        st.caption(f"**Window:** {window}")

    # ---- KPIs ------------------------------------------------------------
    counts = df["Status"].value_counts().to_dict()
    imp_counts = df["Importance"].value_counts().to_dict()
    n_done = counts.get("Done", 0)
    n_brief = counts.get("Brief", 0)
    n_pending = counts.get("Pending", 0)
    n_scouted = counts.get("Scouted", 0)
    n_excluded = counts.get("Excluded", 0)
    n_oow = counts.get("Out_of_window", 0)
    n_4 = imp_counts.get("****", 0)
    n_3plus = imp_counts.get("***", 0) + n_4

    kpi_data = [
        ("Done", n_done),
        ("Brief", n_brief),
        ("Pending", n_pending + n_scouted),
        ("Out of window", n_oow),
        ("Excluded", n_excluded),
        ("**** releases", n_4),
        ("***+**** releases", n_3plus),
    ]
    cols = st.columns(len(kpi_data))
    for col, (label, value) in zip(cols, kpi_data):
        col.markdown(
            f'<div class="e-kpi"><div class="label">{html.escape(label)}</div>'
            f'<div class="value">{value}</div></div>',
            unsafe_allow_html=True,
        )

    st.write("")

    # ---- Filters ---------------------------------------------------------
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        status_sel = st.multiselect(
            "Status",
            [s for s in STATUS_LEVELS if s in df["Status"].unique()],
            key="scout_status",
        )
    with f2:
        imp_sel = st.multiselect(
            "Importance",
            [i for i in reversed(IMPORTANCE_LEVELS) if i in df["Importance"].unique()],
            key="scout_imp",
        )
    with f3:
        sector_sel = st.multiselect(
            "Sector",
            sorted(df["Sector"].unique()),
            key="scout_sector",
        )
    with f4:
        country_sel = st.multiselect(
            "Country",
            sorted(df["Country"].unique()),
            key="scout_country",
        )

    view = df.copy()
    if status_sel:
        view = view[view["Status"].isin(status_sel)]
    if imp_sel:
        view = view[view["Importance"].isin(imp_sel)]
    if sector_sel:
        view = view[view["Sector"].isin(sector_sel)]
    if country_sel:
        view = view[view["Country"].isin(country_sel)]

    # Hide the helper Importance N column
    show_cols = [c for c in view.columns if c != "Importance N"]
    st.dataframe(
        view[show_cols],
        hide_index=True,
        width="stretch",
        column_config={
            "Publi Date": st.column_config.TextColumn("Publi Date", width="small"),
            "Importance": st.column_config.TextColumn("Importance", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small"),
        },
    )


# ---------------------------------------------------------------------------
# Tab 4 — Company Notes
# ---------------------------------------------------------------------------


def _render_company_notes(
    company_blocks: list[dict[str, Any]],
    company_df: pd.DataFrame,
) -> None:
    if not company_blocks:
        st.info("No full company notes found in stock file.")
        return

    # ---- left controls (top of page on narrow viewports) -----------------
    left, right = st.columns([1, 3], gap="medium")

    with left:
        st.markdown("**Filters**")
        sectors = sorted({c["sector"] for c in company_blocks if c["sector"]})
        sector_sel = st.multiselect("Sector", sectors, key="cn_sector")

        imp_present = [i for i in reversed(IMPORTANCE_LEVELS) if any(c["importance"] == i for c in company_blocks)]
        imp_sel = st.multiselect("Importance", imp_present, key="cn_imp")

        states_present = [s for s in STATE_TRANSITIONS if any(c["state_transition"] == s for c in company_blocks)]
        state_sel = st.multiselect("State transition", states_present, key="cn_state")

        search = st.text_input("Search", "", placeholder="Free text…", key="cn_search")

        # Apply filters to derive the ticker selectbox options
        def keep(c: dict[str, Any]) -> bool:
            if sector_sel and c["sector"] not in sector_sel:
                return False
            if imp_sel and c["importance"] not in imp_sel:
                return False
            if state_sel and c["state_transition"] not in state_sel:
                return False
            if search:
                blob = (
                    f"{c['ticker']} {c['company']} {c['sector']} "
                    f"{' '.join(c['themes'])} "
                    f"{' '.join(c['sections'].values())}"
                ).lower()
                if search.lower() not in blob:
                    return False
            return True

        filtered = [c for c in company_blocks if keep(c)]
        if not filtered:
            st.warning("No matches.")
            return

        labels = [f"{c['ticker']} — {c['company']}" for c in filtered]
        idx = st.selectbox(
            "Company",
            range(len(filtered)),
            format_func=lambda i: labels[i],
            key="cn_ticker",
        )
        selected = filtered[idx]

    with right:
        _render_company_block(selected, in_expander=False)


def _render_company_block(c: dict[str, Any], *, in_expander: bool) -> None:
    """Render one full company note. Used in Company Notes + Sector expanders."""

    # ---- header ----------------------------------------------------------
    ticker = html.escape(c["ticker"])
    company = html.escape(c["company"])
    state_html = styles.state_badge_html(c["state_transition"])
    stars = styles.stars_html(c["importance_n"])
    themes_html = ""
    if c["themes"]:
        themes_html = " ".join(
            f'<span class="reltick">{html.escape(t)}</span>' for t in c["themes"]
        )
    if not in_expander:
        st.markdown(f"## {ticker} — {company}")
    meta_line = (
        f"{html.escape(c['sector'])} • {html.escape(c['country'])} • "
        f"{html.escape(c['publi_date'])} • {html.escape(c['event'])}"
    )
    st.markdown(
        f"<div class='meta'>{meta_line}</div>"
        f"<div style='margin:6px 0;'>{stars} {state_html}</div>"
        f"<div>{themes_html}</div>",
        unsafe_allow_html=True,
    )
    st.write("")

    sections = c["sections"]

    # EXEC SUMMARY — open
    _render_section(sections, "EXEC SUMMARY", expanded=True)

    # REPORTED FACTS — collapsed
    _render_section(sections, "REPORTED FACTS", expanded=False)

    # SEGMENT / BUSINESS BREAKDOWN — special: table + commentary
    _render_segment_section(c)

    for name in [
        "GUIDANCE / CAPITAL ALLOCATION",
        "PROFESSIONAL MARKET COMMENTARY",
        "COMMENT — ANALYST LAYER",
        "CONTEXT FROM PRIOR QUARTER / PRIOR DEBATE",
        "HF TAKE — STRATEGIST LAYER",
        "MARKET REACTION / PEER READ-ACROSS",
        "KEY ISSUES TO MONITOR",
    ]:
        _render_section(sections, name, expanded=False)

    # BOTTOM LINE — open + highlighted
    bottom = sections.get("BOTTOM LINE", "").strip()
    if bottom:
        st.markdown(
            f'<div class="e-bottom-line"><h4>Bottom Line</h4>'
            f'<p>{html.escape(bottom).replace(chr(10), "<br/>")}</p></div>',
            unsafe_allow_html=True,
        )


def _render_section(sections: dict[str, str], name: str, *, expanded: bool) -> None:
    body = sections.get(name, "").strip()
    if not body:
        return
    if expanded:
        st.markdown(f"**{name}**")
        st.markdown(_format_paragraphs(body))
    else:
        with st.expander(name, expanded=False):
            st.markdown(_format_paragraphs(body))


def _format_paragraphs(text: str) -> str:
    """Render plain text with paragraph + line breaks preserved."""
    # Split on blank line into paragraphs; render each as markdown.
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "\n\n".join(paras)


def _render_segment_section(c: dict[str, Any]) -> None:
    """Render the SEGMENT / BUSINESS BREAKDOWN according to status."""
    status = (c.get("segment_table_status") or "").strip()
    table = c.get("segment_table")
    commentary = c["sections"].get("SEGMENT / BUSINESS BREAKDOWN", "").strip()

    # If status is "Not meaningful", do not render the table even if present.
    show_table = status.lower() != "not meaningful" and table is not None and not table.empty

    label = "SEGMENT / BUSINESS BREAKDOWN"
    with st.expander(label, expanded=False):
        if show_table and status.lower() == "partial":
            st.markdown(
                '<span class="e-partial">Partial disclosure</span>',
                unsafe_allow_html=True,
            )
        elif status and status.lower() == "not meaningful":
            st.caption("Segment table not meaningful for this issuer.")
        if show_table:
            st.dataframe(table, hide_index=True, width="stretch")
        if commentary:
            st.markdown(_format_paragraphs(commentary))


# ---------------------------------------------------------------------------
# Tab 5 — Themes
# ---------------------------------------------------------------------------


def _render_themes(
    recap: dict[str, Any],
    company_blocks: list[dict[str, Any]],
) -> None:
    themes = recap.get("themes", [])
    company_themes = collect_unique_themes(company_blocks)

    # ---- filter on company-metadata themes ------------------------------
    if company_themes:
        sel = st.multiselect(
            "Filter companies by theme (from full notes)",
            company_themes,
            key="themes_filter",
        )
        if sel:
            matches = [
                c for c in company_blocks
                if any(t in c["themes"] for t in sel)
            ]
            st.markdown(f"**{len(matches)} companies match**")
            for c in matches:
                _render_match_chip(c)
            st.write("---")

    # ---- recap themes ----------------------------------------------------
    if not themes:
        st.info("No KEY_CROSS_THEMES block found.")
        return

    # Build a quick map: theme keyword -> list of tickers it relates to.
    # Heuristic: a theme is related to a company if any token of the theme
    # appears in the company's themes metadata (case-insensitive substring).
    def related_tickers(theme: dict[str, str]) -> list[str]:
        title = theme.get("theme", "").lower()
        out: list[str] = []
        for c in company_blocks:
            for t in c["themes"]:
                tl = t.lower()
                if tl in title or title in tl:
                    out.append(c["ticker"])
                    break
                # token-overlap fallback
                if any(
                    len(tok) > 3 and tok in title
                    for tok in tl.replace("/", " ").split()
                ):
                    out.append(c["ticker"])
                    break
        return sorted(set(out))

    cols = st.columns(2)
    for i, t in enumerate(themes):
        related = related_tickers(t)
        rel_html = ""
        if related:
            chips = " ".join(
                f'<span class="reltick">{html.escape(tk)}</span>' for tk in related
            )
            rel_html = f'<div class="relrow">Related (from notes): {chips}</div>'
        with cols[i % 2]:
            st.markdown(
                f'<div class="e-theme">'
                f'<h4>{html.escape(t["theme"])}</h4>'
                f'<p style="margin:0;font-size:0.9rem;line-height:1.5;">'
                f'{html.escape(t["commentary"])}</p>'
                f'{rel_html}'
                f'</div>',
                unsafe_allow_html=True,
            )


def _render_match_chip(c: dict[str, Any]) -> None:
    state_html = styles.state_badge_html(c["state_transition"])
    stars = styles.stars_html(c["importance_n"])
    st.markdown(
        f'<div class="e-card"><div class="row1">'
        f'<span class="ticker">{html.escape(c["ticker"])}</span>'
        f'<span class="company">{html.escape(c["company"])}</span>'
        f'{stars}{state_html}'
        f'</div><div class="meta">{html.escape(c["sector"])} • '
        f'{html.escape(c["country"])} • {html.escape(c["publi_date"])} • '
        f'Themes: {html.escape(", ".join(c["themes"]))}</div></div>',
        unsafe_allow_html=True,
    )
