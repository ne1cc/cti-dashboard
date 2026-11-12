"""Geography Trends — county access map, state listing table and monthly trend."""

import pandas as pd
import plotly.express as px
import streamlit as st
from components import data
from components.audit_panel import render_provenance_drawer
from components.county_access import render_county_access
from components.guardrails import guarded_footer, page_setup
from components.guidance import format_condition_group, render_page_guide
from components.profile import render_preset_selector

page_setup("Geography Trends")
preset = render_preset_selector()
profile_id = preset.preset_id
data.require_warehouse(preset)

render_page_guide("geography_trends", profile_id)

trends = data.condition_geography_trends(preset)

# 1. Keep retained warehouses usable without inventing population-adjusted rates.
population_ready = "recruiting_trials_per_100k" in trends.columns
if not population_ready:
    trends = trends.assign(recruiting_trials_per_100k=float("nan"))
    st.warning(
        "Saved data predates population normalization. Showing raw counts; "
        "rebuild the warehouse to enable per-100k rates."
    )

condition_options = sorted(trends["condition_group"].dropna().unique())
if not condition_options:
    st.info("No condition group records available for this profile.")
    render_provenance_drawer(preset, metric="latest_study", key="geography")
    guarded_footer()
    st.stop()

condition = st.sidebar.selectbox(
    "Condition group",
    condition_options,
    index=0,
    format_func=lambda g: format_condition_group(g, profile_id),
    help="Select diagnostic category to view state-level distribution and monthly trends.",
)
scoped = trends[trends["condition_group"] == condition]

latest_month = scoped["activity_month"].max()
latest = scoped[scoped["activity_month"] == latest_month]
latest_month_text = f" in {latest_month}" if pd.notna(latest_month) else ""

geography_context = {
    "displayed_measure": (
        f"Recruiting study–jurisdiction memberships{latest_month_text} across reporting "
        "states and territories. A multi-jurisdiction study is counted once per jurisdiction "
        "per month, so summing across jurisdictions counts memberships and can exceed "
        "unique national studies. Does not represent a national study census or confirmed "
        "present-day local recruiting activity. The audit panel inspects underlying count inputs "
        "and historical snapshot observations; its distinct contributor union does not "
        "recalculate or replace the summed membership total."
    ),
    "growth_window": (
        "Monthly counts are distinct NCT IDs across daily observations per "
        "profile/condition/state/month; recruiting requires RECRUITING on an "
        "observation. Sponsor count is distinct normalized lead sponsors. "
        "Newly registered (first posted) requires first-post month equal to "
        "snapshot month: registrations, not transitions. "
        "Growth = (current monthly recruiting count - earliest recruiting count "
        "in the inclusive preceding three-calendar-month range)/earliest count; "
        "null with zero baseline or until the window's earliest observed month "
        "sits at least three months back, the baseline snapshot sits at least 3 "
        "calendar months back, and observed history spans at least 90 days — "
        "'not enough history', never zero. State sums count study–jurisdiction memberships "
        "and may repeat multi-state studies. "
        "This panel audits window counts; deltas remain warehouse calculations."
    ),
    "population_normalization": (
        "Per-100k rates divide recruiting trial listings by U.S. Census resident "
        "population estimates (Vintage 2024, July 1, 2024 estimates from "
        "NST-EST2024-ALLDATA.csv SUMLEV 040) multiplied by 100,000. States or "
        "territories without Vintage 2024 population estimates have null rates. "
        "Neither raw counts nor population-normalized rates measure patient access "
        "or clinical need."
        if population_ready
        else "Population-adjusted rates are unavailable in this retained warehouse. "
        "The page shows raw counts until a rebuild adds the population denominators."
    ),
}

col1, col2 = st.columns(2)
with col1:
    st.metric(
        "States with listings",
        int((latest["recruiting_trial_count"] > 0).sum()) if not latest.empty else 0,
        help="States and territories with at least one actively recruiting study listed.",
    )
    render_provenance_drawer(
        preset,
        metric="latest_study",
        filters={"conditions": [condition] if condition else []},
        key="geo_states",
        label="How this number is computed: States with listings",
        context={"displayed_measure": "States and territories with active recruiting listings."},
    )
with col2:
    st.metric(
        "Recruiting study–jurisdiction memberships",
        int(latest["recruiting_trial_count"].sum()) if not latest.empty else 0,
        help=(
            "Actively recruiting study–jurisdiction memberships across states and territories"
            f"{latest_month_text}. A study is counted once per jurisdiction and month and "
            "can appear in multiple jurisdictions; this sums jurisdiction memberships rather "
            "than unique national studies, and does not confirm present-day site "
            "recruiting activity."
        ),
    )
    render_provenance_drawer(
        preset,
        filters={"conditions": [condition] if condition else []},
        metric="history",
        months=scoped["activity_month"],
        states=scoped["state_normalized"].tolist(),
        context=geography_context,
        key="geography",
        label="How this number is computed: Recruiting study–jurisdiction memberships",
    )

# The county map replaces the interim state-level per-100k / raw-count toggle (#51): it
# shows the question that toggle stood in for (distance to an open site), per county.
render_county_access(preset)

unknown_rates = int(latest["recruiting_trials_per_100k"].isna().sum())
unknown_rate_note = (
    f" {unknown_rates} state(s)/territory(ies) have unknown population rates. "
    "The table shows only the top 20 rows for the selected measure."
    if population_ready and unknown_rates > 0
    else ""
)

st.subheader("Top states")
# Growth renders as text so an uncovered window can say "not enough history"
# instead of a blank that reads as zero; covered windows keep a signed percent.
sort_col = "recruiting_trials_per_100k" if population_ready else "recruiting_trial_count"
top_rows = latest.sort_values(sort_col, ascending=False).head(20).copy()


def _format_growth_3m(row: pd.Series) -> str:
    val = row["recruiting_growth_3m"]
    if pd.notna(val):
        return f"{val * 100:+.1f}%"
    history = row.get("observed_history_days", 0)
    baseline = row.get("recruiting_count_3m_baseline")
    if pd.notna(baseline) and baseline == 0 and history >= 90:
        return "zero baseline"
    return "not enough history (window spans under 3 months)"


top_rows["recruiting_growth_3m"] = top_rows.apply(_format_growth_3m, axis=1)

latest_table = top_rows[
    [
        "state_normalized",
        "recruiting_trials_per_100k",
        "recruiting_trial_count",
        "trial_count",
        "sponsor_count",
        "newly_registered_in_month",
        "observed_history_days",
        "recruiting_growth_3m",
    ]
]
st.caption(
    f"Month shown: {latest_month}. Listings are normalized by total state resident population "
    "(U.S. Census Bureau Vintage 2024, July 1, 2024 estimates) in the per-100k column. "
    "Neither population-adjusted rates nor raw counts measure patient access or clinical "
    f"need.{unknown_rate_note}"
)
st.dataframe(
    latest_table,
    hide_index=True,
    width="content",
    column_config={
        "state_normalized": st.column_config.TextColumn("State"),
        "recruiting_trials_per_100k": st.column_config.NumberColumn(
            "Per 100k Residents",
            format="%.2f",
            help="Monthly recruiting trial listings per 100,000 residents (Census Vintage 2024)",
        ),
        "trial_count": st.column_config.NumberColumn("Total Trials", format="%d"),
        "recruiting_trial_count": st.column_config.NumberColumn("Recruiting Trials", format="%d"),
        "sponsor_count": st.column_config.NumberColumn("Sponsors", format="%d"),
        "newly_registered_in_month": st.column_config.NumberColumn(
            "Newly registered (first posted)",
            format="%d",
            help="Trials whose registry first-post month equals the snapshot "
            "month — registrations, not recruiting transitions.",
        ),
        "observed_history_days": st.column_config.NumberColumn(
            "Observed History (days)",
            format="%d",
            help="Snapshot history accrued for this profile up to this month.",
        ),
        "recruiting_growth_3m": st.column_config.TextColumn(
            "3-Month Growth",
            help="Relative change from the earliest recruiting count in the "
            "three-month window; 'not enough history' until that window "
            "actually spans three months.",
        ),
    },
)

months = scoped["activity_month"].nunique()
if months > 1:
    st.subheader("Monthly trend")
    fig2 = px.line(
        scoped.groupby("activity_month", as_index=False)["recruiting_trial_count"].sum(),
        x="activity_month",
        y="recruiting_trial_count",
        markers=True,
        labels={
            "activity_month": "Snapshot Month",
            "recruiting_trial_count": "Study–jurisdiction memberships",
        },
    )
    fig2.update_layout(yaxis_title="Study–jurisdiction memberships")
    fig2.update_traces(
        hovertemplate=(
            "Snapshot Month: %{x}<br>"
            "Study–jurisdiction memberships: %{y}<br>"
            "<extra>Sum of state/territory counts; "
            "multi-jurisdiction studies counted once per jurisdiction</extra>"
        )
    )
    st.plotly_chart(fig2, width="stretch")
else:
    st.info(
        "Trend lines need multiple monthly snapshots; this project has "
        f"accrued {months} snapshot month so far. Re-run `make pipeline` "
        "over time to build the series."
    )

guarded_footer()
