"""Overview — Clinical Trial Access & Recruitment Competition Intelligence."""

import streamlit as st
from components import data
from components.audit_panel import PRIORITY_DERIVED_RULES, render_metric_audit
from components.formatting import (
    format_band,
    format_phase,
    format_priority_explanation,
    format_snapshot_date,
)
from components.guardrails import guarded_footer, page_setup, proxy_caption
from components.guidance import (
    format_condition_group,
    render_indication_banner,
    render_page_guide,
)
from components.navigation import SECTIONS
from components.profile import render_profile_selector

SECTION_PURPOSE = {
    "Compare": "Rank condition × state × phase segments and see where listings concentrate.",
    "Inspect": "Open the facilities, sponsors and study records behind a segment.",
    "Verify": "Check capture runs, coverage and known limitations before sharing counts.",
}

page_setup("Recruitment Competition Intelligence")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_indication_banner(profile_id)
render_page_guide("overview", profile_id)

metrics = data.overview_metrics(profile_id)

col1, col2, col3, col4 = st.columns(4)
col1.metric(
    "Trials tracked",
    f"{int(metrics['total_trials']):,}",
    help="Total interventional clinical trials with U.S. sites recorded in the warehouse.",
    border=True,
)
col2.metric(
    "Currently recruiting",
    f"{int(metrics['recruiting_trials']):,}",
    help="Active trials with overall status RECRUITING in the latest snapshot.",
    border=True,
)
col3.metric(
    "States with listed sites",
    int(metrics["states_with_sites"]),
    help="Distinct U.S. states and territories containing at least one listed trial facility.",
    border=True,
)
col4.metric(
    "Listed facilities",
    f"{int(metrics['listed_facilities']):,}",
    help=(
        "Total facility listings across all trial site records "
        "(best-effort facility normalization)."
    ),
    border=True,
)

runs_count = metrics.get("snapshot_count", metrics.get("warehouse_runs", 0))
st.caption(
    f"Latest snapshot: {format_snapshot_date(metrics['latest_snapshot'])} · "
    f"snapshots accrued: {int(runs_count) if runs_count is not None else 0}"
)
proxy_caption()

st.subheader("Top of the feasibility review priority queue")
queue = data.priority_queue(profile_id)
queue["priority_explanation"] = (
    queue["priority_explanation"]
    .str.replace("data confidence", "legacy operational completeness adjustment", regex=False)
    .str.replace("relative density", "relative trial count", regex=False)
)
preview_df = queue.head(10)[
    [
        "priority_rank",
        "condition_group",
        "state_normalized",
        "phase_normalized",
        "feasibility_review_priority_score",
        "priority_band",
        "recruiting_trial_count",
        "priority_explanation",
    ]
].copy()
preview_df["condition_group"] = preview_df["condition_group"].apply(format_condition_group)
preview_df["phase_normalized"] = preview_df["phase_normalized"].map(format_phase)
preview_df["priority_band"] = preview_df["priority_band"].map(format_band)
preview_df["priority_explanation"] = [
    format_priority_explanation(row) for _, row in queue.head(10).iterrows()
]

st.dataframe(
    preview_df,
    row_height=76,
    height=440,
    hide_index=True,
    width="content",
    column_config={
        "priority_rank": st.column_config.NumberColumn("Rank", format="%d", width=60),
        "condition_group": st.column_config.TextColumn("Condition Group", width=240),
        "state_normalized": st.column_config.TextColumn("State", width=70),
        "phase_normalized": st.column_config.TextColumn("Phase", width=110),
        "feasibility_review_priority_score": st.column_config.ProgressColumn(
            "Priority Score", format="%.3f", min_value=0.0, max_value=1.0, width=180
        ),
        "priority_band": st.column_config.TextColumn("Band", width=110),
        "recruiting_trial_count": st.column_config.NumberColumn(
            "Recruiting Trials", format="%d", width=125
        ),
        "priority_explanation": st.column_config.TextColumn(
            "Explanation",
            width=700,
            help="HHI measures sponsor concentration from 0–1. Completeness reflects "
            "record and location coverage, not scientific confidence.",
        ),
    },
)
st.page_link(
    "pages/1_Priority_Queue.py",
    label="Open the full Priority Queue",
    icon=":material/arrow_forward:",
)

st.subheader("Where to look next")
for column, (section, purpose) in zip(
    st.columns(len(SECTION_PURPOSE)), SECTION_PURPOSE.items(), strict=True
):
    with column.container(border=True):
        st.markdown(f"**{section}**")
        st.caption(purpose)
        for spec in SECTIONS[section]:
            st.page_link(spec.path, label=spec.title, icon=spec.icon)

st.subheader("Evidence behind these figures")
st.caption(
    "Each audit lists the contributing NCT IDs, coverage, exclusions and rule "
    "versions for one block above, with a JSON export."
)
render_metric_audit(
    profile_id,
    metric="latest_study",
    context={
        "displayed_measure": (
            "Trials tracked counts current dim_trial rows; currently "
            "recruiting filters current overall status exactly RECRUITING."
        )
    },
    key="overview",
    label="Audit: trials tracked and currently recruiting",
)
render_metric_audit(
    profile_id,
    metric="facility",
    observation_metric="history",
    context={
        "displayed_measure": (
            "Listed facilities counts mart_site_overlap facility rows "
            "across captured dates, so repeat facility observations can "
            "count again. States with sites counts profile dim_geography "
            "rows. Inspect original locations and snapshots; the audit "
            "summary counts distinct studies, not facility rows."
        )
    },
    key="overview_locations",
    label="Audit: states and listed facilities",
)
render_metric_audit(
    profile_id,
    segments=queue.head(10),
    context={
        "scope": "Queue preview row inputs; sidebar scope before derived rank filters.",
        "derived_rules": PRIORITY_DERIVED_RULES,
    },
    key="overview_queue",
    label="Audit: priority queue preview",
)

guarded_footer()
