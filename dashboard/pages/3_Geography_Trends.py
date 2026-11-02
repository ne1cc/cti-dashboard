"""Geography Trends — state-level listing activity (choropleth + monthly)."""

import pandas as pd
import plotly.express as px
import streamlit as st
from components import data
from components.audit_panel import render_metric_audit
from components.guardrails import guarded_footer, page_setup
from components.guidance import format_condition_group, render_page_guide
from components.profile import render_profile_selector

page_setup("Geography Trends")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

render_page_guide("geography_trends", profile_id)

trends = data.condition_geography_trends(profile_id)

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
    render_metric_audit(profile_id, metric="latest_study", key="geography")
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

view_mode = st.radio(
    "Listing metric",
    ["Per 100k residents", "Raw count"] if population_ready else ["Raw count"],
    index=0,
    horizontal=True,
    help="Toggle between population-normalized listings per 100k residents and raw trial counts.",
)

unknown_rates = int(latest["recruiting_trials_per_100k"].isna().sum())
unknown_rate_note = (
    f" {unknown_rates} state(s)/territory(ies) have unknown population rates "
    "and may be uncolored on the rate map. The table shows only the top 20 rows "
    "for the selected measure."
    if population_ready and unknown_rates > 0
    else ""
)

if view_mode == "Per 100k residents":
    st.subheader(
        f"Recruiting listings per 100k residents by state — "
        f"{format_condition_group(condition, profile_id)}"
    )
    fig = px.choropleth(
        latest,
        locations="state_normalized",
        locationmode="USA-states",
        color="recruiting_trials_per_100k",
        scope="usa",
        color_continuous_scale="Blues",
        labels={
            "recruiting_trials_per_100k": "Recruiting listings per 100k",
            "recruiting_trial_count": "Recruiting listings",
            "state_normalized": "State",
        },
        hover_data={
            "state_normalized": True,
            "recruiting_trials_per_100k": ":.2f",
            "recruiting_trial_count": True,
        },
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        f"Month shown: {latest_month}. Showing monthly recruiting registry listings "
        "normalized by total state resident population (U.S. Census Bureau Vintage 2024, "
        "July 1, 2024 estimates). Neither population-adjusted rates nor raw counts "
        f"measure patient access or clinical need.{unknown_rate_note}"
    )
else:
    st.subheader(f"Recruiting listings by state — {format_condition_group(condition, profile_id)}")
    fig = px.choropleth(
        latest,
        locations="state_normalized",
        locationmode="USA-states",
        color="recruiting_trial_count",
        scope="usa",
        color_continuous_scale="Blues",
        labels={
            "recruiting_trial_count": "Recruiting listings",
            "recruiting_trials_per_100k": "Recruiting listings per 100k",
            "state_normalized": "State",
        },
        hover_data={
            "state_normalized": True,
            "recruiting_trial_count": True,
            "recruiting_trials_per_100k": ":.2f",
        },
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        f"Month shown: {latest_month}. Counts are trial listings with at least one "
        "usable U.S. site in that state. Neither raw listing counts nor population-adjusted "
        f"rates measure patient access or clinical need.{unknown_rate_note}"
    )

st.subheader("Top states")
# Growth renders as text so an uncovered window can say "not enough history"
# instead of a blank that reads as zero; covered windows keep a signed percent.
sort_col = (
    "recruiting_trials_per_100k" if view_mode == "Per 100k residents" else "recruiting_trial_count"
)
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
    )
    st.plotly_chart(fig2, width="stretch")
else:
    st.info(
        "Trend lines need multiple monthly snapshots; this project has "
        f"accrued {months} snapshot month so far. Re-run `make pipeline` "
        "over time to build the series."
    )

render_metric_audit(
    profile_id,
    filters={"conditions": [condition] if condition else []},
    metric="history",
    months=scoped["activity_month"],
    states=scoped["state_normalized"].tolist(),
    context={
        "growth_window": (
            "Monthly counts are distinct NCT IDs across daily observations per "
            "profile/condition/state/month; recruiting requires RECRUITING on an "
            "observation. Sponsor count is distinct normalized lead sponsors. "
            "Newly registered (first posted) requires first-post month equal to "
            "snapshot month: registrations, not transitions. "
            "Growth = (current monthly recruiting count - earliest recruiting count "
            "in the inclusive preceding three-calendar-month range)/earliest count; "
            "null with zero baseline or until the window's earliest observed month "
            "sits at least three months back — 'not enough history', never zero. "
            "State sums may repeat studies. This panel audits window counts; deltas "
            "remain warehouse calculations."
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
    },
    key="geography",
)

guarded_footer()
