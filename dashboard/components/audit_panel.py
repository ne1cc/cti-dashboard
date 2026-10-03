"""Shared, filter-aware drill-through for displayed registry metrics."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from components import data
from components.audit import DISCLAIMER, compute_audit, export_audit
from src.utils.paths import project_root

PRIORITY_DERIVED_RULES = (
    "Each component uses (input - profile minimum)/(profile maximum - minimum), "
    "with zero for no spread; weighted sum uses the active score weights. "
    "Bands are fixed score thresholds: priority_review >=0.70, review >=0.45, "
    "watch otherwise. Recruiting inputs and sponsor HHI use current segment "
    "counts; HHI sums squared normalized lead-sponsor shares of segment trials. "
    "Growth sums daily recruiting entrants over 90 days, requiring a previous "
    "non-null non-recruiting status; with one captured date, first-post >= "
    "snapshot minus 90 days is the proxy. Site overlap is distinct recruiting "
    "segment trials sharing any facility with >1 recruiting trial in the "
    "profile divided by distinct recruiting segment trials. Legacy operational "
    "completeness = 0.5 segment record-quality-ok share + 0.5 latest successful-run "
    "usable-location share (missing shares coalesce to zero). This panel audits "
    "count inputs; derived score values remain warehouse calculations."
)


def active_rules(profile_id: str) -> dict:
    """Identify the active configuration; do not claim historical rule identity."""
    root = project_root()
    profile = yaml.safe_load((root / f"config/profiles/{profile_id}.yml").read_text())
    paths = {
        "taxonomy": profile["profile"]["taxonomy"],
        "geography": "config/geography_rules.yml",
        "score_weights": profile["profile"]["score_weights"],
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
    profile_id,
    observations,
    filters,
    *,
    metric="competition",
    count_status="recruiting",
    context=None,
):
    ids = observations.snapshot_id.unique().tolist() if not observations.empty else []
    studies = data.audit_studies(profile_id, ids)
    locations = data.audit_locations(profile_id, ids)
    conditions = data.audit_condition_mapping(profile_id)
    for frame in (studies, locations, conditions):
        if frame.attrs.get("audit_unavailable"):
            raise ValueError(frame.attrs["audit_unavailable"])
    if (context or {}).get("empty_facility_selection"):
        studies = studies.iloc[:0]
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
        update_threshold_days=180,
        rule_config={**active_rules(profile_id), "display_context": context or {}},
    )
    if (context or {}).get("observation_selection") == "history":
        result["metadata"]["observation_source"] = (
            "int_trial_status_history: latest study observation per profile/date"
        )
    return result


def render_metric_audit(
    profile_id,
    *,
    filters=None,
    metric="competition",
    observation_metric=None,
    segments=None,
    sponsors=None,
    facilities=None,
    months=None,
    states=None,
    context=None,
    key="metric",
):
    """Audit whole filtered scope or a displayed row, with explicit status/period."""
    with st.expander("Data coverage and caveats"):
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
            chosen = st.selectbox(
                "Audit segment",
                [None, *options],
                format_func=lambda row: (
                    "Sidebar scope before derived row filters" if row is None else " · ".join(row)
                ),
                key=f"{key}_{profile_id}_segment",
            )
            if chosen:
                selected_filters.update(
                    conditions=[chosen[0]], states=[chosen[1]], phases=[chosen[2]]
                )
            display_context["displayed_segments"] = options
            values = segments
            if chosen:
                values = segments[
                    (segments.condition_group == chosen[0])
                    & (segments.state_normalized == chosen[1])
                    & (segments.phase_normalized == chosen[2])
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
            chosen = st.selectbox(
                "Audit facility",
                [None, *facilities],
                format_func=lambda row: (
                    "All shown facilities" if row is None else " · ".join(str(v or "") for v in row)
                ),
                key=f"{key}_{profile_id}_facility",
            )
            selected_filters["facilities"] = [chosen] if chosen else facilities
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
        display_context["observation_selection"] = observation_metric or metric
        observations = data.metric_audit_observations(profile_id, observation_metric or metric)
        if metric == "competition" and not observations.empty:
            mode = st.selectbox(
                "Audit metric inputs",
                ["Current count", "90-day growth observations"],
                key=f"{key}_{profile_id}_window",
            )
            if mode == "90-day growth observations":
                end = pd.Timestamp(pd.to_datetime(observations.snapshot_date).max()).date()
                observations = data.metric_audit_observations(profile_id, "history")
                dates = pd.to_datetime(observations.snapshot_date).dt.date
                observations = observations[(dates >= end - timedelta(days=90)) & (dates <= end)]
                metric = "history"
                display_context["growth_window_start"] = str(end - timedelta(days=90))
                display_context["growth_window_end"] = str(end)
                st.caption(
                    "These are the captured observations underlying the growth "
                    "window. Transition counts require the previous observation "
                    "status; this audit does not replace the derived growth value."
                )
        if observations.attrs.get("audit_unavailable"):
            st.warning(observations.attrs["audit_unavailable"])
            return
        if months is not None and not observations.empty:
            periods = sorted(
                {str(pd.Timestamp(month).to_period("M")) for month in months if pd.notna(month)}
            )
            chosen = st.selectbox(
                "Audit displayed month",
                ["All displayed months", *periods],
                key=f"{key}_{profile_id}_month",
            )
            scope = st.radio(
                "History input scope",
                ["Monthly counts", "Three-month growth inputs"],
                key=f"{key}_{profile_id}_history_scope",
            )
            scoped = periods if chosen == "All displayed months" else [chosen]
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
            "warn after 180 days and remain included. Enrollment is study-level, separated "
            "by estimated/actual/missing/type and never summed across repeated observations. "
            "Raw references may no longer resolve after bronze retention pruning."
        )
        studies = result["studies"].copy()
        studies["registry_record"] = studies.nct_id.map(
            lambda nct: f"https://clinicaltrials.gov/study/{nct}"
        )
        studies["audit_rule_version"] = result["metadata"]["rule_version"]
        st.subheader("Study decisions and contributing NCT IDs")
        st.dataframe(
            studies,
            hide_index=True,
            width="stretch",
            column_config={"registry_record": st.column_config.LinkColumn("Study drill-through")},
        )
        ids = result["contributors"]
        st.write({"contributing_nct_ids": ids})
        drill_ids = sorted(studies.nct_id.unique().tolist())
        if drill_ids:
            nct = st.selectbox(
                "Recorded locations for study", drill_ids, key=f"{key}_{profile_id}_nct"
            )
            locations = result["locations"].loc[result["locations"].nct_id == nct].copy()
            locations["audit_rule_version"] = result["metadata"]["rule_version"]
            st.dataframe(locations, hide_index=True, width="stretch")
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
