"""Sponsor Landscape — lead sponsors of currently recruiting trials."""

import plotly.express as px
import streamlit as st
from components import data
from components.guardrails import guarded_footer, page_setup
from components.guidance import render_page_guide

page_setup("Sponsor Landscape")
data.require_warehouse()

render_page_guide("sponsor_landscape")

sponsors = data.sponsor_landscape()

col1, col2 = st.columns(2)
col1.metric(
    "Lead sponsors (recruiting)",
    f"{len(sponsors):,}",
    help=(
        "Organizations designated as the primary/lead sponsor on "
        "currently recruiting ADRD clinical trials."
    ),
)
col2.metric(
    "Recruiting listings",
    f"{int(sponsors['recruiting_trial_count'].sum()):,}",
    help="Total actively recruiting study protocols sponsored across all organizations.",
)

st.subheader("Top lead sponsors by recruiting listings")
top = sponsors.head(20)
fig = px.bar(
    top,
    y="lead_sponsor",
    x="recruiting_trial_count",
    color="sponsor_class",
    orientation="h",
    labels={
        "lead_sponsor": "Lead Sponsor",
        "recruiting_trial_count": "Recruiting Trials",
        "sponsor_class": "Sponsor Class",
    },
)
fig.update_layout(yaxis=dict(autorange="reversed"), height=560)
st.plotly_chart(fig, width="stretch")

st.subheader("All lead sponsors")
st.dataframe(
    sponsors,
    hide_index=True,
    width="stretch",
    column_config={
        "lead_sponsor": st.column_config.TextColumn("Lead Sponsor"),
        "recruiting_trial_count": st.column_config.NumberColumn(
            "Recruiting Trials", format="%d", help="Actively recruiting protocols"
        ),
        "total_trial_count": st.column_config.NumberColumn(
            "Total Trials", format="%d", help="All trials sponsored in the warehouse"
        ),
        "sponsor_class": st.column_config.TextColumn("Sponsor Class"),
    },
)

st.caption(
    "Counts of registry listings by lead sponsor — not market share, "
    "spend, or enrollment performance."
)

guarded_footer()
