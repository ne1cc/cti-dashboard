"""Trial Explorer — individual registry records with links to ClinicalTrials.gov."""

from typing import Any

import streamlit as st
from components import data
from components.formatting import format_phase
from components.guardrails import guarded_footer, page_setup
from components.guidance import registry_link_guidance, render_indication_banner, render_page_guide
from components.profile import render_profile_selector

page_setup("Trial Explorer")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_indication_banner(profile_id)
render_page_guide("trial_explorer", profile_id)

trials = data.trial_explorer(profile_id)

col1, col2 = st.columns(3)[:2]
col1.metric(
    "Trials in this profile",
    f"{len(trials):,}",
    help=(
        "Total individual clinical study records stored in the analytics "
        "warehouse for this profile."
    ),
)
col2.metric(
    "Currently recruiting",
    f"{int((trials['overall_status'] == 'RECRUITING').sum()):,}",
    help="Studies marked with overall status RECRUITING in the latest snapshot.",
)

statuses = sorted(trials["overall_status"].dropna().unique())
phases = sorted(trials["phase"].dropna().unique())

fcol1, fcol2, fcol3 = st.columns(3)
status_sel = fcol1.multiselect("Overall status", statuses, default=[])
phase_sel = fcol2.multiselect("Phase", phases, default=[])
search = fcol3.text_input("Search title / sponsor / NCT ID")

filtered = trials
if status_sel:
    filtered = filtered[filtered["overall_status"].isin(status_sel)]
if phase_sel:
    filtered = filtered[filtered["phase"].isin(phase_sel)]
if search:
    needle = search.strip().lower()
    mask = (
        filtered["brief_title"].str.lower().str.contains(needle, na=False)
        | filtered["lead_sponsor"].str.lower().str.contains(needle, na=False)
        | filtered["nct_id"].str.lower().str.contains(needle, na=False)
    )
    filtered = filtered[mask]

st.caption(f"{len(filtered):,} of {len(trials):,} trials shown.")

display_trials = filtered.copy()
display_trials["phase"] = display_trials["phase"].map(format_phase)
display_trials["overall_status"] = (
    display_trials["overall_status"].str.replace("_", " ").str.capitalize()
)

column_config: dict[str, Any] = {
    "overall_status": st.column_config.TextColumn("Status"),
    "phase": st.column_config.TextColumn("Phase"),
    "lead_sponsor": st.column_config.TextColumn("Lead sponsor"),
    "registry_url": st.column_config.LinkColumn(
        "Registry record",
        help="Opens the public ClinicalTrials.gov record",
        display_text="Open registry",
    ),
    "nct_id": st.column_config.TextColumn("NCT ID"),
    "brief_title": st.column_config.TextColumn("Brief title", width="large"),
    "study_first_post_date": st.column_config.DateColumn("First posted", format="MMM D, YYYY"),
    "enrollment_count": st.column_config.NumberColumn(
        "Reported enrollment",
        format="%d",
        help="Registry-reported enrollment; may be estimated or actual",
    ),
    "us_states": st.column_config.TextColumn("Listed U.S. states"),
}
if "indication_profile_id" in filtered.columns:
    column_config["indication_profile_id"] = None

st.dataframe(
    display_trials,
    column_order=[
        "brief_title",
        "phase",
        "overall_status",
        "lead_sponsor",
        "us_states",
        "enrollment_count",
        "study_first_post_date",
        "nct_id",
        "registry_url",
    ],
    hide_index=True,
    width="content",
    column_config=column_config,
)

st.caption(
    "Each row is a public registry record as of this project's latest snapshot. "
    + registry_link_guidance()
    + " Reported enrollment may be estimated or actual; this view does not distinguish the type."
)

guarded_footer()
