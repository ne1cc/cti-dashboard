"""Competition Landscape — recruiting trial counts and sponsor concentration."""

import plotly.express as px
import streamlit as st
from components import data
from components.audit_panel import render_metric_audit
from components.filters import segment_filters
from components.guardrails import guarded_footer, page_setup, proxy_caption
from components.guidance import format_condition_group, render_page_guide
from components.profile import render_profile_selector

page_setup("Competition Landscape")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_page_guide("competition_landscape")

competition = data.recruiting_competition(profile_id)
filtered = segment_filters(competition)

col1, col2, col3 = st.columns(3)
col1.metric(
    "Segments",
    len(filtered),
    help="Number of distinct condition × state × phase segments matching current sidebar filters.",
)
col2.metric(
    "Elevated-signal segments",
    int((filtered["competition_signal_band"] == "elevated").sum()),
    help=("Segments falling in the top percentile band for trial counts or sponsor concentration."),
)
col3.metric(
    "Recruiting trials (summed segments)",
    int(filtered["recruiting_trial_count"].sum()),
    help=(
        "Sum of distinct recruiting study counts per segment. "
        "A study in multiple segments is counted in each."
    ),
)
proxy_caption()

st.subheader("Recruiting trial count vs sponsor concentration")
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
    "Each point is a condition × state × phase segment at the latest "
    "snapshot. Bubble size = listed sites. Bands are relative percentile "
    "cuts, not absolute judgments."
)

st.subheader("Segments by signal band")
table_df = filtered.sort_values(
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
].copy()
table_df["condition_group"] = table_df["condition_group"].apply(format_condition_group)

st.dataframe(
    table_df,
    hide_index=True,
    width="stretch",
    column_config={
        "condition_group": st.column_config.TextColumn("Condition Group"),
        "state_normalized": st.column_config.TextColumn("State"),
        "phase_normalized": st.column_config.TextColumn("Phase"),
        "recruiting_trial_count": st.column_config.NumberColumn("Recruiting Listings", format="%d"),
        "listed_site_count": st.column_config.NumberColumn("Listed Sites", format="%d"),
        "new_recruiting_90d": st.column_config.NumberColumn("New Recruiting (90d)", format="%d"),
        "newly_posted_90d_proxy": st.column_config.NumberColumn(
            "Newly Posted (90d Proxy)", format="%d"
        ),
        "sponsor_count": st.column_config.NumberColumn("Sponsors", format="%d"),
        "top_sponsor_share": st.column_config.NumberColumn("Top Sponsor Share", format="%.1%"),
        "sponsor_hhi": st.column_config.NumberColumn("Sponsor HHI", format="%.3f"),
        "competition_signal_band": st.column_config.TextColumn("Signal Band"),
    },
)

render_metric_audit(
    profile_id,
    filters=filtered.attrs.get("audit_filters", {}),
    segments=filtered,
    context={
        "derived_rules": (
            "Counts: distinct confirmed RECRUITING NCT IDs per condition/state/phase/date; "
            "Listed sites sum counts of distinct normalized facility/city pairs per study/state "
            "within each segment; duplicates collapse and missing normalized facility names "
            "contribute no identity. City nulls coalesce to empty text in the identity. "
            "HHI = sum((distinct study count per normalized lead sponsor / sum of "
            "sponsor study counts in the segment)^2); top share is the maximum share. "
            "Bands use percent_rank of recruiting trial count across all segments "
            "within profile and snapshot: low <0.5, moderate <0.8, elevated otherwise. "
            "New recruiting (90d) sums daily distinct entrants over the inclusive "
            "90-day range; entrant requires previous captured status non-null and "
            "not RECRUITING. First-post proxy counts recruiting studies with first "
            "post date >= snapshot date minus 90 days. Counts may repeat across segments. "
            "This panel audits count inputs; derived values remain warehouse calculations."
        )
    },
    key="competition",
)

guarded_footer()
