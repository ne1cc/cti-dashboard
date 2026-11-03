"""Competition Landscape — recruiting trial counts and sponsor concentration."""

import pandas as pd
import plotly.express as px
import streamlit as st
from components import data
from components.audit_panel import render_metric_audit
from components.filters import segment_filters
from components.formatting import format_phase, hhi_status
from components.guardrails import guarded_footer, page_setup, proxy_caption
from components.guidance import format_condition_group, render_page_guide
from components.profile import render_profile_selector

page_setup("Competition Landscape")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_page_guide("competition_landscape", profile_id)

competition = data.recruiting_competition(profile_id)
filtered = segment_filters(competition, profile_id=profile_id)

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

observed_history_days = (
    int(competition["observed_history_days"].max()) if not competition.empty else 0
)
growth_window_covered = observed_history_days >= 90

st.subheader("Recruiting trial count vs sponsor concentration")
hhi_chart_data = filtered[filtered["sponsor_hhi"].notna()]
fig = px.scatter(
    hhi_chart_data,
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
    f"Excluded {len(filtered) - len(hhi_chart_data)} segments with insufficient sponsor data."
)
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
        "observed_history_days",
        "new_recruiting_90d",
        "newly_registered_90d",
        "sponsor_count",
        "top_sponsor_share",
        "sponsor_hhi",
        "competition_signal_band",
    ]
].copy()
table_df["condition_group"] = table_df["condition_group"].apply(
    lambda g: format_condition_group(g, profile_id)
)
table_df["phase_normalized"] = table_df["phase_normalized"].map(format_phase)
table_df.insert(
    list(table_df.columns).index("sponsor_hhi") + 1,
    "hhi_status",
    [
        hhi_status(value, count)
        for value, count in zip(table_df["sponsor_hhi"], table_df["sponsor_count"], strict=True)
    ],
)
table_df["competition_signal_band"] = table_df["competition_signal_band"].str.capitalize()
# The "(90d)" label is only honest once 90 days were actually observed and covered;
# below that or across unobserved gaps the column carries "not enough history".
has_covered_rows = (
    bool(filtered["new_recruiting_90d"].notna().any()) if not filtered.empty else False
)
growth_window_covered = observed_history_days >= 90 and has_covered_rows


def _format_competition_new_recruiting(value: float | None) -> str:
    if pd.notna(value):
        return f"{value:.0f}"
    if observed_history_days >= 90:
        return "not enough history (gap between snapshots exceeds window)"
    return f"not enough history ({observed_history_days} of 90 days observed)"


table_df["new_recruiting_90d"] = table_df["new_recruiting_90d"].apply(
    _format_competition_new_recruiting
)
if growth_window_covered:
    growth_column_label = "New Recruiting (90d)"
else:
    growth_column_label = "New Recruiting (observed history too short)"

st.dataframe(
    table_df,
    hide_index=True,
    width="content",
    column_config={
        "condition_group": st.column_config.TextColumn("Condition Group"),
        "state_normalized": st.column_config.TextColumn("State"),
        "phase_normalized": st.column_config.TextColumn("Phase"),
        "recruiting_trial_count": st.column_config.NumberColumn("Recruiting Listings", format="%d"),
        "listed_site_count": st.column_config.NumberColumn("Listed Sites", format="%d"),
        "observed_history_days": st.column_config.NumberColumn(
            "Observed History (days)",
            format="%d",
            help="Snapshot history accrued for this profile in days.",
        ),
        "new_recruiting_90d": st.column_config.TextColumn(
            growth_column_label,
            help="Trials observed transitioning into recruiting within 90 days; "
            "null until 90 days of snapshot history are observed.",
        ),
        "newly_registered_90d": st.column_config.NumberColumn(
            "Newly registered (first posted)",
            format="%d",
            help="Recruiting studies whose registry first-post date falls within "
            "90 days — a registration count, not a recruiting transition.",
        ),
        "sponsor_count": st.column_config.NumberColumn("Sponsors", format="%d"),
        "top_sponsor_share": st.column_config.NumberColumn("Top Sponsor Share", format="percent"),
        "hhi_status": st.column_config.TextColumn("HHI status"),
        "sponsor_hhi": st.column_config.NumberColumn(
            "Sponsor concentration",
            format="%.3f",
            help="Herfindahl-Hirschman Index (0–1); higher means more concentrated",
        ),
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
            "not RECRUITING; it is null until observed history covers 90 days and "
            "the latest snapshot gap does not itself span the window (a transition "
            "across a longer gap has unobserved timing inside it), so null means "
            f"not enough history, never zero. Observed history here: "
            f"{observed_history_days} days. Newly registered (first posted) counts "
            "recruiting studies whose registry first-post date is within 90 days of "
            "the snapshot — registrations, not transitions. Counts may repeat across "
            "segments. "
            "This panel audits count inputs; derived values remain warehouse calculations."
        )
    },
    key="competition",
)

guarded_footer()
