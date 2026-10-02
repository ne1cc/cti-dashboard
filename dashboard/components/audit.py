"""Pure captured-record audit computations; no warehouse or UI dependencies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pandas as pd

KEY = ["indication_profile_id", "snapshot_id", "nct_id"]
RULE_VERSION = "competition-audit-v1"
DISCLAIMER = (
    "Registry-derived signals support preliminary feasibility review. They do not measure "
    "site-level recruitment performance or establish scientific validity. Counts reflect "
    "captured public records and the displayed inclusion rules."
)
DEFINITIONS = {
    "competition": "Distinct confirmed RECRUITING NCT IDs with reported usable "
    "U.S. state geography.",
    "history": "Distinct NCT IDs across selected daily observations with usable "
    "U.S. state geography.",
    "facility": "Distinct NCT IDs with usable U.S. state and normalized facility name; facility "
    "identity matches normalized name, city and state text.",
    "latest_study": "Distinct NCT IDs from each study's current captured observation.",
}
OBSERVATION_SOURCES = {
    "competition": "int_trial_status_history at mart_recruiting_competition latest profile date",
    "history": "int_trial_status_history: latest study observation per profile/date",
    "facility": "int_trial_status_history at mart_site_overlap latest profile date",
    "latest_study": "int_current_trial_status: latest observation per profile/study",
}


def _keys(frame: pd.DataFrame) -> set[tuple]:
    return set(frame[KEY].itertuples(index=False, name=None)) if not frame.empty else set()


def compute_audit(
    studies: pd.DataFrame,
    locations: pd.DataFrame,
    conditions: pd.DataFrame,
    *,
    profile_id: str,
    observations: pd.DataFrame,
    filters: dict[str, Any],
    metric: str = "competition",
    count_status: str = "recruiting",
    evaluation_time: Any,
    update_threshold_days: int = 180,
    rule_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit exact observations and filters before geographic/status exclusions.

    Filters: conditions, phases, states, sponsors, and facilities (triples of
    normalized name/city/state). Empty selections mean unrestricted. Totals
    count distinct NCT IDs; history enrollment is separately grouped by snapshot
    and never summed across repeated observations. Zero coverage is undefined
    (None) when its eligible denominator is empty. Flags may overlap; the first
    applicable exclusion wins in status, geography, region, facility order.
    """
    if metric not in DEFINITIONS:
        raise ValueError(f"Unknown audit metric: {metric}")
    if count_status not in {"recruiting", "all"}:
        raise ValueError("count_status must be recruiting or all")
    if update_threshold_days < 0:
        raise ValueError("update_threshold_days must be nonnegative")
    evaluated = pd.Timestamp(evaluation_time)
    if evaluated.tzinfo is None:
        raise ValueError("evaluation_time must include a UTC offset")
    evaluated = evaluated.tz_convert("UTC")
    selected = (
        observations.loc[observations.indication_profile_id == profile_id]
        if not (observations.empty)
        else observations
    )
    frame = (
        studies.loc[studies.indication_profile_id == profile_id].copy()
        if not (studies.empty)
        else studies.copy()
    )
    if frame.empty:
        frame = frame.reindex(
            columns=list(
                dict.fromkeys(
                    [
                        *frame.columns,
                        *KEY,
                        "snapshot_date",
                        "enrollment_category",
                        "enrollment_count",
                    ]
                )
            )
        )
    snapshot_records = []
    if not frame.empty:
        frame = frame.merge(selected[KEY].drop_duplicates(), on=KEY, how="inner")
        frame = frame.drop_duplicates(KEY)
        snapshot_records = (
            frame[["snapshot_id", "snapshot_date"]].drop_duplicates().to_dict("records")
        )
        if filters.get("conditions"):
            matching = conditions.loc[conditions.condition_group.isin(filters["conditions"])]
            frame = frame.merge(matching[KEY].drop_duplicates(), on=KEY, how="inner")
        for selection, column in [
            ("phases", "phase_normalized"),
            ("sponsors", "lead_sponsor_name"),
        ]:
            if filters.get(selection):
                frame = frame.loc[frame[column].isin(filters[selection])].copy()
    scoped_locations = locations.copy()
    if not locations.empty:
        scoped_locations = locations.merge(frame[KEY], on=KEY, how="inner")
    valid = (
        scoped_locations.loc[scoped_locations.usable_geography_flag.fillna(False)]
        if not (scoped_locations.empty)
        else scoped_locations
    )
    regional = valid
    if filters.get("states") and not regional.empty:
        regional = regional.loc[regional.state_normalized.isin(filters["states"])]
    facility = regional
    if metric == "facility" and not facility.empty:
        facility = facility.loc[facility.facility_normalized.notna()]
        if filters.get("facilities"):
            identities = {tuple(item) for item in filters["facilities"]}
            facility = facility.loc[
                facility[["facility_normalized", "city_normalized", "state_normalized"]]
                .apply(lambda row: tuple(None if pd.isna(v) else v for v in row), axis=1)
                .isin(identities)
            ]
    regional_keys, facility_keys = _keys(regional), _keys(facility)
    condition_keys = _keys(conditions)
    decisions = []
    for _, row in frame.iterrows():
        key = tuple(row[col] for col in KEY)
        recruiting = pd.notna(row.overall_status) and row.overall_status == "RECRUITING"
        membership = (
            "inside"
            if key in regional_keys
            else "undetermined"
            if row.geography_category == "missing"
            else "outside"
        )
        reason = None
        if count_status == "recruiting" and not recruiting:
            reason = "not_confirmed_recruiting"
        elif metric in {"competition", "history"} and key not in condition_keys:
            reason = "missing_condition_mapping"
        elif metric != "latest_study" or filters.get("states"):
            if row.geography_category != "usable":
                reason = f"{row.geography_category}_geography"
            elif membership == "outside":
                reason = "outside_selected_region"
            elif metric == "facility" and key not in facility_keys:
                reason = "missing_or_unmatched_facility"
        flags = []
        if metric in {"competition", "history"} and key not in condition_keys:
            flags.append("missing_condition_mapping")
        age = row.posted_update_age_days
        older = pd.notna(age) and age > update_threshold_days
        if older:
            flags.append("older_posted_update")
        if pd.isna(age):
            flags.append("missing_or_unparseable_posted_update")
        if not recruiting:
            flags.append("not_confirmed_recruiting")
        if row.geography_category != "usable":
            flags.append(f"{row.geography_category}_geography")
        if row.get("missing_facility_location_count", 0) > 0:
            flags.append("missing_facility")
        decisions.append(
            dict(
                confirmed_recruiting_flag=recruiting,
                region_membership=membership,
                included_flag=reason is None,
                exclusion_reason=reason,
                older_posted_update_flag=older,
                flag_reasons=flags,
            )
        )
    for column in [
        "confirmed_recruiting_flag",
        "region_membership",
        "included_flag",
        "exclusion_reason",
        "older_posted_update_flag",
        "flag_reasons",
    ]:
        frame[column] = [decision[column] for decision in decisions]
    frame["pipeline_age_hours"] = (
        evaluated
        - pd.to_datetime(frame.get("snapshot_ended_at_utc", pd.Series(dtype="object")), utc=True)
    ).dt.total_seconds() / 3600
    included = frame.loc[frame.included_flag.astype(bool)].sort_values(
        ["snapshot_date", "snapshot_id"]
    )
    # A study contributing on any selected observation is included in the union.
    unique = frame.sort_values(["snapshot_date", "snapshot_id"]).drop_duplicates(
        "nct_id", keep="last"
    )
    contributor_ids = sorted(included.nct_id.unique().tolist()) if not included.empty else []
    excluded = unique.loc[~unique.nct_id.isin(contributor_ids)] if not unique.empty else unique
    eligible = int(frame.nct_id.nunique()) if not frame.empty else 0
    recruiting_ids = frame.loc[frame.confirmed_recruiting_flag.astype(bool), "nct_id"].unique()
    recruiting_contributors = included.loc[
        included.confirmed_recruiting_flag.astype(bool), "nct_id"
    ]

    def enrollment_totals(records: pd.DataFrame) -> dict:
        return {
            category: {
                "studies": int((records.enrollment_category == category).sum()),
                "total": None
                if category == "missing"
                else float(
                    records.loc[records.enrollment_category == category, "enrollment_count"].sum()
                ),
            }
            for category in ["estimated", "actual", "missing", "unknown_type"]
        }

    enrollment = (
        enrollment_totals(included.drop_duplicates("nct_id", keep="last"))
        if not (included.empty)
        else {
            c: {"studies": 0, "total": None if c == "missing" else 0}
            for c in ["estimated", "actual", "missing", "unknown_type"]
        }
    )
    summary = dict(
        eligible_studies=eligible,
        contributing_studies=len(contributor_ids),
        recruiting_eligible_studies=len(recruiting_ids),
        coverage=len(contributor_ids) / eligible if eligible else None,
        recruiting_coverage=recruiting_contributors.nunique() / len(recruiting_ids)
        if len(recruiting_ids)
        else None,
        region_undetermined_studies=int(
            frame.loc[frame.region_membership == "undetermined", "nct_id"].nunique()
        ),
        exclusions=excluded.exclusion_reason.value_counts().to_dict(),
        enrollment=enrollment,
        enrollment_by_snapshot={
            str(sid): enrollment_totals(group) for sid, group in included.groupby("snapshot_id")
        },
    )
    snapshots = snapshot_records
    metadata = dict(
        profile_id=profile_id,
        metric=metric,
        definition=DEFINITIONS[metric],
        observation_source=OBSERVATION_SOURCES[metric],
        count_status=count_status,
        filters=deepcopy(filters),
        rule_version=RULE_VERSION,
        rule_config=deepcopy(rule_config or {}),
        snapshot_ids=sorted({str(item["snapshot_id"]) for item in snapshots}),
        snapshots=snapshots,
        evaluation_time=evaluated.isoformat(),
        update_threshold_days=update_threshold_days,
        update_threshold_policy="Project-defined review threshold; warn and retain.",
        enrollment_scope="Latest included observation per study; per-run totals separate.",
        disclaimer=DISCLAIMER,
        raw_reference_caveat="Raw references may no longer resolve after bronze retention pruning.",
    )
    return dict(
        metadata=metadata,
        summary=summary,
        studies=frame,
        locations=scoped_locations,
        contributors=contributor_ids,
    )


def export_audit(result: dict[str, Any]) -> bytes:
    """Serialize all decisions/provenance to JSON, preserving nulls and date precision."""
    import json

    payload = {
        **result,
        "studies": result["studies"].to_dict("records"),
        "locations": result["locations"].to_dict("records"),
    }

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        if value is None or pd.isna(value):
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if hasattr(value, "item"):
            return value.item()
        return value

    return json.dumps(clean(payload), allow_nan=False, sort_keys=True).encode("utf-8")
