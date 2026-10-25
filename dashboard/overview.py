"""Overview — Clinical Trial Access & Recruitment Competition Intelligence."""

import streamlit as st
from components import data
from components.audit_panel import PRIORITY_DERIVED_RULES, render_metric_audit
from components.formatting import (
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
    "Compare": (
        "Inspect condition × state × phase segments and see where recruiting trials concentrate."
    ),
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
    help="Trials known from retained captures, using each trial's latest-observed record.",
    border=True,
)
col2.metric(
    "Currently recruiting",
    f"{int(metrics['recruiting_trials']):,}",
    help="Trials whose latest-observed overall status is exactly RECRUITING.",
    border=True,
)
col3.metric(
    "States with listed sites",
    int(metrics["states_with_sites"]),
    help=(
        "Distinct U.S. states and territories with usable listed locations "
        "across retained captures."
    ),
    border=True,
)
col4.metric(
    "Listed facilities",
    f"{int(metrics['listed_facilities']):,}",
    help=(
        "Facility identities in the selected profile's latest captured snapshot "
        "(best-effort normalized name + city + state); zero if none are attributable."
    ),
    border=True,
)

runs_count = metrics.get("snapshot_count", metrics.get("warehouse_runs", 0))
st.caption(
    f"Latest snapshot: {format_snapshot_date(metrics['latest_snapshot'])} · "
    f"snapshots accrued: {int(runs_count) if runs_count is not None else 0}"
)
proxy_caption()

st.subheader("Top registry segments by recruiting trials")
queue = data.priority_queue(profile_id)
preview_df = queue.head(10)[
    [
        "condition_group",
        "state_normalized",
        "phase_normalized",
        "active_recruiting_trials",
        "sponsor_hhi",
        "site_overlap_rate",
        "distinct_sponsors",
        "priority_explanation",
    ]
].copy()
preview_df["condition_group"] = preview_df["condition_group"].apply(format_condition_group)
preview_df["phase_normalized"] = preview_df["phase_normalized"].map(format_phase)
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
        "condition_group": st.column_config.TextColumn("Condition Group", width=220),
        "state_normalized": st.column_config.TextColumn("State", width=70),
        "phase_normalized": st.column_config.TextColumn("Phase", width=110),
        "active_recruiting_trials": st.column_config.NumberColumn(
            "Recruiting Trials",
            format="%d",
            width=135,
            help="Actively recruiting trials in this segment",
        ),
        "sponsor_hhi": st.column_config.NumberColumn(
            "Sponsor HHI",
            format="%.3f",
            width=110,
            help="Herfindahl-Hirschman Index (0–1); higher means more concentrated",
        ),
        "site_overlap_rate": st.column_config.NumberColumn(
            "Site Overlap",
            format="percent",
            width=110,
            help="Share of recruiting trials sharing facilities with competing studies",
        ),
        "distinct_sponsors": st.column_config.NumberColumn(
            "Sponsors",
            format="%d",
            width=90,
            help="Distinct sponsors in this segment",
        ),
        "priority_explanation": st.column_config.TextColumn(
            "Explanation",
            width=650,
            help="Descriptive segment summary from public registry data.",
        ),
    },
)
st.page_link(
    "pages/1_Priority_Queue.py",
    label="Open all Registry Segments",
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
            "Trials tracked counts each trial's latest-observed dim_trial row across "
            "retained captures; currently recruiting filters that status exactly RECRUITING."
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
            "Listed facilities counts best-effort normalized name + city + state "
            "identities at the selected profile's latest captured snapshot date; "
            "zero if that capture has no attributable facilities. States with sites "
            "counts distinct profile/state geography rows across retained captures. "
            "This combined audit keeps historical study observations and original "
            "locations across retained captures; its summary counts distinct studies, "
            "not either location card's scalar."
        )
    },
    key="overview_locations",
    label="Audit: states and listed facilities",
)
render_metric_audit(
    profile_id,
    segments=queue.head(10),
    context={
        "scope": "Queue preview row inputs; sidebar scope before segment filters.",
        "derived_rules": PRIORITY_DERIVED_RULES,
    },
    key="overview_queue",
    label="Audit: priority queue preview",
)

guarded_footer()
