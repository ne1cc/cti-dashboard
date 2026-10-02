"""Overview — Clinical Trial Access & Recruitment Competition Intelligence."""

import streamlit as st
from components import data
from components.audit_panel import render_metric_audit
from components.guardrails import guarded_footer, page_setup, proxy_caption
from components.guidance import (
    format_condition_group,
    render_indication_banner,
    render_page_guide,
)
from components.profile import render_profile_selector

page_setup("Recruitment Competition Intelligence — Overview")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_indication_banner(profile_id)
render_page_guide("overview")

metrics = data.overview_metrics(profile_id)

col1, col2, col3, col4 = st.columns(4)
col1.metric(
    "Trials tracked",
    f"{int(metrics['total_trials']):,}",
    help="Total interventional clinical trials with U.S. sites recorded in the warehouse.",
)
col2.metric(
    "Currently recruiting",
    f"{int(metrics['recruiting_trials']):,}",
    help="Active trials with overall status RECRUITING in the latest snapshot.",
)
col3.metric(
    "States with listed sites",
    int(metrics["states_with_sites"]),
    help="Distinct U.S. states and territories containing at least one listed trial facility.",
)
col4.metric(
    "Listed facilities",
    f"{int(metrics['listed_facilities']):,}",
    help=(
        "Total facility listings across all trial site records "
        "(best-effort facility normalization)."
    ),
)

runs_count = metrics.get("snapshot_count", metrics.get("warehouse_runs", 0))
st.caption(
    f"Latest snapshot: {metrics['latest_snapshot']} · "
    f"snapshots accrued: {int(runs_count) if runs_count is not None else 0}"
)
proxy_caption()

st.subheader("Top of the Feasibility Review Priority Queue")
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

st.dataframe(
    preview_df,
    hide_index=True,
    width="stretch",
    column_config={
        "priority_rank": st.column_config.NumberColumn("Rank", format="%d"),
        "condition_group": st.column_config.TextColumn("Condition Group"),
        "state_normalized": st.column_config.TextColumn("State"),
        "phase_normalized": st.column_config.TextColumn("Phase"),
        "feasibility_review_priority_score": st.column_config.NumberColumn(
            "Priority Score", format="%.3f"
        ),
        "priority_band": st.column_config.TextColumn("Band"),
        "recruiting_trial_count": st.column_config.NumberColumn("Recruiting Trials", format="%d"),
        "priority_explanation": st.column_config.TextColumn("Explanation"),
    },
)
st.page_link(
    "pages/1_Priority_Queue.py",
    label="Open the full Priority Queue",
    icon=":material/arrow_forward:",
)

st.subheader("How to Use This Dashboard to Eliminate Guesswork")
st.markdown(
    """
    This platform translates raw public registry data into structured operational intelligence
    for **Clinical Trial Feasibility Leads**, **Study Planners**, and **Medical Directors**:

    1. **Prioritize Feasibility Review (Pages 1 & 2):** Use the **Priority Queue** and
       **Competition Landscape** to rank condition × state × phase segments. Identify where trial
       counts or rapid growth demands differentiated protocol strategy.
    2. **Mitigate Site Congestion (Pages 3 & 4):** Review **Geography Trends** and **Site Overlap**
       to identify institutions carrying multiple active protocols and assess investigator
       bandwidth before outreach.
    3. **Benchmark Competitors & Design (Pages 5, 7, & 8):** Evaluate lead sponsors, inspect
       individual registry records in **Trial Explorer**, and use **Trial Similarity** to benchmark
       protocols sharing identical phases, masking, and biomarker gating.
    4. **Model Scenario Exposure (Page 6):** Stress-test feasibility timelines in
       **Data Reliability** to quantify the financial cost of enrollment delays ($/month burn rate)
       without arbitrary guesswork.
    """
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
)
render_metric_audit(
    profile_id,
    segments=queue.head(10),
    context={"scope": "Queue preview row inputs; sidebar scope before derived rank filters."},
    key="overview_queue",
)

guarded_footer()
