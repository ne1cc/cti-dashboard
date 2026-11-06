"""Participation vs Burden — share of reported participants against share of disease burden."""

import plotly.express as px
import streamlit as st
from components import data
from components.guardrails import guarded_footer, page_setup
from components.participation_prevalence import ratio_view
from components.profile import render_profile_selector

page_setup("Participation vs Burden")
profile_id = render_profile_selector()
data.require_warehouse(profile_id)

rows = data.participation_prevalence(profile_id)
if rows.empty:
    st.info(
        "No participation rows yet. They need completed trials with posted baseline "
        "characteristics; rebuild the marts (`make pipeline`) once such trials are loaded."
    )
    guarded_footer()
    st.stop()

condition = st.sidebar.selectbox(
    "Condition (MeSH)", sorted(rows["condition_label"].dropna().unique())
)
rows = rows[rows["condition_label"] == condition]
window = st.sidebar.selectbox(
    "Completion window", sorted(rows["time_window"].unique(), reverse=True)
)
rows = rows[rows["time_window"] == window]
dimension = st.sidebar.selectbox("Group by", sorted(rows["dimension"].unique()))
rows = rows[rows["dimension"] == dimension].sort_values("group_label")

table, caption = ratio_view(rows)
has_ratio = table["Ratio"].notna().any()
st.subheader(f"Participation vs disease burden by {dimension} — {condition}, {window}")
st.caption(caption)
st.caption(
    "Describes a difference between reported participation and burden. It says nothing "
    "about intent, quotas, causation or sponsor quality. Only trials with posted baseline "
    "results are counted, and only those reporting this variable."
)

column = "Ratio" if has_ratio else "Participation share"
shown = table[table[column].notna()]
if shown.empty:
    st.info("All groups in this selection are suppressed (fewer than 3 reporting trials).")
else:
    basis = "participation share / burden share" if has_ratio else "composition only"
    fig = px.bar(shown, x="Group", y=column, title=f"{column} by {dimension} ({basis})")
    st.plotly_chart(fig, width="stretch")
st.dataframe(table, hide_index=True, width="content")
st.caption(f"Table denominator: {caption}")
guarded_footer()
