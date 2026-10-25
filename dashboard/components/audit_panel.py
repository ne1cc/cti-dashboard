"""Shared, filter-aware drill-through for displayed registry metrics."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml

from components import data
from components.audit import DISCLAIMER, compute_audit, export_audit
from components.formatting import format_phase
from src.utils.paths import project_root

PRIORITY_DERIVED_RULES = (
    "Raw segment metrics are derived directly from dimensional marts "
    "(fct_trial_snapshot, dim_trial, fct_trial_site). "
    "recruiting_trial_count (standardized alias active_recruiting_trials) counts "
    "actively recruiting interventional trials within the condition, state, and phase segment. "
    "distinct_sponsors (alias sponsor_count) counts unique lead sponsors active in the segment. "
    "sponsor_hhi calculates the Herfindahl-Hirschman Index of lead sponsor concentration, "
    "summing squared sponsor shares of segment trials (bounded [0, 1]). "
    "site_overlap_rate (alias site_overlap_share) measures the share of investigative facilities "
    "hosting multiple competing trials within the segment (bounded [0, 1]). "
    "Growth sums daily recruiting entrants over 90 days, requiring a previous "
    "non-null non-recruiting status; with one captured date, first-post >= "
    "snapshot minus 90 days is the proxy. "
    "data_confidence_share retains operational data completeness (0.5 segment "
    "record-quality-ok share + 0.5 latest successful-run usable-location share; "
    "missing shares coalesce to zero) as a quality signal pending Issue #78. "
    "This panel audits count inputs and underlying study records; derived metric values "
    "remain warehouse calculations."
)


def active_rules(profile_id: str) -> dict[str, Any]:
    """Identify the active configuration; do not claim historical rule identity."""
    root = project_root()
    profile = yaml.safe_load((root / f"config/profiles/{profile_id}.yml").read_text())
    paths = {
        "taxonomy": profile["profile"]["taxonomy"],
        "geography": "config/geography_rules.yml",
    }
    model_root = Path(__file__).resolve().parents[2] / "dbt_clinical_trials"
    model_paths = [
        "dbt_project.yml",
        "models/marts/mart_recruiting_competition.sql",
        "models/marts/mart_feasibility_priority_queue.sql",
        "models/marts/mart_condition_geography_trends.sql",
        "models/marts/mart_site_overlap.sql",
        "models/intermediate/int_sponsor_concentration.sql",
        "models/intermediate/int_trial_status_history.sql",
    ]
    return {
        "derived_model_rules": {
            path: hashlib.sha256((model_root / path).read_bytes()).hexdigest()
            for path in model_paths
        },
        **{
            name: {"path": path, "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest()}
            for name, path in paths.items()
        },
        "metric_rules": {
            "path": "dashboard/components/audit.py",
            "sha256": hashlib.sha256(Path(__file__).with_name("audit.py").read_bytes()).hexdigest(),
        },
        "rule_scope": "Active configuration; historical configuration identity is not recorded.",
    }


def build_display_audit(
    profile_id: str,
    observations: pd.DataFrame,
    filters: dict[str, Any],
    *,
    metric: str = "competition",
    count_status: str = "recruiting",
    context: dict[str, Any] | None = None,
    update_threshold_days: int = 180,
) -> dict[str, Any]:
    ids = observations.snapshot_id.unique().tolist() if not observations.empty else []
    if "predecessor_snapshot_id" in observations.columns:
        ids = sorted(set(ids) | set(observations.predecessor_snapshot_id.dropna()))
    studies = data.audit_studies(profile_id, ids)
    locations = data.audit_locations(profile_id, ids)
    conditions = data.audit_condition_mapping(profile_id)
    for frame in (studies, locations, conditions):
        if frame.attrs.get("audit_unavailable"):
            raise ValueError(frame.attrs["audit_unavailable"])
    filters = dict(filters)
    if (context or {}).get("empty_facility_selection"):
        filters["facility_restriction_applied"] = True
    result = compute_audit(
        studies,
        locations,
        conditions,
        profile_id=profile_id,
        observations=observations,
        filters=filters,
        metric=metric,
        count_status=count_status,
        evaluation_time=pd.Timestamp.now(tz="UTC"),
        update_threshold_days=update_threshold_days,
        window_start=(context or {}).get("growth_window_start"),
        window_end=(context or {}).get("growth_window_end"),
        rule_config={**active_rules(profile_id), "display_context": context or {}},
    )
    if (context or {}).get("observation_selection") == "history":
        result["metadata"]["observation_source"] = (
            "int_trial_status_history: latest study observation per profile/date"
        )
    return result


def render_metric_audit(
    profile_id: str,
    *,
    filters: dict[str, Any] | None = None,
    metric: str = "competition",
    observation_metric: str | None = None,
    segments: pd.DataFrame | None = None,
    sponsors: Sequence[str] | None = None,
    facilities: Sequence[Sequence[Any]] | None = None,
    months: Iterable[Any] | None = None,
    states: Sequence[str] | None = None,
    context: dict[str, Any] | None = None,
    key: str = "metric",
    label: str = "Data coverage and caveats",
) -> None:
    """Audit whole filtered scope or a displayed row, with explicit status/period."""
    with st.expander(label):
        st.caption(
            "Every count, chart and derived signal above can be inspected here. "
            "Choose a row to inspect its inputs; summary counts use distinct NCT IDs "
            "and must not be summed across overlapping segments."
        )
        st.markdown(DISCLAIMER)
        selected_filters = dict(filters or {})
        display_context = dict(context or {})
        display_context["sidebar_filters"] = dict(filters or {})
        if segments is not None:
            rows = segments[
                ["condition_group", "state_normalized", "phase_normalized"]
            ].drop_duplicates()
            options = [tuple(row) for row in rows.itertuples(index=False, name=None)]
            chosen_segment = st.selectbox(
                "Audit segment",
                [None, *options],
                format_func=lambda row: (
                    "Sidebar scope before derived row filters" if row is None else " · ".join(row)
                ),
                key=f"{key}_{profile_id}_segment",
            )
            if chosen_segment:
                selected_filters.update(
                    conditions=[chosen_segment[0]],
                    states=[chosen_segment[1]],
                    phases=[chosen_segment[2]],
                )
            display_context["displayed_segments"] = options
            values = segments
            if chosen_segment:
                values = segments[
                    (segments.condition_group == chosen_segment[0])
                    & (segments.state_normalized == chosen_segment[1])
                    & (segments.phase_normalized == chosen_segment[2])
                ]
            display_context["displayed_metric_values"] = values.rename(
                columns=lambda name: name.replace(
                    "data_confidence", "legacy_operational_completeness"
                )
            ).to_dict("records")
        if sponsors is not None:
            sponsor = st.selectbox(
                "Audit sponsor",
                [None, *sponsors],
                format_func=lambda item: item or "All sponsors",
                key=f"{key}_{profile_id}_sponsor",
            )
            if sponsor:
                selected_filters["sponsors"] = [sponsor]
        if facilities is not None:
            chosen_facility = st.selectbox(
                "Audit facility",
                [None, *facilities],
                format_func=lambda row: (
                    "All shown facilities" if row is None else " · ".join(str(v or "") for v in row)
                ),
                key=f"{key}_{profile_id}_facility",
            )
            selected_filters["facilities"] = [chosen_facility] if chosen_facility else facilities
            display_context["empty_facility_selection"] = not facilities
        if states is not None:
            selected_filters["states"] = st.multiselect(
                "Audit state", sorted(set(states)), key=f"{key}_{profile_id}_state"
            )
        status = st.radio(
            "Audit count status",
            ["recruiting", "all"],
            format_func=lambda value: (
                "Confirmed RECRUITING" if value == "recruiting" else "All statuses"
            ),
            key=f"{key}_{profile_id}_status",
        )
        threshold = st.number_input(
            "Project-defined posted-update review warning (days)",
            min_value=0,
            value=180,
            step=1,
            key=f"{key}_{profile_id}_threshold",
            help=(
                "Warn when the posting age is strictly greater than this threshold; retain studies."
            ),
        )
        display_context["observation_selection"] = observation_metric or metric
        observations = data.metric_audit_observations(profile_id, observation_metric or metric)
        if metric == "competition" and not observations.empty:
            mode = st.selectbox(
                "Audit metric inputs",
                ["Current count", "90-day growth observations", "First-post (90-day proxy)"],
                key=f"{key}_{profile_id}_window",
            )
            if mode == "90-day growth observations":
                end = pd.Timestamp(pd.to_datetime(observations.snapshot_date).max()).date()
                observations = data.metric_audit_observations(profile_id, "recent_recruiting")
                dates = pd.to_datetime(observations.snapshot_date).dt.date
                observations = observations[(dates >= end - timedelta(days=90)) & (dates <= end)]
                metric = "recent_recruiting"
                display_context["growth_window_start"] = str(end - timedelta(days=90))
                display_context["growth_window_end"] = str(end)
                st.caption(
                    "Contributors are captured recruiting-entry events in this inclusive "
                    "90-day window. Predecessor evidence is retained even before the window. "
                    "Eligible coverage and count-input contributors remain separate."
                )
            elif mode == "First-post (90-day proxy)":
                end = pd.Timestamp(pd.to_datetime(observations.snapshot_date).max()).date()
                metric = "first_post_proxy"
                display_context["growth_window_start"] = str(end - timedelta(days=90))
                display_context["growth_window_end"] = str(end)
                st.caption(
                    "Proxy contributors require RECRUITING and source first-post date "
                    ">= snapshot date minus 90 days; the lower bound is inclusive, "
                    "with no upper bound, matching the source formula."
                )
        if observations.attrs.get("audit_unavailable"):
            st.warning(observations.attrs["audit_unavailable"])
            return
        if months is not None and not observations.empty:
            periods = sorted(
                {str(pd.Timestamp(month).to_period("M")) for month in months if pd.notna(month)}
            )
            chosen_month = st.selectbox(
                "Audit displayed month",
                ["All displayed months", *periods],
                key=f"{key}_{profile_id}_month",
            )
            scope = st.radio(
                "History input scope",
                ["Monthly counts", "Three-month growth inputs"],
                key=f"{key}_{profile_id}_history_scope",
            )
            scoped = periods if chosen_month == "All displayed months" else [chosen_month]
            if scope == "Three-month growth inputs":
                scoped = sorted(
                    {
                        str(pd.Period(month, freq="M") - offset)
                        for month in scoped
                        for offset in range(4)
                    }
                )
            observations = observations[
                pd.to_datetime(observations.snapshot_date)
                .dt.to_period("M")
                .astype(str)
                .isin(scoped)
            ]
            display_context["displayed_months"] = periods
            display_context["audited_months"] = scoped
        if observations.empty:
            st.info(
                "No captured observations match this scope. Clear filters or run "
                "make pipeline to build snapshot history."
            )
        try:
            result = build_display_audit(
                profile_id,
                observations,
                selected_filters,
                metric=metric,
                count_status=status,
                context=display_context,
                update_threshold_days=int(threshold),
            )
        except ValueError as error:
            st.warning(str(error))
            return
        # An empty table of facilities means no contributors, not unrestricted scope.
        if facilities is not None and not facilities:
            st.info(
                "No displayed facilities match the current filters. Clear the "
                "overlap or state filter."
            )
        st.caption(
            f"Rules: {result['metadata']['rule_version']} · snapshots: "
            + ", ".join(result["metadata"]["snapshot_ids"])
        )
        st.json(result["metadata"])
        st.json(result["summary"])
        st.caption(
            "Coverage describes inclusion of captured records under these rules. "
            "Flags overlap; exclusion reasons are mutually exclusive. Older posted updates "
            f"warn after {int(threshold)} days and remain included. "
            "Enrollment is study-level, separated "
            "by estimated/actual/missing/type and never summed across repeated observations. "
            "Raw references may no longer resolve after bronze retention pruning."
        )
        studies = result["studies"].copy()
        studies["registry_record"] = studies.nct_id.map(
            lambda nct: f"https://clinicaltrials.gov/study/{nct}"
        )
        studies["phase_normalized"] = studies["phase_normalized"].map(format_phase)
        for column in ["study_type", "overall_status", "exclusion_reason"]:
            studies[column] = (
                studies[column].astype("string").str.replace("_", " ").str.capitalize()
            )
        studies["exclusion_reason"] = studies["exclusion_reason"].fillna("Included")
        st.subheader("Study decisions and contributing NCT IDs")
        study_display_columns = [
            "brief_title",
            "study_type",
            "phase_normalized",
            "overall_status",
            "included_flag",
            "exclusion_reason",
            "snapshot_date",
            "nct_id",
            "registry_record",
            "snapshot_id",
        ]
        st.dataframe(
            studies[study_display_columns],
            hide_index=True,
            width="content",
            column_config={
                "brief_title": st.column_config.TextColumn("Trial", width="large"),
                "study_type": st.column_config.TextColumn("Study type"),
                "phase_normalized": st.column_config.TextColumn("Phase"),
                "overall_status": st.column_config.TextColumn("Status"),
                "included_flag": st.column_config.CheckboxColumn("Contributes"),
                "exclusion_reason": st.column_config.TextColumn("Decision detail"),
                "snapshot_date": st.column_config.DateColumn("Captured on", format="MMM D, YYYY"),
                "snapshot_id": st.column_config.TextColumn("Capture run ID"),
                "nct_id": st.column_config.TextColumn("NCT ID"),
                "registry_record": st.column_config.LinkColumn(
                    "Registry",
                    display_text="Open",
                    help="Opens the public ClinicalTrials.gov record",
                ),
            },
        )
        if metric in {"recent_recruiting", "first_post_proxy"}:
            st.subheader("Metric-specific contributing events and source evidence")
            events = result["events"].copy()
            events["phase_normalized"] = events["phase_normalized"].map(format_phase)
            event_columns = [
                "brief_title",
                "event_date",
                "phase_normalized",
                "overall_status",
                *(
                    ["previous_status", "predecessor_snapshot_date"]
                    if metric == "recent_recruiting"
                    else []
                ),
                "snapshot_date",
                "nct_id",
                "snapshot_id",
            ]
            for column in ["overall_status", "previous_status"]:
                if column in events:
                    events[column] = (
                        events[column].astype("string").str.replace("_", " ").str.capitalize()
                    )
            st.dataframe(
                events[[column for column in event_columns if column in events]],
                hide_index=True,
                width="content",
                column_config={
                    "brief_title": st.column_config.TextColumn("Trial", width="large"),
                    "phase_normalized": st.column_config.TextColumn("Phase"),
                    "overall_status": st.column_config.TextColumn("Current status"),
                    "previous_status": st.column_config.TextColumn("Previous status"),
                    "event_date": st.column_config.DateColumn(
                        "Recruiting entry date"
                        if metric == "recent_recruiting"
                        else "First posted (proxy)",
                        format="MMM D, YYYY",
                    ),
                    "predecessor_snapshot_date": st.column_config.DateColumn(
                        "Previous capture", format="MMM D, YYYY"
                    ),
                    "snapshot_date": st.column_config.DateColumn(
                        "Captured on", format="MMM D, YYYY"
                    ),
                    "nct_id": st.column_config.TextColumn("NCT ID"),
                    "snapshot_id": st.column_config.TextColumn("Capture run ID"),
                },
            )
        ids = result["contributors"]
        st.write({"contributing_nct_ids": ids})
        drill_ids = sorted(studies.nct_id.unique().tolist())
        if drill_ids:
            nct = st.selectbox(
                "Recorded locations for study", drill_ids, key=f"{key}_{profile_id}_nct"
            )
            locations = result["locations"].loc[result["locations"].nct_id == nct].copy()
            for column in ["location_status", "geography_category"]:
                locations[column] = (
                    locations[column].astype("string").str.replace("_", " ").str.capitalize()
                )
            location_columns = [
                "facility_name",
                "city",
                "state_raw",
                "country",
                "zip_code",
                "location_status",
                "geography_category",
                "usable_geography_flag",
                "location_ordinal",
                "nct_id",
                "snapshot_id",
            ]
            st.dataframe(
                locations[location_columns],
                hide_index=True,
                width="content",
                column_config={
                    "facility_name": st.column_config.TextColumn(
                        "Recorded facility", width="large"
                    ),
                    "city": st.column_config.TextColumn("City"),
                    "state_raw": st.column_config.TextColumn("Recorded state"),
                    "country": st.column_config.TextColumn("Country"),
                    "zip_code": st.column_config.TextColumn("Postal code"),
                    "location_status": st.column_config.TextColumn("Location status"),
                    "geography_category": st.column_config.TextColumn("Geography assessment"),
                    "usable_geography_flag": st.column_config.CheckboxColumn(
                        "Usable U.S. geography"
                    ),
                    "location_ordinal": st.column_config.NumberColumn("Recorded row", format="%d"),
                    "nct_id": st.column_config.TextColumn("NCT ID"),
                    "snapshot_id": st.column_config.TextColumn("Capture run ID"),
                },
            )
        if not ids:
            st.info(
                "No studies contribute under the selected rules. Inspect "
                "exclusions above or clear filters."
            )
        st.download_button(
            "Download metric audit (JSON)",
            export_audit(result),
            file_name=f"{profile_id}_{metric}_audit.json",
            mime="application/json",
            key=f"{key}_{profile_id}_export",
        )
