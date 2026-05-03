"""Compact institutional styling for the Earnings module.

Kept in one place so the rest of the UI code stays focused on layout / data.
"""

from __future__ import annotations

import streamlit as st

# Color map for state-transition badges. Picked to be readable on both light
# and dark themes.
STATE_COLORS: dict[str, tuple[str, str]] = {
    # (background, text)
    "Improvement":     ("#0f5132", "#d1e7dd"),
    "Continuation":    ("#0c4a6e", "#cfe8f3"),
    "Stabilization":   ("#3f3f46", "#e5e7eb"),
    "Reversal":        ("#7c3a00", "#fde7c8"),  # positive turn -> warm amber
    "Deterioration":   ("#7f1d1d", "#fee2e2"),
    "False dawn":      ("#854d0e", "#fef3c7"),
    "Mixed":           ("#52525b", "#e5e7eb"),
}

STATUS_COLORS: dict[str, tuple[str, str]] = {
    "Done":          ("#0f5132", "#d1e7dd"),
    "Brief":         ("#0c4a6e", "#cfe8f3"),
    "Pending":       ("#3f3f46", "#e5e7eb"),
    "Scouted":       ("#52525b", "#e5e7eb"),
    "Out_of_window": ("#854d0e", "#fef3c7"),
    "Excluded":      ("#7f1d1d", "#fee2e2"),
}

CSS = """
<style>
.earnings-root {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    Helvetica, Arial, sans-serif;
}

/* compact, institutional card */
.e-card {
  border: 1px solid rgba(120,120,140,0.25);
  border-radius: 6px;
  padding: 10px 14px;
  margin: 6px 0 10px 0;
  background: rgba(255,255,255,0.02);
}
.e-card .row1 {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 4px;
}
.e-card .ticker {
  font-weight: 700;
  font-size: 1.05rem;
  letter-spacing: 0.02em;
}
.e-card .company {
  color: #d4d4d8;
  font-size: 0.95rem;
}
.e-card .meta {
  color: #a1a1aa;
  font-size: 0.78rem;
}
.e-card .commentary {
  margin-top: 4px;
  font-size: 0.92rem;
  line-height: 1.45;
}

/* compact badge */
.e-badge {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  vertical-align: middle;
  white-space: nowrap;
}
.e-stars {
  color: #f5b301;
  letter-spacing: 0.04em;
  font-size: 0.85rem;
}
.e-stars .off { color: #4b5563; }

/* Macro / read-across cards */
.e-macro {
  border-left: 3px solid #3b82f6;
  background: rgba(59,130,246,0.06);
  padding: 8px 12px;
  margin: 6px 0;
  border-radius: 0 6px 6px 0;
}
.e-macro h4 {
  margin: 0 0 4px 0;
  font-size: 0.85rem;
  letter-spacing: 0.05em;
  color: #93c5fd;
  text-transform: uppercase;
}
.e-macro p {
  margin: 0;
  font-size: 0.92rem;
  line-height: 1.5;
}

.e-bottom-line {
  border: 1px solid #f5b301;
  background: rgba(245,179,1,0.07);
  border-radius: 8px;
  padding: 10px 14px;
  margin: 8px 0 16px 0;
}
.e-bottom-line h4 {
  margin: 0 0 6px 0;
  font-size: 0.85rem;
  letter-spacing: 0.06em;
  color: #f5b301;
  text-transform: uppercase;
}
.e-bottom-line p {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.55;
}

/* Theme card */
.e-theme {
  border: 1px solid rgba(147,197,253,0.35);
  background: rgba(59,130,246,0.05);
  border-radius: 6px;
  padding: 10px 14px;
  margin: 6px 0;
}
.e-theme h4 {
  margin: 0 0 4px 0;
  font-size: 0.95rem;
  color: #cfe8f3;
}
.e-theme .relrow {
  margin-top: 6px;
  font-size: 0.78rem;
  color: #a1a1aa;
}
.e-theme .reltick {
  display: inline-block;
  padding: 1px 6px;
  margin: 2px 4px 0 0;
  border-radius: 4px;
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(120,120,140,0.25);
  font-weight: 600;
  font-size: 0.72rem;
}

/* small KPI tiles */
.e-kpi {
  border: 1px solid rgba(120,120,140,0.25);
  border-radius: 6px;
  padding: 8px 10px;
  text-align: center;
}
.e-kpi .label {
  color: #a1a1aa;
  font-size: 0.72rem;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.e-kpi .value {
  font-size: 1.4rem;
  font-weight: 700;
  margin-top: 2px;
}

/* segment table 'partial' badge */
.e-partial {
  display: inline-block;
  padding: 1px 6px;
  margin-left: 8px;
  border-radius: 4px;
  background: rgba(245,179,1,0.15);
  color: #f5b301;
  border: 1px solid rgba(245,179,1,0.4);
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  vertical-align: middle;
}
</style>
"""


def inject() -> None:
    """Inject the module's CSS into the current Streamlit page (idempotent)."""
    if not st.session_state.get("_earnings_css_injected"):
        st.markdown(CSS, unsafe_allow_html=True)
        st.session_state["_earnings_css_injected"] = True


def stars_html(n: int, total: int = 4) -> str:
    """Render an importance count as filled / empty stars."""
    n = max(0, min(total, int(n or 0)))
    on = "★" * n
    off = "☆" * (total - n)
    return f'<span class="e-stars">{on}<span class="off">{off}</span></span>'


def state_badge_html(state: str) -> str:
    if not state:
        return ""
    bg, fg = STATE_COLORS.get(state, ("#3f3f46", "#e5e7eb"))
    return (
        f'<span class="e-badge" style="background:{bg};color:{fg};">{state}</span>'
    )


def status_badge_html(status: str) -> str:
    if not status:
        return ""
    bg, fg = STATUS_COLORS.get(status, ("#3f3f46", "#e5e7eb"))
    return (
        f'<span class="e-badge" style="background:{bg};color:{fg};">{status}</span>'
    )
