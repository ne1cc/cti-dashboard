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

render_page_guide("geography_trends")

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

st.subheader(f"Recruiting listings by state — {format_condition_group(condition)}")
fig = px.choropleth(
    latest,
    locations="state_normalized",
    locationmode="USA-states",
    color="recruiting_trial_count",
    scope="usa",
    color_continuous_scale="Blues",
    labels={"recruiting_trial_count": "Recruiting listings"},
)
st.plotly_chart(fig, width="stretch")
st.caption(
    f"Month shown: {latest_month}. Counts are trial listings with at "
    "least one usable U.S. site in that state — not patient availability."
)

st.subheader("Top states")
st.dataframe(
    latest.sort_values("recruiting_trial_count", ascending=False)[
        [
            "state_normalized",
            "trial_count",
            "recruiting_trial_count",
            "sponsor_count",
            "newly_posted_in_month_proxy",
            "recruiting_growth_3m",
        ]
    ].head(20),
    hide_index=True,
    width="stretch",
    column_config={
        "state_normalized": st.column_config.TextColumn("State"),
        "trial_count": st.column_config.NumberColumn("Total Trials", format="%d"),
        "recruiting_trial_count": st.column_config.NumberColumn("Recruiting Trials", format="%d"),
        "sponsor_count": st.column_config.NumberColumn("Sponsors", format="%d"),
        "newly_posted_in_month_proxy": st.column_config.NumberColumn(
            "Newly Posted (Month Proxy)", format="%d"
        ),
        "recruiting_growth_3m": st.column_config.NumberColumn(
            "3-Month Growth",
            format="%+.1%",
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
        )
    },
    key="geography",
)

guarded_footer()
