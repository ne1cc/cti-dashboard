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

view_mode = st.sidebar.radio(
    "View mode",
    ["Per 100k residents", "Raw count"],
    index=0,
    help=(
        "Toggle between listings normalized per 100,000 residents "
        "(Census Vintage 2024) and unadjusted counts."
    ),
)
is_per_100k = view_mode == "Per 100k residents"

scoped = trends[trends["condition_group"] == condition]

latest_month = scoped["activity_month"].max()
latest = scoped[scoped["activity_month"] == latest_month]

if is_per_100k:
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
            "recruiting_trials_per_100k": "Listings per 100k",
            "state_normalized": "State",
            "recruiting_trial_count": "Recruiting listings",
            "population": "Population",
        },
        hover_data={
            "state_normalized": False,
            "recruiting_trials_per_100k": ":.2f",
            "recruiting_trial_count": True,
            "population": ":,",
        },
    )
else:
    st.subheader(f"Recruiting listings by state (raw count) — {format_condition_group(condition)}")
    fig = px.choropleth(
        latest,
        locations="state_normalized",
        locationmode="USA-states",
        color="recruiting_trial_count",
        scope="usa",
        color_continuous_scale="Blues",
        labels={
            "recruiting_trial_count": "Recruiting listings",
            "state_normalized": "State",
            "recruiting_trials_per_100k": "Listings per 100k",
            "population": "Population",
        },
        hover_data={
            "state_normalized": False,
            "recruiting_trial_count": True,
            "recruiting_trials_per_100k": ":.2f",
            "population": ":,",
        },
    )

st.plotly_chart(fig, width="stretch")

unknown_rate_states = (
    latest[latest["recruiting_trials_per_100k"].isna()]["state_normalized"]
    .dropna()
    .unique()
    .tolist()
)
unknown_count = len(unknown_rate_states)
joined_unknown = ", ".join(sorted(unknown_rate_states))
unknown_note = (
    f" ({unknown_count} state/territory with unknown denominator: {joined_unknown})"
    if unknown_count > 0
    else ""
)

if is_per_100k:
    st.caption(
        f"Month shown: {latest_month}. Metrics are monthly recruiting registry listings "
        "normalized by resident population (per 100,000 residents) using U.S. Census Bureau "
        "Vintage 2024 estimates (July 1, 2024 / POPESTIMATE2024), neither patient access nor "
        f"clinical need.{unknown_note}"
    )
else:
    st.caption(
        f"Month shown: {latest_month}. Counts are raw trial listings with at least one usable "
        f"U.S. site in that state — not patient availability or clinical need.{unknown_note}"
    )

st.subheader("Top states")

sort_cols = (
    ["recruiting_trials_per_100k", "recruiting_trial_count"]
    if is_per_100k
    else ["recruiting_trial_count", "recruiting_trials_per_100k"]
)

table_df = (
    latest.sort_values(sort_cols, ascending=[False, False], na_position="last")[
        [
            "state_normalized",
            "recruiting_trials_per_100k",
            "recruiting_trial_count",
            "population",
            "trial_count",
            "sponsor_count",
            "newly_posted_in_month_proxy",
            "recruiting_growth_3m",
        ]
    ]
    .head(20)
    .assign(recruiting_growth_3m=lambda frame: frame.recruiting_growth_3m * 100)
)

st.dataframe(
    table_df,
    hide_index=True,
    width="content",
    column_config={
        "state_normalized": st.column_config.TextColumn("State"),
        "recruiting_trials_per_100k": st.column_config.NumberColumn(
            "Per 100k Residents",
            format="%.2f",
            help="Recruiting trial listings per 100,000 residents (Census Vintage 2024)",
        ),
        "recruiting_trial_count": st.column_config.NumberColumn("Recruiting Trials", format="%d"),
        "population": st.column_config.NumberColumn(
            "Population",
            format="%d",
            help="Census resident population estimate (Vintage 2024, July 1, 2024)",
        ),
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
    st.subheader("Monthly trend (recruiting listings)")
    fig2 = px.line(
        scoped.groupby("activity_month", as_index=False)["recruiting_trial_count"].sum(),
        x="activity_month",
        y="recruiting_trial_count",
        markers=True,
        labels={"recruiting_trial_count": "Recruiting listings", "activity_month": "Month"},
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
            "studies. Population normalization divides recruiting listings by U.S. Census Bureau "
            "Vintage 2024 resident population estimates (as of July 1, 2024, POPESTIMATE2024) "
            "per 100k residents; metrics reflect recruiting registry listings normalized by "
            "total resident population, neither patient access nor clinical need. "
            "This panel audits window counts; deltas remain warehouse calculations."
        )
    },
    key="geography",
)

guarded_footer()
