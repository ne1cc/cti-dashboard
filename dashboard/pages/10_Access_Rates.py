"""Access Rates — recruiting trials per resident and per estimated case."""

import plotly.express as px
import streamlit as st
from components import data
from components.access_rates import NO_BURDEN, PER_1K_CASES, rate_view
from components.guardrails import guarded_footer, page_setup
from components.profile import render_profile_selector

page_setup("Access Rates")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

rates = data.access_rates(profile_id)
if rates.empty:
    st.info("No access-rate rows yet. Rebuild the marts (`make pipeline`) once trials are loaded.")
    guarded_footer()
    st.stop()

labels = sorted(rates["condition_label"].dropna().unique())
condition = st.sidebar.selectbox("Condition (MeSH)", labels)
per_case = (
    st.sidebar.radio(
        "Denominator",
        ["Per 100k residents", "Per 1,000 estimated cases"],
        help=f"Estimated-case rates need a burden source; otherwise: {NO_BURDEN}.",
    )
    == "Per 1,000 estimated cases"
)

shown, column, caption = rate_view(rates[rates["condition_label"] == condition], per_case)
title_unit = "per 1,000 estimated cases" if column == PER_1K_CASES else "per 100,000 residents"

st.subheader(f"Recruiting trials {title_unit} — {condition}")
st.caption(caption)
top = shown.sort_values(column, ascending=False).head(20)
fig = px.bar(
    top,
    x="state_code",
    y=column,
    labels={"state_code": "State", column: f"Recruiting trials {title_unit}"},
    title=f"Recruiting trials {title_unit}, by state",
)
st.plotly_chart(fig, width="stretch")

st.dataframe(
    top[
        [
            "state_code",
            "recruiting_trial_count",
            column,
            "population",
            "estimated_cases",
            "denominator_label",
        ]
    ],
    hide_index=True,
    width="content",
)
st.caption(f"Table denominator: {caption}")
guarded_footer()
