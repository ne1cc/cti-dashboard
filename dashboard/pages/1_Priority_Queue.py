"""Feasibility Review Priority Queue — ranked segments for human review."""

import pandas as pd
import plotly.express as px
import streamlit as st
from components import data
from components.filters import segment_filters
from components.guardrails import guarded_footer, page_setup
from components.profile import render_profile_selector

page_setup("Feasibility Review Priority Queue")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

queue = data.priority_queue(profile_id)
filtered = segment_filters(queue)

band_options = ["priority_review", "review", "watch"]
selected_bands = st.sidebar.multiselect("Priority band", band_options)
if selected_bands:
    pre_band_count = len(filtered)
    filtered = filtered[filtered["priority_band"].isin(selected_bands)]
    st.sidebar.caption(
        f"{len(filtered):,} of {pre_band_count:,} rows shown after priority band filter"
    )

band_counts = filtered["priority_band"].value_counts()
col1, col2, col3 = st.columns(3)
col1.metric("Priority review", int(band_counts.get("priority_review", 0)))
col2.metric("Review", int(band_counts.get("review", 0)))
col3.metric("Watch", int(band_counts.get("watch", 0)))

if bool(filtered["growth_uses_registry_proxy_flag"].any()):
    st.warning(
        "Growth component currently uses the registry first-post-date proxy "
        "because multi-snapshot history has not accrued yet."
    )

st.subheader("Ranked queue")
st.caption("Click a row to see its full score breakdown below.")
queue_columns = [
    "priority_rank",
    "condition_group",
    "state_normalized",
    "phase_normalized",
    "feasibility_review_priority_score",
    "priority_band",
    "recruiting_trial_count",
    "sponsor_hhi",
    "site_overlap_share",
    "data_confidence_share",
    "priority_explanation",
]
queue_event = st.dataframe(
    filtered[queue_columns],
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
)

export_columns = [
    "priority_rank",
    "condition_group",
    "state_normalized",
    "phase_normalized",
    "feasibility_review_priority_score",
    "priority_band",
    "recruiting_trial_count",
    "sponsor_hhi",
    "site_overlap_share",
    "data_confidence_share",
    "normalized_recruiting_trial_count",
    "normalized_recent_recruiting_growth",
    "normalized_sponsor_concentration",
    "normalized_site_overlap",
    "normalized_data_confidence_adjustment",
    "priority_explanation",
    "interpretation_note",
]
st.download_button(
    "Download filtered queue as CSV",
    filtered[export_columns].to_csv(index=False).encode("utf-8"),
    file_name="feasibility_priority_queue.csv",
    mime="text/csv",
    help="Exports exactly the rows and filters currently shown above, "
    "including the interpretation note on every row.",
)

selected_rows = queue_event.selection.rows if queue_event.selection else []
if selected_rows:
    segment = filtered.iloc[selected_rows[0]]
    st.subheader(
        f"Score breakdown — {segment['condition_group']} · "
        f"{segment['state_normalized']} · {segment['phase_normalized']} "
        f"(rank #{int(segment['priority_rank'])})"
    )
    breakdown_rows = [
        (
            "Recruiting density",
            f"{int(segment['recruiting_trial_count'])} recruiting trials",
            segment["normalized_recruiting_trial_count"],
            segment["weight_recruiting_trial_count"],
            segment["weighted_recruiting_trial_count"],
        ),
        (
            "Recent growth",
            f"{int(segment['recent_growth_input'])} newly recruiting",
            segment["normalized_recent_recruiting_growth"],
            segment["weight_recent_recruiting_growth"],
            segment["weighted_recent_recruiting_growth"],
        ),
        (
            "Sponsor concentration",
            f"HHI {segment['sponsor_hhi']:.2f} across {int(segment['sponsor_count'])} sponsor(s)",
            segment["normalized_sponsor_concentration"],
            segment["weight_sponsor_concentration"],
            segment["weighted_sponsor_concentration"],
        ),
        (
            "Site overlap",
            f"{segment['site_overlap_share'] * 100:.0f}% multi-trial facility share",
            segment["normalized_site_overlap"],
            segment["weight_site_overlap"],
            segment["weighted_site_overlap"],
        ),
        (
            "Data confidence",
            f"{segment['data_confidence_share'] * 100:.0f}% confidence",
            segment["normalized_data_confidence_adjustment"],
            segment["weight_data_confidence_adjustment"],
            segment["weighted_data_confidence_adjustment"],
        ),
    ]
    breakdown = pd.DataFrame(
        breakdown_rows,
        columns=["Component", "Raw value", "Normalized (0-1)", "Weight", "Weighted contribution"],
    )
    st.dataframe(breakdown, hide_index=True, width="stretch")
    weighted_total = sum(row[4] for row in breakdown_rows)
    st.metric(
        "Weighted total (sum of weighted contributions)",
        f"{weighted_total:.4f}",
        help="Matches feasibility_review_priority_score for this segment "
        "(verified by the assert_weighted_components_sum_to_score dbt test).",
    )
    st.caption(segment["priority_explanation"])

st.subheader("Score composition (top 15 shown)")
top = filtered.head(15).copy()
top["segment"] = (
    top["condition_group"] + " · " + top["state_normalized"] + " · " + top["phase_normalized"]
)
components = {
    "normalized_recruiting_trial_count": "Recruiting density",
    "normalized_recent_recruiting_growth": "Recent growth",
    "normalized_sponsor_concentration": "Sponsor concentration",
    "normalized_site_overlap": "Site overlap",
    "normalized_data_confidence_adjustment": "Data confidence",
}
melted = top.melt(
    id_vars="segment",
    value_vars=list(components),
    var_name="component",
    value_name="normalized value",
)
melted["component"] = melted["component"].map(components)
fig = px.bar(
    melted,
    y="segment",
    x="normalized value",
    color="component",
    orientation="h",
    title="Normalized (unweighted) component values per segment",
)
fig.update_layout(yaxis=dict(autorange="reversed"), height=520)
st.plotly_chart(fig, width="stretch")
st.caption(
    "Bars show normalized component inputs before weighting; the score "
    "applies the weights in config/score_weights.yml."
)

if not filtered.empty:
    st.info(filtered.iloc[0]["interpretation_note"])

guarded_footer()
