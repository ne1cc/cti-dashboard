"""Reusable sidebar filters over segment dataframes."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components.guidance import format_condition_group


def segment_filters(
    frame: pd.DataFrame,
    condition_col: str = "condition_group",
    state_col: str = "state_normalized",
    phase_col: str = "phase_normalized",
) -> pd.DataFrame:
    """Render Streamlit sidebar multiselect filters over segment DataFrame columns.

    Dynamically inspects the input DataFrame for the specified condition group,
    state, and phase columns, rendering sidebar widgets for columns present and
    filtering the returned DataFrame according to user selections.

    Args:
        frame: Input DataFrame containing trial or site segment data.
        condition_col: Column name representing condition grouping. Defaults to
            "condition_group".
        state_col: Column name representing normalized state codes. Defaults to
            "state_normalized".
        phase_col: Column name representing normalized trial phases. Defaults to
            "phase_normalized".

    Returns:
        pd.DataFrame: Filtered subset of the input DataFrame matching all active
            sidebar filter selections.
    """
    st.sidebar.header("Filters")
    filtered = frame.copy()
    selections = {}

    if condition_col in frame.columns:
        options = sorted(frame[condition_col].dropna().unique())
        selected = st.sidebar.multiselect(
            "Condition group",
            options,
            format_func=format_condition_group,
            help="Filter by deterministically mapped ADRD diagnostic categories.",
        )
        selections["conditions"] = list(selected)
        if selected:
            filtered = filtered[filtered[condition_col].isin(selected)]

    if state_col in frame.columns:
        options = sorted(frame[state_col].dropna().unique())
        selected = st.sidebar.multiselect("State", options)
        selections["states"] = list(selected)
        if selected:
            filtered = filtered[filtered[state_col].isin(selected)]

    if phase_col in frame.columns:
        options = sorted(frame[phase_col].dropna().unique())
        selected = st.sidebar.multiselect("Phase", options)
        selections["phases"] = list(selected)
        if selected:
            filtered = filtered[filtered[phase_col].isin(selected)]

    st.sidebar.caption(f"{len(filtered):,} of {len(frame):,} rows shown")
    filtered.attrs["audit_filters"] = selections
    return filtered
