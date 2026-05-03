"""Macro / Earnings dashboard — Streamlit entry point.

Currently exposes one main tab: **Earnings**. To add more tabs (e.g. Macro,
Rates, FX), append them to the ``st.tabs([...])`` call below.
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

# Add more top-level tabs here as the dashboard grows.
tabs = st.tabs(["Earnings"])
with tabs[0]:
    render_earnings_tab()
