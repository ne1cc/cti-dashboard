"""Competition Landscape — recruiting density and sponsor concentration."""

import plotly.express as px
import streamlit as st
from components import data
from components.filters import segment_filters
from components.guardrails import guarded_footer, page_setup, proxy_caption
from components.profile import render_profile_selector

page_setup("Competition Landscape")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

competition = data.recruiting_competition(profile_id)
filtered = segment_filters(competition)

col1, col2, col3 = st.columns(3)
col1.metric("Segments", len(filtered))
col2.metric(
    "Elevated-signal segments",
    int((filtered["competition_signal_band"] == "elevated").sum()),
)
col3.metric(
    "Recruiting listings",
    int(filtered["recruiting_trial_count"].sum()),
)
proxy_caption()

st.subheader("Recruiting density vs sponsor concentration")
fig = px.scatter(
    filtered,
    x="recruiting_trial_count",
    y="sponsor_hhi",
    size="listed_site_count",
    color="competition_signal_band",
    category_orders={"competition_signal_band": ["low", "moderate", "elevated"]},
    hover_data=["condition_group", "state_normalized", "phase_normalized", "sponsor_count"],
    labels={
        "recruiting_trial_count": "Recruiting listings in segment",
        "sponsor_hhi": "Sponsor HHI (0..1)",
        "competition_signal_band": "Signal band",
    },
)
st.plotly_chart(fig, width="stretch")
st.caption(
    "Each point is a condition x state x phase segment at the latest "
    "snapshot. Bubble size = listed sites. Bands are relative percentile "
    "cuts, not absolute judgments."
)

st.subheader("Segments by signal band")
st.dataframe(
    filtered.sort_values(
        ["competition_signal_band", "recruiting_trial_count"], ascending=[True, False]
    )[
        [
            "condition_group",
            "state_normalized",
            "phase_normalized",
            "recruiting_trial_count",
            "listed_site_count",
            "new_recruiting_90d",
            "newly_posted_90d_proxy",
            "sponsor_count",
            "top_sponsor_share",
            "sponsor_hhi",
            "competition_signal_band",
        ]
    ],
    hide_index=True,
    width="stretch",
)

guarded_footer()
