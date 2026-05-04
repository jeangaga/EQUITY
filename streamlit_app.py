"""Macro / Earnings dashboard — Streamlit entry point.

The five earnings sub-tabs are exposed at the top level so **PM Read-Across**
is the first thing the user sees on launch (Streamlit always selects the
first tab by default).

When you add more dashboards in the future (e.g. Rates, FX, Macro), wrap the
earnings sub-tabs in an outer ``st.tabs([...])`` again — but keep
``"PM Read-Across"`` (or whatever earnings sub-tab you want to lead with) at
position 0 inside the earnings group, and put the earnings group at position 0
of the outer tabs so the landing view stays PM Read-Across.
"""

from __future__ import annotations

import streamlit as st

from earnings.ui import render_earnings_tab

st.set_page_config(
    page_title="Macro / Earnings Dashboard",
    page_icon=":bar_chart:",
    layout="wide",
)

st.markdown(
    "<h2 style='margin-bottom:0;'>Macro / Earnings Dashboard</h2>",
    unsafe_allow_html=True,
)

render_earnings_tab()
