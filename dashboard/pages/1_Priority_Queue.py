"""Feasibility Review Priority Queue — ranked segments for human review."""

import pandas as pd
import plotly.express as px
import streamlit as st
from components import data
from components.audit_panel import render_metric_audit
from components.filters import segment_filters
from components.guardrails import guarded_footer, page_setup
from components.guidance import format_condition_group, render_page_guide
from components.profile import render_profile_selector

page_setup("Feasibility Review Priority Queue")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_page_guide("priority_queue")

queue = data.priority_queue(profile_id)
queue["priority_explanation"] = (
    queue["priority_explanation"]
    .str.replace("data confidence", "legacy operational completeness adjustment", regex=False)
    .str.replace("relative density", "relative trial count", regex=False)
)
st.caption(
    "Legacy operational completeness adjustment: 0.5 × "
    "record-quality-ok share + 0.5 × latest successful-run "
    "usable-location share. This is an operational score input; "
    "scientific validity is not measured."
)
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
col1.metric(
    "Priority review",
    int(band_counts.get("priority_review", 0)),
    help=(
        "Score at least 0.70, reflecting elevated recruiting trial counts or rapid recent growth."
    ),
)
col2.metric(
    "Review",
    int(band_counts.get("review", 0)),
    help=("Score from 0.45 to below 0.70, reflecting trial counts and viable site capacity."),
)
col3.metric(
    "Watch",
    int(band_counts.get("watch", 0)),
    help=(
        "Score below 0.45, reflecting low trial counts—"
        "promising targets for community-based recruitment."
    ),
)

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
display_df = filtered[queue_columns].copy()
display_df["condition_group"] = display_df["condition_group"].apply(format_condition_group)

queue_event = st.dataframe(
    display_df,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "priority_rank": st.column_config.NumberColumn("Rank", format="%d"),
        "condition_group": st.column_config.TextColumn("Condition Group"),
        "state_normalized": st.column_config.TextColumn("State"),
        "phase_normalized": st.column_config.TextColumn("Phase"),
        "feasibility_review_priority_score": st.column_config.NumberColumn(
            "Priority Score", format="%.4f", help="Weighted composite score (0.0 - 1.0)"
        ),
        "priority_band": st.column_config.TextColumn("Band"),
        "recruiting_trial_count": st.column_config.NumberColumn("Recruiting Trials", format="%d"),
        "sponsor_hhi": st.column_config.NumberColumn(
            "Sponsor HHI", format="%.3f", help="Herfindahl-Hirschman Index of sponsor concentration"
        ),
        "site_overlap_share": st.column_config.NumberColumn(
            "Site Overlap", format="%.1%", help="Share of listed facilities hosting multiple trials"
        ),
        "data_confidence_share": st.column_config.NumberColumn(
            "Legacy operational completeness adjustment",
            format="%.1%",
            help="0.5 record-quality-ok share + 0.5 latest successful-run usable-location share",
        ),
        "priority_explanation": st.column_config.TextColumn("Deterministic Explanation"),
    },
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
    filtered[export_columns]
    .rename(
        columns={
            "data_confidence_share": "legacy_operational_completeness_adjustment",
            "normalized_data_confidence_adjustment": (
                "normalized_legacy_operational_completeness_adjustment"
            ),
        }
    )
    .to_csv(index=False)
    .encode("utf-8"),
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
            "Recruiting trial count",
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
            "Legacy operational completeness adjustment",
            f"{segment['data_confidence_share'] * 100:.0f}% operational completeness adjustment",
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
    "normalized_recruiting_trial_count": "Recruiting trial count",
    "normalized_recent_recruiting_growth": "Recent growth",
    "normalized_sponsor_concentration": "Sponsor concentration",
    "normalized_site_overlap": "Site overlap",
    "normalized_data_confidence_adjustment": "Legacy operational completeness adjustment",
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

render_metric_audit(
    profile_id,
    filters=filtered.attrs.get("audit_filters", {}),
    segments=filtered,
    context={
        "priority_bands": selected_bands,
        "derived_rules": (
            "Each component uses (input - profile minimum)/(profile maximum - minimum), "
            "with zero for no spread; weighted sum uses the active score weights. "
            "Bands are fixed score thresholds: priority_review >=0.70, review >=0.45, "
            "watch otherwise. Recruiting inputs and sponsor HHI use current segment "
            "counts; HHI sums squared normalized lead-sponsor shares of segment trials. "
            "Growth sums daily recruiting entrants over 90 days, requiring a previous "
            "non-null non-recruiting status; with one captured date, first-post >= "
            "snapshot minus 90 days is the proxy. Site overlap is distinct recruiting "
            "segment trials sharing any facility with >1 recruiting trial in the "
            "profile divided by distinct recruiting segment trials. Legacy operational "
            "completeness = 0.5 segment record-quality-ok share + 0.5 latest successful-run "
            "usable-location share (missing shares coalesce to zero). This panel audits "
            "count inputs; derived score values remain warehouse calculations."
        ),
    },
    key="priority",
)

guarded_footer()
