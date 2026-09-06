"""Geography Trends — state-level listing activity (choropleth + monthly)."""

import plotly.express as px
import streamlit as st
from components import data
from components.guardrails import guarded_footer, page_setup
from components.profile import render_profile_selector

page_setup("Geography Trends")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

trends = data.condition_geography_trends(profile_id)

condition_options = sorted(trends["condition_group"].dropna().unique())
default_index = (
    condition_options.index("alzheimers_disease")
    if "alzheimers_disease" in condition_options
    else 0
)
condition = st.sidebar.selectbox("Condition group", condition_options, index=default_index)
scoped = trends[trends["condition_group"] == condition]

latest_month = scoped["activity_month"].max()
latest = scoped[scoped["activity_month"] == latest_month]

st.subheader(f"Recruiting listings by state — {condition}")
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

guarded_footer()
