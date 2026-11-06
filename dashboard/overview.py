"""Overview — Clinical Trial Access & Recruitment Competition Intelligence."""

import streamlit as st
from components import data
from components.audit_panel import (
    PRIORITY_DERIVED_RULES,
    render_provenance_drawer,
)
from components.formatting import (
    format_phase,
    format_priority_explanation,
    format_snapshot_date,
    hhi_status,
)
from components.guardrails import guarded_footer, page_setup, proxy_caption
from components.guidance import (
    clinicaltrials_gov_search_url,
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
with col1:
    st.metric(
        "Trials tracked",
        f"{int(metrics['total_trials']):,}",
        help="Trials known from retained captures, using each trial's latest-observed record.",
        border=True,
    )
    render_provenance_drawer(
        profile_id,
        metric="latest_study",
        count_status="all",
        context={
            "displayed_measure": (
                "Trials tracked counts each trial's latest-observed dim_trial row across "
                "retained captures."
            )
        },
        key="overview_trials",
        label="How this number is computed: Trials tracked",
    )
with col2:
    st.metric(
        "Currently recruiting",
        f"{int(metrics['recruiting_trials']):,}",
        help="Trials whose latest-observed overall status is exactly RECRUITING.",
        border=True,
    )
    render_provenance_drawer(
        profile_id,
        metric="latest_study",
        count_status="recruiting",
        context={
            "displayed_measure": (
                "Currently recruiting filters latest-observed status exactly RECRUITING."
            )
        },
        key="overview_recruiting",
        label="How this number is computed: Currently recruiting",
    )
with col3:
    st.metric(
        "States with listed sites",
        int(metrics["states_with_sites"]),
        help=(
            "Distinct U.S. states and territories with usable listed locations "
            "across retained captures."
        ),
        border=True,
    )
    render_provenance_drawer(
        profile_id,
        metric="facility",
        observation_metric="history",
        context={
            "displayed_measure": (
                "States with sites counts distinct profile/state geography rows across "
                "retained captures."
            )
        },
        key="overview_states",
        label="How this number is computed: States with listed sites",
    )
with col4:
    st.metric(
        "Listed facilities",
        f"{int(metrics['listed_facilities']):,}",
        help=(
            "Facility identities in the selected profile's latest captured snapshot "
            "(best-effort normalized name + city + state); zero if none are attributable."
        ),
        border=True,
    )
    render_provenance_drawer(
        profile_id,
        metric="facility",
        observation_metric="history",
        context={
            "displayed_measure": (
                "Listed facilities counts best-effort normalized name + city + state "
                "identities at the selected profile's latest captured snapshot date; "
                "zero if that capture has no attributable facilities."
            )
        },
        key="overview_facilities",
        label="How this number is computed: Listed facilities",
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
        "listed_site_count",
        "priority_explanation",
    ]
].copy()
preview_df["registry_search_url"] = [
    clinicaltrials_gov_search_url(cg, state, profile_id)
    for cg, state in zip(
        preview_df["condition_group"], preview_df["state_normalized"], strict=False
    )
]
preview_df["condition_group"] = preview_df["condition_group"].apply(
    lambda g: format_condition_group(g, profile_id)
)
preview_df["phase_normalized"] = preview_df["phase_normalized"].map(format_phase)
preview_df.insert(
    list(preview_df.columns).index("sponsor_hhi") + 1,
    "hhi_status",
    [
        hhi_status(value, count)
        for value, count in zip(
            preview_df["sponsor_hhi"], preview_df["distinct_sponsors"], strict=True
        )
    ],
)
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
        "hhi_status": st.column_config.TextColumn("HHI status"),
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
        "listed_site_count": st.column_config.NumberColumn("Listed Sites", format="%d"),
        "registry_search_url": st.column_config.LinkColumn(
            "Registry Search", display_text="Search ClinicalTrials.gov"
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
render_provenance_drawer(
    profile_id,
    segments=queue.head(10),
    context={
        "scope": "Queue preview row inputs; sidebar scope before segment filters.",
        "derived_rules": PRIORITY_DERIVED_RULES,
    },
    key="overview_queue",
    label="How this table is computed: Top registry segments",
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

guarded_footer()
