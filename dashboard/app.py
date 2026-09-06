"""Overview — Clinical Trial Access & Recruitment Competition Intelligence."""

import streamlit as st
from components import data
from components.guardrails import guarded_footer, page_setup, proxy_caption
from components.profile import render_profile_selector

page_setup("Recruitment Competition Intelligence — Overview")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

metrics = data.overview_metrics(profile_id)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Trials tracked", f"{int(metrics['total_trials']):,}")
col2.metric("Currently recruiting", f"{int(metrics['recruiting_trials']):,}")
col3.metric("States with listed sites", int(metrics["states_with_sites"]))
col4.metric("Listed facilities", f"{int(metrics['listed_facilities']):,}")

st.caption(
    f"Latest snapshot: {metrics['latest_snapshot']} · "
    f"snapshots accrued: {int(metrics['snapshot_count'])}"
)
proxy_caption()

st.subheader("Top of the Feasibility Review Priority Queue")
queue = data.priority_queue(profile_id)
st.dataframe(
    queue.head(10)[
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
    ],
    hide_index=True,
    width="stretch",
)
st.page_link(
    "pages/1_Priority_Queue.py",
    label="Open the full Priority Queue",
    icon=":material/arrow_forward:",
)

st.subheader("How to read this dashboard")
st.markdown(
    """
- **Priority Queue** ranks condition x state x phase segments for *human
  feasibility review* using weighted, normalized registry signals.
- **Competition Landscape** shows recruiting density, sponsor
  concentration, and growth signals per segment.
- **Geography Trends** tracks monthly listing activity by state.
- **Site Overlap** flags facilities listed by multiple recruiting trials
  (best-effort facility matching).
- **Sponsor Landscape** summarizes lead sponsors of recruiting trials.
- **Data Reliability** exposes run-level reconciliation and the
  assumption-driven scenario explorer.
    """
)

guarded_footer()
