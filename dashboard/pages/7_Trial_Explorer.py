"""Trial Explorer — individual registry records with links to ClinicalTrials.gov."""

import streamlit as st
from components import data
from components.guardrails import guarded_footer, page_setup
from components.guidance import render_indication_banner, render_page_guide

page_setup("Trial Explorer")
data.require_warehouse()

render_indication_banner()
render_page_guide("trial_explorer")

trials = data.trial_explorer()

col1, col2 = st.columns(3)[:2]
col1.metric(
    "Trials in warehouse",
    f"{len(trials):,}",
    help="Total individual clinical study records stored in the analytics warehouse.",
)
col2.metric(
    "Currently recruiting",
    f"{int((trials['overall_status'] == 'RECRUITING').sum()):,}",
    help="Studies marked with overall status RECRUITING in the latest snapshot.",
)

statuses = sorted(trials["overall_status"].dropna().unique())
phases = sorted(trials["phase"].dropna().unique())
indications = (
    sorted(trials["indication_profile_id"].dropna().unique())
    if "indication_profile_id" in trials.columns
    else []
)

has_multiple_indications = len(indications) > 1
if has_multiple_indications:
    fcol1, fcol2, fcol3, fcol4 = st.columns(4)
    ind_sel = fcol1.multiselect("Indication", indications, default=[])
    status_sel = fcol2.multiselect("Overall status", statuses, default=[])
    phase_sel = fcol3.multiselect("Phase", phases, default=[])
    search = fcol4.text_input("Search title / sponsor / NCT ID")
else:
    fcol1, fcol2, fcol3 = st.columns(3)
    ind_sel = []
    status_sel = fcol1.multiselect("Overall status", statuses, default=[])
    phase_sel = fcol2.multiselect("Phase", phases, default=[])
    search = fcol3.text_input("Search title / sponsor / NCT ID")

filtered = trials
if ind_sel:
    filtered = filtered[filtered["indication_profile_id"].isin(ind_sel)]
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

column_config = {
    "registry_url": st.column_config.LinkColumn(
        "Registry record",
        help="Opens the public ClinicalTrials.gov record",
        display_text="View on ClinicalTrials.gov",
    ),
    "nct_id": st.column_config.TextColumn("NCT ID"),
    "brief_title": st.column_config.TextColumn("Brief title", width="large"),
    "study_first_post_date": st.column_config.DateColumn("First posted"),
    "enrollment_count": st.column_config.NumberColumn("Planned enrollment"),
    "us_states": st.column_config.TextColumn("Listed U.S. states"),
}
if "indication_profile_id" in filtered.columns:
    column_config["indication_profile_id"] = st.column_config.TextColumn("Indication")

st.dataframe(
    filtered,
    hide_index=True,
    width="stretch",
    column_config=column_config,
)

st.caption(
    "Each row is a public registry record as of this project's latest snapshot; "
    "the link opens the authoritative current record on ClinicalTrials.gov. "
    "Listed enrollment is the sponsor-reported plan, not actual accrual."
)

guarded_footer()
