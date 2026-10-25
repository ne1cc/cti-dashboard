"""Registry Segments — transparent, sortable segments for human feasibility review."""

import pandas as pd
import plotly.express as px
import streamlit as st
from components import data
from components.audit_panel import PRIORITY_DERIVED_RULES, render_metric_audit
from components.filters import segment_filters
from components.formatting import format_phase, format_priority_explanation
from components.guardrails import guarded_footer, page_setup
from components.guidance import format_condition_group, render_page_guide
from components.profile import render_profile_selector

page_setup("Registry Segments")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_page_guide("priority_queue", profile_id)

queue = data.priority_queue(profile_id)
st.caption(
    "Segments are defined by condition group, U.S. state, and clinical trial phase. "
    "Sort directly by any raw metric below (e.g. active recruiting trials, sponsor "
    "concentration HHI, or site overlap) according to your operational screening "
    "criteria. Scientific validity is not measured."
)
filtered = segment_filters(queue)

if bool(filtered["growth_uses_registry_proxy_flag"].any()):
    st.warning(
        "Recent recruiting growth metric (recent_growth_input) currently uses "
        "the registry first-post-date proxy because multi-snapshot history has "
        "not accrued yet."
    )

st.subheader("Registry segments")
st.caption(
    "Default sort is active recruiting trials descending. "
    "Click column headers to sort by sponsor concentration HHI, site overlap, or trial counts. "
    "Click a row to inspect segment details."
)

queue_columns = [
    "condition_group",
    "state_normalized",
    "phase_normalized",
    "active_recruiting_trials",
    "sponsor_hhi",
    "site_overlap_rate",
    "distinct_sponsors",
    "listed_site_count",
    "data_confidence_share",
    "priority_explanation",
]
display_df = filtered[queue_columns].copy()
display_df["condition_group"] = display_df["condition_group"].apply(format_condition_group)
display_df["phase_normalized"] = display_df["phase_normalized"].map(format_phase)
display_df["priority_explanation"] = [
    format_priority_explanation(row) for _, row in filtered.iterrows()
]

queue_event = st.dataframe(
    display_df,
    row_height=76,
    height=440,
    hide_index=True,
    width="content",
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "condition_group": st.column_config.TextColumn("Condition Group", width=220),
        "state_normalized": st.column_config.TextColumn("State", width=70),
        "phase_normalized": st.column_config.TextColumn("Phase", width=110),
        "active_recruiting_trials": st.column_config.NumberColumn(
            "Recruiting Trials",
            format="%d",
            width=135,
            help="Actively recruiting trials in this segment (status RECRUITING)",
        ),
        "sponsor_hhi": st.column_config.NumberColumn(
            "Sponsor concentration",
            format="%.3f",
            width=150,
            help="Herfindahl-Hirschman Index (0–1); higher means fewer sponsors dominate",
        ),
        "site_overlap_rate": st.column_config.NumberColumn(
            "Site Overlap",
            format="percent",
            width=110,
            help=(
                "Share of recruiting trials sharing facilities with another competing "
                "trial in this segment"
            ),
        ),
        "distinct_sponsors": st.column_config.NumberColumn(
            "Sponsors",
            format="%d",
            width=90,
            help="Distinct trial sponsor organizations operating in this segment",
        ),
        "listed_site_count": st.column_config.NumberColumn(
            "Listed Sites",
            format="%d",
            width=100,
            help="Attributable clinical facility listings in this state for this segment",
        ),
        "data_confidence_share": st.column_config.NumberColumn(
            "Completeness adjustment",
            format="percent",
            width=160,
            help="0.5 record-quality-ok share + 0.5 latest usable-location share",
        ),
        "priority_explanation": st.column_config.TextColumn(
            "Explanation",
            width=700,
            help="Descriptive segment summary from public registry data.",
        ),
    },
)

export_columns = [
    "condition_group",
    "state_normalized",
    "phase_normalized",
    "active_recruiting_trials",
    "recruiting_trial_count",
    "sponsor_hhi",
    "site_overlap_rate",
    "distinct_sponsors",
    "listed_site_count",
    "recent_growth_input",
    "data_confidence_share",
    "priority_explanation",
    "interpretation_note",
]
st.download_button(
    "Download filtered segments as CSV",
    filtered[export_columns]
    .rename(
        columns={
            "data_confidence_share": "legacy_operational_completeness_adjustment",
        }
    )
    .to_csv(index=False)
    .encode("utf-8"),
    file_name="registry_segments.csv",
    mime="text/csv",
    help="Exports exactly the rows and filters currently shown above, "
    "including the interpretation note on every row.",
)

selected_rows = (
    queue_event.get("selection", {}).get("rows", []) if isinstance(queue_event, dict) else []
)
if selected_rows:
    segment = filtered.iloc[selected_rows[0]]
    st.subheader(
        f"Segment details — {format_condition_group(segment['condition_group'])} · "
        f"{segment['state_normalized']} · {format_phase(segment['phase_normalized'])}"
    )
    growth_label = (
        "First-posted registry proxy"
        if bool(segment.get("growth_uses_registry_proxy_flag", False))
        else "Recent recruiting growth"
    )
    growth_val = (
        f"{int(segment['recent_growth_input'])} newly posted (proxy)"
        if bool(segment.get("growth_uses_registry_proxy_flag", False))
        else f"{int(segment['recent_growth_input'])} newly recruiting"
    )
    details_rows = [
        ("Active recruiting trials", f"{int(segment['active_recruiting_trials'])} trials"),
        (growth_label, growth_val),
        ("Sponsor count", f"{int(segment['distinct_sponsors'])} distinct sponsor(s)"),
        ("Sponsor concentration HHI", f"{segment['sponsor_hhi']:.3f}"),
        ("Top sponsor share", f"{segment['top_sponsor_share'] * 100:.1f}%"),
        ("Site overlap rate", f"{segment['site_overlap_rate'] * 100:.1f}% of trials"),
        ("Listed site affiliations", f"{int(segment['listed_site_count'])} sites"),
        ("Operational completeness", f"{segment['data_confidence_share'] * 100:.1f}%"),
    ]
    details_df = pd.DataFrame(details_rows, columns=["Metric", "Observed Value"])
    st.dataframe(
        details_df,
        hide_index=True,
        width="content",
        column_config={
            "Metric": st.column_config.TextColumn("Registry Metric", width="large"),
            "Observed Value": st.column_config.TextColumn("Observed Value", width="large"),
        },
    )
    st.caption(segment["priority_explanation"])

st.subheader("Active Recruiting Trials by Segment (Top 15)")
top = filtered.head(15).copy()
top["segment"] = (
    top["condition_group"] + " · " + top["state_normalized"] + " · " + top["phase_normalized"]
)
fig = px.bar(
    top,
    y="segment",
    x="active_recruiting_trials",
    orientation="h",
    title="Active recruiting trials across top segments",
    labels={"segment": "Segment", "active_recruiting_trials": "Active Recruiting Trials"},
)
fig.update_layout(yaxis=dict(autorange="reversed"), height=480)
st.plotly_chart(fig, width="stretch")

if not filtered.empty:
    st.info(filtered.iloc[0]["interpretation_note"])

render_metric_audit(
    profile_id,
    filters=filtered.attrs.get("audit_filters", {}),
    segments=filtered,
    context={
        "derived_rules": PRIORITY_DERIVED_RULES,
    },
    key="priority",
)

guarded_footer()
