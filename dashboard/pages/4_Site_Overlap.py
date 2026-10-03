"""Site Overlap — facilities listed by multiple recruiting trials."""

import plotly.express as px
import streamlit as st
from components import data
from components.audit_panel import render_metric_audit
from components.guardrails import guarded_footer, page_setup
from components.guidance import render_page_guide
from components.profile import render_profile_selector

page_setup("Site Overlap")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_page_guide("site_overlap")

overlap = data.site_overlap(profile_id)

st.sidebar.header("Filters")
states = sorted(overlap["state_normalized"].dropna().unique())
selected_states = st.sidebar.multiselect("State", states)
only_repeated = st.sidebar.checkbox("Only multi-trial facilities", value=True)

filtered = overlap
if selected_states:
    filtered = filtered[filtered["state_normalized"].isin(selected_states)]
if only_repeated:
    filtered = filtered[filtered["repeated_site_participation_flag"]]

col1, col2 = st.columns(2)
col1.metric(
    "Facilities shown",
    f"{len(filtered):,}",
    help="Number of clinical facilities matching current state and overlap filters.",
)
col2.metric(
    "Multi-trial facilities (all states)",
    f"{int(overlap['repeated_site_participation_flag'].sum()):,}",
    help=(
        "Total facilities across the U.S. listed as active study sites "
        "for 2 or more recruiting trials."
    ),
)

st.subheader("Facilities by recruiting-trial listings")
st.dataframe(
    filtered[
        [
            "facility_name",
            "city",
            "state_normalized",
            "recruiting_trial_count",
            "listed_trial_count",
            "sponsor_count",
            "phase_mix",
        ]
    ].head(200),
    hide_index=True,
    width="stretch",
    column_config={
        "facility_name": st.column_config.TextColumn("Facility Name"),
        "city": st.column_config.TextColumn("City"),
        "state_normalized": st.column_config.TextColumn("State"),
        "recruiting_trial_count": st.column_config.NumberColumn(
            "Recruiting Trials",
            format="%d",
            help="Count of currently recruiting trials at this facility",
        ),
        "listed_trial_count": st.column_config.NumberColumn(
            "Total Trials Listed",
            format="%d",
            help="All historical or active trials listing this site",
        ),
        "sponsor_count": st.column_config.NumberColumn("Sponsors", format="%d"),
        "phase_mix": st.column_config.TextColumn(
            "Phase Mix", help="Distribution of trial phases active at this location"
        ),
    },
)

st.subheader("States with the most multi-trial facilities")
by_state = (
    overlap[overlap["repeated_site_participation_flag"]]
    .groupby("state_normalized", as_index=False)
    .size()
    .rename(columns={"size": "multi_trial_facilities"})
    .sort_values("multi_trial_facilities", ascending=False)
    .head(15)
)
fig = px.bar(by_state, x="state_normalized", y="multi_trial_facilities")
st.plotly_chart(fig, width="stretch")

st.caption(
    "Facility identity is best-effort matching of public listing text "
    "(name + city + state). Overlap indicates shared listings only — it "
    "is not a claim about site workload or performance."
)

render_metric_audit(
    profile_id,
    filters={"states": selected_states},
    metric="facility",
    facilities=filtered[["facility_normalized", "city_normalized", "state_normalized"]]
    .where(filtered.notna(), None)
    .values.tolist(),
    context={
        "only_multi_trial_facilities": only_repeated,
        "identity": (
            "Normalized name + city + state; counts distinct NCT IDs at the facility/date. "
            "Multi-trial requires >1 confirmed recruiting NCT ID within the profile. "
            "Sponsor count and phase mix include all listed studies. "
            "Shared listings do not measure workload."
        ),
    },
    key="sites",
)

render_metric_audit(
    profile_id,
    metric="facility",
    facilities=overlap.loc[
        overlap["repeated_site_participation_flag"],
        ["facility_normalized", "city_normalized", "state_normalized"],
    ]
    .where(overlap.notna(), None)
    .values.tolist(),
    context={
        "displayed_measure": (
            "All-states multi-trial facility card and chart: count "
            "facility identities with >1 confirmed recruiting NCT ID at "
            "the latest profile date. Bar chart shows the top 15 states "
            "from these identities; audit summary counts distinct study "
            "inputs."
        )
    },
    key="sites_all_states",
)

guarded_footer()
