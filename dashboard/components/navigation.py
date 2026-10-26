"""Sidebar navigation grouped by the review workflow.

Sections follow the README decision workflow: compare the captured landscape,
inspect contributors, then verify evidence quality. ``url_path`` keeps the
addresses file-based navigation published, so links already shared resolve.
"""

from __future__ import annotations

from typing import NamedTuple

import streamlit as st
from streamlit.navigation.page import StreamlitPage


class PageSpec(NamedTuple):
    path: str  # relative to dashboard/
    title: str
    icon: str
    url_path: str | None = None  # None marks the default page at "/"


SECTIONS: dict[str, tuple[PageSpec, ...]] = {
    "": (PageSpec("overview.py", "Overview", ":material/dashboard:"),),
    "Compare": (
        PageSpec(
            "pages/1_Priority_Queue.py",
            "Registry Segments",
            ":material/format_list_numbered:",
            "Priority_Queue",
        ),
        PageSpec(
            "pages/2_Competition_Landscape.py",
            "Competition Landscape",
            ":material/scatter_plot:",
            "Competition_Landscape",
        ),
        PageSpec(
            "pages/3_Geography_Trends.py", "Geography Trends", ":material/map:", "Geography_Trends"
        ),
    ),
    "Inspect": (
        PageSpec(
            "pages/4_Site_Overlap.py", "Site Overlap", ":material/location_on:", "Site_Overlap"
        ),
        PageSpec(
            "pages/5_Sponsor_Landscape.py",
            "Sponsor Landscape",
            ":material/business:",
            "Sponsor_Landscape",
        ),
        PageSpec(
            "pages/7_Trial_Explorer.py", "Trial Explorer", ":material/search:", "Trial_Explorer"
        ),
        PageSpec(
            "pages/8_Trial_Similarity.py",
            "Trial Similarity",
            ":material/compare_arrows:",
            "Trial_Similarity",
        ),
    ),
    "Verify": (
        PageSpec(
            "pages/6_Data_Reliability.py",
            "Data Reliability",
            ":material/fact_check:",
            "Data_Reliability",
        ),
    ),
}


def build_navigation() -> StreamlitPage:
    """Register every page with ``st.navigation`` and return the selected page."""
    return st.navigation(
        {
            section: [
                st.Page(
                    spec.path,
                    title=spec.title,
                    icon=spec.icon,
                    url_path=spec.url_path,
                    default=spec.url_path is None,
                )
                for spec in specs
            ]
            for section, specs in SECTIONS.items()
        }
    )
