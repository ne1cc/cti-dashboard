"""Sponsor Landscape — lead sponsors of currently recruiting trials."""

import plotly.express as px
import streamlit as st
from components import data
from components.audit_panel import render_provenance_drawer
from components.formatting import format_phase
from components.guardrails import guarded_footer, page_setup
from components.guidance import render_page_guide
from components.profile import render_preset_selector

page_setup("Sponsor Landscape")
preset = render_preset_selector()
profile_id = preset.preset_id
data.require_warehouse(preset)

render_page_guide("sponsor_landscape", profile_id)

sponsors = data.sponsor_landscape(preset)

col1, col2 = st.columns(2)
with col1:
    st.metric(
        "Lead sponsors (recruiting)",
        f"{len(sponsors):,}",
        help=(
            "Organizations designated as the primary/lead sponsor on "
            "currently recruiting clinical trials."
        ),
    )
    render_provenance_drawer(
        preset,
        metric="latest_study",
        sponsors=sponsors["lead_sponsor"].tolist(),
        key="sponsors",
        label="How this number is computed: Lead sponsors",
        context={
            "displayed_measure": (
                "Lead sponsors: organizations designated as primary/lead sponsor "
                "on currently recruiting clinical trials."
            )
        },
    )
with col2:
    st.metric(
        "Recruiting listings",
        f"{int(sponsors['recruiting_trial_count'].sum()):,}",
        help="Total actively recruiting study protocols sponsored across all organizations.",
    )
    render_provenance_drawer(
        preset,
        metric="latest_study",
        sponsors=sponsors["lead_sponsor"].tolist(),
        key="sponsors_listings",
        label="How this number is computed: Recruiting listings",
        context={
            "displayed_measure": (
                "Recruiting listings: total actively recruiting study protocols "
                "sponsored across all organizations."
            )
        },
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
display_sponsors = sponsors.copy()
display_sponsors["phase_mix"] = display_sponsors["phase_mix"].map(
    lambda value: (
        " · ".join(format_phase(phase.strip()) for phase in value.split("|"))
        if isinstance(value, str)
        else value
    )
)
display_sponsors["sponsor_class"] = (
    display_sponsors["sponsor_class"].str.replace("_", " ").str.title()
)
st.dataframe(
    display_sponsors,
    column_order=["lead_sponsor", "sponsor_class", "recruiting_trial_count", "phase_mix"],
    hide_index=True,
    width="content",
    column_config={
        "lead_sponsor": st.column_config.TextColumn("Lead sponsor", width="large"),
        "indication_profile_id": None,
        "phase_mix": st.column_config.TextColumn("Recruiting study phases"),
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
