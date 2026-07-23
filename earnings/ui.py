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
from .loader import (
    AVAILABLE_QUARTERS,
    DEFAULT_QUARTER,
    load_recap_text,
    load_stock_text,
)
from .parsers import (
    IMPORTANCE_LEVELS,
    STATE_TRANSITIONS,
    build_company_dataframe,
    parse_company_blocks,
    parse_earnings_recap,
)

# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def render_earnings_tab() -> None:
    """Render the full 'Earnings' tab. Call this from your main app."""
    styles.inject()

    # ---- quarter toggle (drives every section below) ---------------------
    # A single season selector, defaulting to the latest quarter. All five
    # sections read from the recap + by-stock files of the selected quarter,
    # so the toggle is effectively global for this screen.
    _qcol, _sp, _bcol = st.columns([1.3, 3.7, 1.2])
    with _qcol:
        quarter = st.selectbox(
            "Quarter",
            AVAILABLE_QUARTERS,
            index=AVAILABLE_QUARTERS.index(DEFAULT_QUARTER),
            key="earnings_quarter",
            label_visibility="collapsed",
            help="Earnings season shown in every section",
        )
    with _bcol:
        # Reload button — clears the 30-min loader cache and re-fetches the
        # latest files from GitHub on the next run.
        if st.button(
            "🔄 Reload data",
            help="Clear the cache and re-fetch the latest files from GitHub",
            width="stretch",
        ):
            st.cache_data.clear()
            st.rerun()

    # When the quarter changes, reset all section-scoped widget state (filters,
    # selections, search boxes). Options differ between seasons — a stale
    # multiselect value that no longer exists in the new quarter's options
    # would make Streamlit raise — and a clean slate is the sane UX anyway.
    #
    # IMPORTANT: reset by *assignment*, never ``del``. Deleting the key of a
    # widget that was instantiated on the previous run desyncs Streamlit: the
    # frontend keeps showing the old value (e.g. a "Financials" chip in the
    # Sector multiselect) while the server-side value silently resets to
    # empty, so the filter looks active but matches nothing. Assigning an
    # empty value propagates to the frontend and actually clears the widget.
    if st.session_state.get("_earnings_active_quarter") != quarter:
        for _k in list(st.session_state.keys()):
            if _k.startswith(("cn_", "sec_", "scout_", "themes_")):
                _v = st.session_state[_k]
                if isinstance(_v, list):
                    st.session_state[_k] = []          # multiselects
                elif isinstance(_v, str):
                    st.session_state[_k] = ""          # search boxes
                elif isinstance(_v, bool):             # toggles (check before
                    st.session_state[_k] = False       # int: bool is an int!)
                elif _k == "themes_min_n":
                    st.session_state[_k] = 2           # slider min_value
                elif isinstance(_v, int):
                    st.session_state[_k] = 0           # index-based widgets
        st.session_state["_earnings_active_quarter"] = quarter

    # Load + parse (cached at the loader layer)
    try:
        recap_text, recap_src = load_recap_text(quarter)
        stock_text, stock_src = load_stock_text(quarter)
    except FileNotFoundError as e:
        st.error(str(e))
        return

    recap = parse_earnings_recap(recap_text)
    company_blocks = parse_company_blocks(stock_text)
    company_df = build_company_dataframe(company_blocks)

    _render_header(recap["meta"], recap_src, stock_src)

    # Streamlit discards a widget's state if that widget isn't rendered on a
    # given run. Because only one section below renders at a time, switching
    # sections would otherwise wipe the filters / company selection of the
    # others. Re-assigning each section-scoped key to itself on every run
    # keeps that state alive, so returning to a section restores exactly what
    # the user last had on screen (e.g. the open company note).
    for _k in list(st.session_state.keys()):
        if _k.startswith(("cn_", "sec_", "scout_", "themes_")):
            st.session_state[_k] = st.session_state[_k]

    # NOTE: we deliberately do *not* use ``st.tabs`` here. ``st.tabs`` does not
    # persist the active tab across reruns -- so when a widget *inside* a
    # sub-section triggers a rerun (e.g. typing a company in the Company Notes
    # search box and pressing Enter), Streamlit snaps the view back to the
    # first tab ("PM Read-Across"). A ``st.radio`` keyed into session_state
    # remembers the open section, so the user stays where they were.
    sections = [
        "PM Read-Across",
        "Sector Dashboard",
        "Scout Tracker",
        "Company Notes",
        "Themes",
    ]
    section = st.radio(
        "Section",
        sections,
        horizontal=True,
        key="earnings_section",
        label_visibility="collapsed",
    )

    if section == "PM Read-Across":
        _render_pm_read_across(recap)
    elif section == "Sector Dashboard":
        _render_sector_dashboard(recap, company_blocks, company_df)
    elif section == "Scout Tracker":
        _render_scout_tracker(company_blocks, company_df)
    elif section == "Company Notes":
        _render_company_notes(company_blocks, company_df)
    elif section == "Themes":
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
        "INDEX-LEVEL EARNINGS PICTURE",
        "US GROWTH",
        "US CONSUMER",
        "FINANCIAL CONDITIONS / CREDIT",
        "AI / CAPEX / INFRASTRUCTURE",
        "EUROPE",
        "ASIA",
        "CROSS-ASSET PM TAKE",
    ]
    seen: set[str] = set()
    # Single-column stack (full width). Easier to scan a long-form macro
    # paragraph and avoids ragged column heights when paragraph lengths differ.
    for key in canonical + [k for k in macro if k not in canonical and k != "BOTTOM LINE"]:
        if key in seen or key not in macro or key == "BOTTOM LINE":
            continue
        seen.add(key)
        body = macro[key]
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
    """Sector Dashboard — sourced directly from the full company blocks.

    This used to read the recap file's ``<<SECTOR_RECAP>>`` list, which had to
    be hand-maintained and drifted out of sync with the actual notes. It now
    derives entirely from the ``<<TICKER_EARNINGS_BEGIN>>`` blocks -- the same
    source as Company Notes -- so the two tabs can never disagree.
    """
    if not company_blocks:
        st.info("No company notes found in the stock file.")
        return

    def _sector_of(c: dict[str, Any]) -> str:
        return c.get("display_sector") or c.get("sector", "") or "Uncategorized"

    # ---- filters ---------------------------------------------------------
    f1, f2, f3, f4 = st.columns([1.4, 1.0, 1.0, 1.0])
    with f1:
        search = st.text_input(
            "Search", "", placeholder="Ticker, company, PM read…",
            key="sec_search",
        )
    with f2:
        sector_opts = sorted({_sector_of(c) for c in company_blocks})
        sectors_sel = st.multiselect("Sector", sector_opts, key="sec_sectors")
    with f3:
        country_opts = sorted({c["country"] for c in company_blocks if c.get("country")})
        countries_sel = st.multiselect("Country", country_opts, key="sec_countries")
    with f4:
        imp_opts = [i for i in reversed(IMPORTANCE_LEVELS)]
        imp_sel = st.multiselect("Importance", imp_opts, key="sec_importance")

    states_present = [
        s for s in STATE_TRANSITIONS
        if any(c.get("state_transition") == s for c in company_blocks)
    ]
    state_sel = st.multiselect("State transition", states_present, key="sec_state")

    # ---- apply filters ---------------------------------------------------
    def keep(c: dict[str, Any]) -> bool:
        if sectors_sel and _sector_of(c) not in sectors_sel:
            return False
        if countries_sel and c.get("country", "") not in countries_sel:
            return False
        if imp_sel and c.get("importance", "") not in imp_sel:
            return False
        if state_sel and c.get("state_transition", "") not in state_sel:
            return False
        if search:
            blob = (
                f"{c['ticker']} {c['company']} {c.get('display_sector', '')} "
                f"{c.get('display_subsector', '')} {c.get('sector', '')} "
                f"{c.get('pm_read', '')} {' '.join(c.get('themes', []))}"
            ).lower()
            if search.lower() not in blob:
                return False
        return True

    kept = [c for c in company_blocks if keep(c)]
    if not kept:
        st.info("No matches.")
        return

    # ---- group by broad sector ------------------------------------------
    by_sector: dict[str, list[dict[str, Any]]] = {}
    for c in kept:
        by_sector.setdefault(_sector_of(c), []).append(c)

    # Sector-level recap headers from the recap file's <<SECTOR_BEGIN>> blocks,
    # keyed by a normalised sector name so minor casing/spacing differences
    # between the recap file and the company blocks still line up.
    recap_by_sector: dict[str, dict[str, Any]] = {
        (rec.get("sector", "") or "").strip().casefold(): rec
        for rec in recap.get("sectors", [])
        if rec.get("sector")
    }

    st.caption(f"Showing {len(kept)} of {len(company_blocks)} releases")

    for sec_name in sorted(by_sector):
        stocks = by_sector[sec_name]
        st.markdown(f"### {html.escape(sec_name)}  ·  {len(stocks)}")
        sector_recap = recap_by_sector.get(sec_name.strip().casefold())
        if sector_recap:
            _render_sector_recap_header(sector_recap)
        for c in stocks:
            _render_sector_card(c)


def _render_sector_card(c: dict[str, Any]) -> None:
    """One company card on the Sector Dashboard, built from a full company block."""
    ticker = html.escape(c["ticker"])
    company = html.escape(c["company"])
    sector = c.get("display_sector") or c.get("sector", "")
    subsector = c.get("display_subsector", "")
    sector_text = f"{sector} / {subsector}" if subsector else sector
    sector_html = html.escape(sector_text)
    country = html.escape(c.get("country", ""))
    date = html.escape(c.get("publication_date") or c.get("publi_date", ""))
    event = html.escape(c.get("event", ""))
    importance_n = c.get("importance_n", 0)
    pm_read = html.escape(c.get("pm_read", ""))

    state_html = styles.state_badge_html(c.get("state_transition", ""))
    market_html = styles.market_reaction_badge_html(c.get("market_reaction", ""))
    stars = styles.stars_html(importance_n)

    meta_bits = " • ".join(p for p in [sector_html, country, date, event] if p)

    card = f"""
<div class="e-card">
  <div class="row1">
    <span class="ticker">{ticker}</span>
    <span class="company">{company}</span>
    {stars}
    {state_html}
    {market_html}
  </div>
  <div class="meta">{meta_bits}</div>
  <div class="commentary">{pm_read}</div>
</div>
"""
    st.markdown(card, unsafe_allow_html=True)

    # Every card links straight to its full note — same data, expanded.
    with st.expander(f"Full note — {ticker}", expanded=False):
        _render_company_block(c, in_expander=True)


def _render_sector_recap_header(rec: dict[str, Any]) -> None:
    """Render the sector-level recap above that sector's stock cards.

    Sourced from the recap file's ``<<SECTOR_BEGIN>>`` blocks: the sector
    state transition, importance, evidence tickers and the summary paragraph.
    """
    state_html = styles.state_badge_html(rec.get("state_transition", ""))
    stars = styles.stars_html(rec.get("importance_n", 0))
    summary = html.escape(rec.get("summary", ""))
    tickers = rec.get("evidence_tickers", [])

    badge_line = ""
    if stars or state_html:
        badge_line = f'<div style="margin-bottom:6px;">{stars} {state_html}</div>'
    body = f"<p>{summary}</p>" if summary else ""
    ev_line = ""
    if tickers:
        ev_line = (
            '<div style="margin-top:6px;font-size:0.78rem;color:#a1a1aa;">'
            f'Evidence: {html.escape(" · ".join(tickers))}</div>'
        )
    st.markdown(
        f'<div class="e-macro">{badge_line}{body}{ev_line}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Tab 3 — Scout Tracker
# ---------------------------------------------------------------------------


def _render_scout_tracker(
    company_blocks: list[dict[str, Any]],
    company_df: pd.DataFrame,
) -> None:
    """Coverage tracker — auto-generated from the full company blocks.

    This used to read a hand-maintained ``<<SCOUT_TRACKER>>`` block. It now
    derives from the ``<<TICKER_EARNINGS_BEGIN>>`` blocks, so every release
    that has a full note shows up here automatically and the list can never
    drift from Company Notes. (The old workflow Status column is gone -- by
    definition every row here is an already-written-up release.)
    """
    if company_df.empty:
        st.info("No company notes found in the stock file.")
        return

    # ---- KPIs ------------------------------------------------------------
    imp_counts = company_df["Importance"].value_counts().to_dict()
    state_counts = company_df["State Transition"].value_counts().to_dict()
    n_4 = imp_counts.get("****", 0)
    n_3plus = imp_counts.get("***", 0) + n_4

    kpi_data = [
        ("Releases covered", len(company_df)),
        ("**** releases", n_4),
        ("***+**** releases", n_3plus),
        ("Improvement", state_counts.get("Improvement", 0)),
        ("Deterioration", state_counts.get("Deterioration", 0)),
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
        imp_sel = st.multiselect(
            "Importance",
            [i for i in reversed(IMPORTANCE_LEVELS) if i in company_df["Importance"].unique()],
            key="scout_imp",
        )
    with f2:
        state_sel = st.multiselect(
            "State transition",
            [s for s in STATE_TRANSITIONS if s in company_df["State Transition"].unique()],
            key="scout_state",
        )
    with f3:
        sector_sel = st.multiselect(
            "Sector",
            sorted(x for x in company_df["Sector"].unique() if x),
            key="scout_sector",
        )
    with f4:
        country_sel = st.multiselect(
            "Country",
            sorted(x for x in company_df["Country"].unique() if x),
            key="scout_country",
        )

    view = company_df.copy()
    if imp_sel:
        view = view[view["Importance"].isin(imp_sel)]
    if state_sel:
        view = view[view["State Transition"].isin(state_sel)]
    if sector_sel:
        view = view[view["Sector"].isin(sector_sel)]
    if country_sel:
        view = view[view["Country"].isin(country_sel)]

    st.caption(
        f"Showing {len(view)} of {len(company_df)} releases — "
        f"select one or more rows to open the full notes below "
        f"(and download them as .txt)"
    )

    # Hide helper / verbose columns; keep the scan-friendly ones.
    hidden = {"Importance N", "Themes List", "Source Role", "Segment Table Status"}
    show_cols = [c for c in view.columns if c not in hidden]
    event = st.dataframe(
        view[show_cols],
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="multi-row",
        # NB: key is intentionally NOT prefixed cn_/sec_/scout_/themes_ so the
        # session_state keep-alive bounce skips it — a dataframe's selection
        # state can't be re-assigned via the API and would raise if bounced.
        key="tracker_table",
        column_config={
            "Publication Date": st.column_config.DateColumn(
                "Publication Date", width="small", format="YYYY-MM-DD"
            ),
            "Importance": st.column_config.TextColumn("Importance", width="small"),
            "State Transition": st.column_config.TextColumn(
                "State Transition", width="small"
            ),
        },
    )

    # ---- selected rows -> full earnings notes + .txt download ------------
    selected_rows = event.selection.rows
    if selected_rows:
        tickers = [view.iloc[i]["Ticker"] for i in selected_rows]
        sel_blocks = [
            b
            for t in tickers
            for b in (next((c for c in company_blocks if c["ticker"] == t), None),)
            if b is not None
        ]
        missing = [t for t in tickers if all(b["ticker"] != t for b in sel_blocks)]

        if sel_blocks:
            quarter = st.session_state.get("earnings_quarter", "")
            txt = _notes_to_txt(sel_blocks, quarter=quarter)
            tag = quarter.replace(" ", "_") if quarter else "EARNINGS"
            if len(sel_blocks) <= 6:
                name_part = "_".join(b["ticker"] for b in sel_blocks)
            else:
                name_part = f"{len(sel_blocks)}_stocks"
            st.download_button(
                f"⬇️ Download {len(sel_blocks)} note(s) as .txt",
                data=txt,
                file_name=f"EARNINGS_NOTES_{tag}_{name_part}.txt",
                mime="text/plain",
                key="tracker_download",
            )
        for t in missing:
            st.info(f"No full note found for {t}.")
        for block in sel_blocks:
            st.markdown("---")
            _render_company_block(block, in_expander=False)

        # ---- previous-quarter notes for the selected stocks --------------
        # Comes AFTER the current-quarter notes above: lets the PM pull up
        # what was written on the same names in earlier earnings seasons.
        if sel_blocks:
            _render_scout_previous_quarters(
                [b["ticker"] for b in sel_blocks]
            )


def _render_scout_previous_quarters(tickers: list[str]) -> None:
    """Bottom-of-page block: show the selected stocks' notes from an earlier
    earnings season, with its own .txt download.

    The quarter toggle lists every season older than the currently active one
    (``AVAILABLE_QUARTERS`` is newest-first). Nothing is fetched or rendered
    until the user flips the show toggle on.
    """
    current_q = st.session_state.get("earnings_quarter", DEFAULT_QUARTER)
    try:
        prev_quarters = AVAILABLE_QUARTERS[AVAILABLE_QUARTERS.index(current_q) + 1:]
    except ValueError:
        prev_quarters = []

    st.markdown("---")
    st.markdown("#### Previous earnings quarters")
    if not prev_quarters:
        st.caption(f"No quarter older than {current_q} is available.")
        return

    # Index-based radio so the session value is always a valid int (a label
    # string could go stale when the available list changes with the quarter).
    if st.session_state.get("scout_prev_q", 0) not in range(len(prev_quarters)):
        st.session_state["scout_prev_q"] = 0
    prev_idx = st.radio(
        "Previous quarter",
        range(len(prev_quarters)),
        format_func=lambda i: prev_quarters[i],
        horizontal=True,
        key="scout_prev_q",
        label_visibility="collapsed",
    )
    prev_q = prev_quarters[prev_idx]

    show = st.toggle(
        f"Show {prev_q} earnings comments for: {', '.join(tickers)}",
        key="scout_prev_show",
    )
    if not show:
        return

    try:
        prev_text, _src = load_stock_text(prev_q)
    except FileNotFoundError as e:
        st.error(str(e))
        return

    by_ticker = {b["ticker"]: b for b in parse_company_blocks(prev_text)}
    found = [by_ticker[t] for t in tickers if t in by_ticker]
    not_covered = [t for t in tickers if t not in by_ticker]

    if not_covered:
        st.info(f"No {prev_q} note for: {', '.join(not_covered)}")
    if not found:
        return

    txt = _notes_to_txt(found, quarter=prev_q)
    tag = prev_q.replace(" ", "_")
    if len(found) <= 6:
        name_part = "_".join(b["ticker"] for b in found)
    else:
        name_part = f"{len(found)}_stocks"
    st.download_button(
        f"⬇️ Download {len(found)} {prev_q} note(s) as .txt",
        data=txt,
        file_name=f"EARNINGS_NOTES_{tag}_{name_part}.txt",
        mime="text/plain",
        # NB: deliberately NOT scout_-prefixed — button session values cannot
        # be (re-)assigned via st.session_state, so the keep-alive bounce and
        # the quarter-change reset must skip this key (same as tracker_table).
        key="tracker_prev_download",
    )
    for b in found:
        st.markdown("---")
        _render_company_block(b, in_expander=False)


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
        # Prefer the display_sector (v2.3 SECTOR) so legacy + v2.3 blocks group
        # under the same top-level sector value.
        sectors = sorted({
            (c.get("display_sector") or c.get("sector", ""))
            for c in company_blocks
            if c.get("display_sector") or c.get("sector")
        })
        sector_sel = st.multiselect("Sector", sectors, key="cn_sector")

        imp_present = [i for i in reversed(IMPORTANCE_LEVELS) if any(c["importance"] == i for c in company_blocks)]
        imp_sel = st.multiselect("Importance", imp_present, key="cn_imp")

        states_present = [s for s in STATE_TRANSITIONS if any(c["state_transition"] == s for c in company_blocks)]
        state_sel = st.multiselect("State transition", states_present, key="cn_state")

        search = st.text_input("Search", "", placeholder="Free text…", key="cn_search")

        # Apply filters to derive the ticker selectbox options
        def keep(c: dict[str, Any]) -> bool:
            sector_for_filter = c.get("display_sector") or c.get("sector", "")
            if sector_sel and sector_for_filter not in sector_sel:
                return False
            if imp_sel and c["importance"] not in imp_sel:
                return False
            if state_sel and c["state_transition"] not in state_sel:
                return False
            if search:
                blob = (
                    f"{c['ticker']} {c['company']} {c.get('sector', '')} "
                    f"{c.get('subsector', '')} {c.get('pm_read', '')} "
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
        # The cn_ticker selection is kept alive across section switches, but a
        # previous (longer) filter set could leave it pointing past the end of
        # a now-shorter list. Clamp it before the widget renders.
        if st.session_state.get("cn_ticker", 0) not in range(len(filtered)):
            st.session_state["cn_ticker"] = 0
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
    """Render one full company note. v2.3 layout with legacy fallback.

    Default ("PM compact") view, top to bottom:

    1. Header card  (TICKER, company, importance ★, state badge,
                     market-reaction pill, sector / subsector · country ·
                     publication_date · event)
    2. PM_READ      (compact highlighted line, when present)
    3. EXEC SUMMARY (always open)
    4. HEADLINE EARNINGS TABLE (always visible if present)
    5. SECTOR KPI TABLE        (always visible if present)
    6. SEGMENT TABLE           (always visible if present, with
                                'Partial disclosure' badge when relevant)
       + segment commentary
    7. HF TAKE — STRATEGIST LAYER preview (first paragraph) + expander
    8. Other sections in collapsed expanders, in canonical order
    9. BOTTOM LINE              (highlighted, at the bottom)
    """

    # ---- header ----------------------------------------------------------
    ticker = html.escape(c["ticker"])
    company = html.escape(c["company"])
    state_html = styles.state_badge_html(c.get("state_transition", ""))
    stars = styles.stars_html(c.get("importance_n", 0))
    market_html = styles.market_reaction_badge_html(c.get("market_reaction", ""))

    # Prefer v2.3 display fields; fall back to legacy combined sector if needed.
    display_sector = c.get("display_sector") or c.get("sector", "")
    display_subsector = c.get("display_subsector", "")
    sector_text = display_sector
    if display_subsector:
        sector_text = f"{display_sector} / {display_subsector}"
    pub_date = c.get("publication_date") or c.get("publi_date", "")
    event = c.get("event", "")

    themes_html = ""
    if c.get("themes"):
        themes_html = " ".join(
            f'<span class="reltick">{html.escape(t)}</span>' for t in c["themes"]
        )
    if not in_expander:
        st.markdown(f"## {ticker} — {company}")

    meta_parts = [sector_text, c.get("country", ""), pub_date, event]
    meta_line = " • ".join(html.escape(p) for p in meta_parts if p)

    st.markdown(
        f"<div class='meta'>{meta_line}</div>"
        f"<div style='margin:6px 0;'>{stars} {state_html} {market_html}</div>"
        f"<div>{themes_html}</div>",
        unsafe_allow_html=True,
    )

    # Optional verbose market reaction detail under the badges
    mrd = c.get("market_reaction_detail", "")
    if mrd:
        st.caption(f"Market reaction: {mrd}")

    # ---- PM_READ (compact highlighted line) ------------------------------
    pm_read = c.get("pm_read", "")
    if pm_read:
        st.markdown(
            f'<div class="e-pmread"><span class="lbl">PM read</span>'
            f'{html.escape(pm_read)}</div>',
            unsafe_allow_html=True,
        )

    sections = c["sections"]

    # ---- EXEC SUMMARY (always open) --------------------------------------
    _render_open_section(sections, "EXEC SUMMARY")

    # ---- HEADLINE EARNINGS TABLE (compact, visible) ----------------------
    _render_compact_table(
        title="Headline earnings",
        df=c.get("headline_table"),
        fallback_text=sections.get("HEADLINE EARNINGS TABLE", ""),
    )

    # ---- SECTOR KPI TABLE (compact, visible) -----------------------------
    _render_compact_table(
        title="Sector KPIs",
        df=c.get("sector_kpi_table"),
        fallback_text=sections.get("SECTOR KPI TABLE", ""),
    )

    # ---- SEGMENT TABLE (compact, visible) --------------------------------
    _render_segment_compact(c)

    # ---- HF TAKE preview (first paragraph) + full expander ----------------
    _render_hf_take_preview(sections.get("HF TAKE — STRATEGIST LAYER", ""))

    # ---- Remaining sections as expanders, canonical order ----------------
    expander_order = [
        "OFFICIAL EARNINGS DETAIL",
        "GUIDANCE / CAPITAL ALLOCATION",
        "PROFESSIONAL MARKET COMMENTARY",
        "COMMENT — ANALYST LAYER",
        "CONTEXT FROM PRIOR QUARTER / PRIOR DEBATE",
        "MARKET REACTION / PEER READ-ACROSS",
        "KEY ISSUES TO MONITOR",
    ]
    for name in expander_order:
        _render_section(sections, name, expanded=False)

    # ---- BOTTOM LINE (highlighted) ---------------------------------------
    bottom = sections.get("BOTTOM LINE", "").strip()
    if bottom:
        st.markdown(
            f'<div class="e-bottom-line"><h4>Bottom Line</h4>'
            f'<p>{html.escape(bottom).replace(chr(10), "<br/>")}</p></div>',
            unsafe_allow_html=True,
        )


def _render_open_section(sections: dict[str, str], name: str) -> None:
    body = sections.get(name, "").strip()
    if not body:
        return
    st.markdown(f"**{name}**")
    st.markdown(_format_paragraphs(body))


def _render_compact_table(
    *, title: str, df, fallback_text: str
) -> None:
    """Render a table inline in the compact view.

    If the table parsed cleanly to a DataFrame, render with ``st.dataframe``.
    If parsing failed but raw text exists, fall back to ``st.markdown`` so the
    user still sees the content.
    """
    if df is not None and not df.empty:
        st.markdown(f"**{title}**")
        st.dataframe(df, hide_index=True, width="stretch")
        return
    raw = (fallback_text or "").strip()
    if raw and "|" in raw:
        st.markdown(f"**{title}**")
        # Escape $ so a dollar figure in a raw table cell isn't read as math.
        st.markdown(raw.replace("$", r"\$"))


def _render_segment_compact(c: dict[str, Any]) -> None:
    """Compact-view segment table + commentary (handles status flags)."""
    status = (c.get("segment_table_status") or "").strip()
    table = c.get("segment_table")
    commentary = c["sections"].get("SEGMENT / BUSINESS BREAKDOWN", "").strip()
    show_table = (
        status.lower() != "not meaningful"
        and table is not None
        and not table.empty
    )
    if not (show_table or commentary or status):
        return

    title_html = "Segment / business breakdown"
    if show_table and status.lower() == "partial":
        title_html += ' <span class="e-partial">Partial disclosure</span>'
    st.markdown(f"**{title_html}**", unsafe_allow_html=True)

    if status and status.lower() == "not meaningful":
        st.caption("Segment table not meaningful for this issuer.")

    if show_table:
        st.dataframe(table, hide_index=True, width="stretch")

    if commentary:
        st.markdown(_format_paragraphs(commentary))


def _render_hf_take_preview(text: str) -> None:
    """Show the first paragraph as a highlighted teaser, full text in expander."""
    text = (text or "").strip()
    if not text:
        return
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    first = paras[0] if paras else ""
    rest = "\n\n".join(paras[1:]) if len(paras) > 1 else ""

    if first:
        st.markdown(
            f'<div class="e-hf-preview"><span class="lbl">HF take</span>'
            f'{html.escape(first)}</div>',
            unsafe_allow_html=True,
        )
    if rest:
        with st.expander("HF TAKE — STRATEGIST LAYER (continued)", expanded=True):
            st.markdown(_format_paragraphs(rest))


def _render_section(sections: dict[str, str], name: str, *, expanded: bool) -> None:
    body = sections.get(name, "").strip()
    if not body:
        return
    if expanded:
        st.markdown(f"**{name}**")
        st.markdown(_format_paragraphs(body))
    else:
        # Expander stays (still collapsible) but opens by default so the whole
        # note is readable top-to-bottom without clicking each subsection.
        with st.expander(name, expanded=True):
            st.markdown(_format_paragraphs(body))


def _format_paragraphs(text: str) -> str:
    """Render plain text with paragraph + line breaks preserved.

    Also escapes ``$`` so Streamlit doesn't treat dollar figures in the notes
    (e.g. ``$2.08B``) as LaTeX math delimiters and mangle whole paragraphs
    into run-together italics.
    """
    # Split on blank line into paragraphs; render each as markdown.
    text = text.replace("$", r"\$")
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "\n\n".join(paras)


# ---------------------------------------------------------------------------
# Plain-text export (Scout Tracker download)
# ---------------------------------------------------------------------------


def _df_to_txt(df) -> str:
    """Render a parsed table DataFrame as aligned plain text."""
    if df is None or df.empty:
        return ""
    return df.to_string(index=False)


def _company_block_to_txt(c: dict[str, Any]) -> str:
    """Serialize one company note to plain text, mirroring the on-screen
    layout of :func:`_render_company_block` (same content, same order)."""
    lines: list[str] = []
    rule = "=" * 78
    lines.append(rule)
    lines.append(f"{c['ticker']} — {c['company']}")
    lines.append(rule)

    display_sector = c.get("display_sector") or c.get("sector", "")
    display_subsector = c.get("display_subsector", "")
    sector_text = display_sector
    if display_subsector:
        sector_text = f"{display_sector} / {display_subsector}"
    meta_rows = [
        ("Sector", sector_text),
        ("Country", c.get("country", "")),
        ("Publication date", c.get("publication_date") or c.get("publi_date", "")),
        ("Event", c.get("event", "")),
        ("Importance", c.get("importance", "")),
        ("State transition", c.get("state_transition", "")),
        ("Market reaction", c.get("market_reaction", "")),
        ("Market reaction detail", c.get("market_reaction_detail", "")),
        ("Themes", ", ".join(c.get("themes", []))),
    ]
    for label, value in meta_rows:
        if value:
            lines.append(f"{label}: {value}")

    pm_read = c.get("pm_read", "")
    if pm_read:
        lines.append("")
        lines.append(f"PM READ: {pm_read}")

    sections = c["sections"]

    def add_section(title: str, body: str) -> None:
        body = (body or "").strip()
        if not body:
            return
        lines.append("")
        lines.append(f"--- {title} ---")
        lines.append(body)

    add_section("EXEC SUMMARY", sections.get("EXEC SUMMARY", ""))

    # Tables: prefer the parsed DataFrame (clean alignment), fall back to the
    # raw section text so nothing displayed on screen is missing from the txt.
    headline_txt = _df_to_txt(c.get("headline_table")) or sections.get(
        "HEADLINE EARNINGS TABLE", ""
    )
    add_section("HEADLINE EARNINGS TABLE", headline_txt)

    kpi_txt = _df_to_txt(c.get("sector_kpi_table")) or sections.get(
        "SECTOR KPI TABLE", ""
    )
    add_section("SECTOR KPI TABLE", kpi_txt)

    seg_status = (c.get("segment_table_status") or "").strip()
    seg_parts: list[str] = []
    if seg_status:
        seg_parts.append(f"[Status: {seg_status}]")
    if seg_status.lower() != "not meaningful":
        seg_txt = _df_to_txt(c.get("segment_table"))
        if seg_txt:
            seg_parts.append(seg_txt)
    seg_commentary = sections.get("SEGMENT / BUSINESS BREAKDOWN", "").strip()
    if seg_commentary:
        seg_parts.append(seg_commentary)
    add_section("SEGMENT / BUSINESS BREAKDOWN", "\n\n".join(seg_parts))

    add_section(
        "HF TAKE — STRATEGIST LAYER", sections.get("HF TAKE — STRATEGIST LAYER", "")
    )

    # Remaining sections, same canonical order as the on-screen expanders.
    expander_order = [
        "OFFICIAL EARNINGS DETAIL",
        "GUIDANCE / CAPITAL ALLOCATION",
        "PROFESSIONAL MARKET COMMENTARY",
        "COMMENT — ANALYST LAYER",
        "CONTEXT FROM PRIOR QUARTER / PRIOR DEBATE",
        "MARKET REACTION / PEER READ-ACROSS",
        "KEY ISSUES TO MONITOR",
    ]
    for name in expander_order:
        add_section(name, sections.get(name, ""))

    add_section("BOTTOM LINE", sections.get("BOTTOM LINE", ""))

    return "\n".join(lines)


def _notes_to_txt(blocks: list[dict[str, Any]], *, quarter: str = "") -> str:
    """Serialize several company notes into one downloadable .txt document."""
    header = "EARNINGS NOTES EXPORT"
    if quarter:
        header += f" — {quarter}"
    header += f" — {len(blocks)} stock(s): " + ", ".join(b["ticker"] for b in blocks)
    body = "\n\n\n".join(_company_block_to_txt(b) for b in blocks)
    return f"{header}\n\n{body}\n"


# ---------------------------------------------------------------------------
# Tab 5 — Themes
# ---------------------------------------------------------------------------


def _render_themes(
    recap: dict[str, Any],
    company_blocks: list[dict[str, Any]],
) -> None:
    themes = recap.get("themes", [])

    # ---- filter companies by theme tag ----------------------------------
    # The per-company THEMES tags have no controlled vocabulary, so the raw
    # list is mostly one-off tags. Count how many companies share each tag,
    # drop the long tail, and order by frequency so the filter is actually
    # useful for *selecting stocks*.
    theme_counts: dict[str, int] = {}
    for c in company_blocks:
        for t in set(c.get("themes", [])):
            theme_counts[t] = theme_counts.get(t, 0) + 1

    if theme_counts:
        max_count = max(theme_counts.values())
        if max_count >= 2:
            min_n = st.slider(
                "Minimum companies sharing a theme",
                min_value=2,
                max_value=max_count,
                value=2,
                key="themes_min_n",
            )
        else:
            min_n = 1
        shared = sorted(
            (t for t, n in theme_counts.items() if n >= min_n),
            key=lambda t: (-theme_counts[t], t.casefold()),
        )
        # Drop any previously-selected tag the current threshold filtered out,
        # otherwise the multiselect errors on a stale session_state value.
        prev = st.session_state.get("themes_filter")
        if prev:
            st.session_state["themes_filter"] = [t for t in prev if t in shared]
        if not shared:
            st.caption(
                "No themes are shared by that many companies — lower the threshold."
            )
        sel = st.multiselect(
            "Filter companies by theme (from full notes)",
            shared,
            format_func=lambda t: f"{t}  ({theme_counts[t]})",
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
