"""Geography Trends — state-level listing activity (choropleth + monthly)."""

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

condition_options = sorted(trends["condition_group"].dropna().unique())
default_index = (
    condition_options.index("alzheimers_disease")
    if "alzheimers_disease" in condition_options
    else 0
)
condition = st.sidebar.selectbox(
    "Condition group",
    condition_options,
    index=default_index,
    format_func=format_condition_group,
    help="Select diagnostic category to view state-level distribution and monthly trends.",
)
scoped = trends[trends["condition_group"] == condition]

latest_month = scoped["activity_month"].max()
latest = scoped[scoped["activity_month"] == latest_month]

view_mode = st.radio(
    "Listing metric",
    ["Per 100k residents", "Raw count"],
    index=0,
    horizontal=True,
    help="Toggle between population-normalized listings per 100k residents and raw trial counts.",
)

unknown_rates = int(latest["recruiting_trials_per_100k"].isna().sum())
unknown_rate_note = (
    f" {unknown_rates} state(s)/territory(ies) with unknown population rates "
    "remain listed in the table and raw view even if uncolored on the rate map."
    if unknown_rates > 0
    else ""
)

if view_mode == "Per 100k residents":
    st.subheader(
        f"Recruiting listings per 100k residents by state — {format_condition_group(condition)}"
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
    st.subheader(f"Recruiting listings by state — {format_condition_group(condition)}")
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
sort_col = (
    "recruiting_trials_per_100k" if view_mode == "Per 100k residents" else "recruiting_trial_count"
)
st.dataframe(
    latest.sort_values(sort_col, ascending=False)[
        [
            "state_normalized",
            "recruiting_trials_per_100k",
            "recruiting_trial_count",
            "trial_count",
            "sponsor_count",
            "newly_posted_in_month_proxy",
            "recruiting_growth_3m",
        ]
    ]
    .head(20)
    .assign(recruiting_growth_3m=lambda frame: frame.recruiting_growth_3m * 100),
    hide_index=True,
    width="content",
    column_config={
        "state_normalized": st.column_config.TextColumn("State"),
        "recruiting_trials_per_100k": st.column_config.NumberColumn(
            "Per 100k Residents",
            format="%.2f",
            help="Monthly recruiting trial listings per 100,000 residents (Census Vintage 2024)",
        ),
        "recruiting_trial_count": st.column_config.NumberColumn("Recruiting Trials", format="%d"),
        "trial_count": st.column_config.NumberColumn("Total Trials", format="%d"),
        "sponsor_count": st.column_config.NumberColumn("Sponsors", format="%d"),
        "newly_posted_in_month_proxy": st.column_config.NumberColumn(
            "Newly Posted (Month Proxy)", format="%d"
        ),
        "recruiting_growth_3m": st.column_config.NumberColumn(
            "3-Month Growth",
            format="%+.1f%%",
            help="Relative change from the earliest recruiting count in the three-month window",
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
            "Posting proxy requires first-post month equal to snapshot month. "
            "Growth = (current monthly recruiting count - earliest recruiting count "
            "in the inclusive preceding three-calendar-month range)/earliest count; "
            "null with fewer than two months or zero baseline. State sums may repeat "
            "studies. This panel audits window counts; deltas remain warehouse calculations."
        ),
        "population_normalization": (
            "Per-100k rates divide recruiting trial listings by U.S. Census resident "
            "population estimates (Vintage 2024, July 1, 2024 estimates from "
            "NST-EST2024-ALLDATA.csv SUMLEV 040) multiplied by 100,000. States or "
            "territories without Vintage 2024 population estimates have null rates. "
            "Neither raw counts nor population-normalized rates measure patient access "
            "or clinical need."
        ),
    },
    key="geography",
)

guarded_footer()
