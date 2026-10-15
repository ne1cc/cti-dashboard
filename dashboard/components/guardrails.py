"""Interpretation guardrails shown on every dashboard page."""

from __future__ import annotations

import streamlit as st

from components.scroll_fix import apply_table_scroll_fix

DISCLAIMER = (
    "**Planning signal only.** All figures are derived from public "
    "ClinicalTrials.gov registry listings. They represent *potential "
    "competition signals* for feasibility review — **not** recruitment "
    "forecasts, patient availability, site capacity, or trial outcomes. "
    "No contact or investigator information is collected or shown."
)

PROXY_NOTE = (
    "Snapshot-transition metrics stay at zero until this project has "
    "accrued multiple snapshots over time; where a registry-date proxy is "
    "used instead, it is labeled as such."
)


def page_setup(title: str, icon: str = ":material/monitor_heart:") -> None:
    st.set_page_config(page_title=title, page_icon=icon, layout="wide")
    apply_table_scroll_fix()
    st.title(title)
    st.info(DISCLAIMER, icon=":material/policy:")


def proxy_caption() -> None:
    st.caption(PROXY_NOTE)


def guarded_footer() -> None:
    st.divider()
    st.caption(
        "Source: ClinicalTrials.gov API v2 (public registry). "
        "Facility identity is best-effort text matching. "
        "See the Data Reliability page for run-level confidence."
    )
