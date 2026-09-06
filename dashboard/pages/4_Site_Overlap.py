"""Site Overlap — facilities listed by multiple recruiting trials."""

import plotly.express as px
import streamlit as st
from components import data
from components.guardrails import guarded_footer, page_setup
from components.profile import render_profile_selector

page_setup("Site Overlap")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

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
col1.metric("Facilities shown", f"{len(filtered):,}")
col2.metric(
    "Multi-trial facilities (all states)",
    f"{int(overlap['repeated_site_participation_flag'].sum()):,}",
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

guarded_footer()
