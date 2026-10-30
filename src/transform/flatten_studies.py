"""Flatten nested ClinicalTrials.gov study JSON into the silver entities.

Grounded in the actual API v2 structure (verified against saved bronze pages
and the v2 OpenAPI spec, https://clinicaltrials.gov/api/oas/v2, version 2.0.5):
protocolSection.{identification,status,sponsorCollaborators,design,conditions,
armsInterventions,outcomes,eligibility,contactsLocations}Module + top-level
hasResults + derivedSection.conditionBrowseModule. Location contact/investigator
fields are deliberately not extracted.

MeSH browse modules (Issue #52): derivedSection.conditionBrowseModule carries
study-level `meshes` (direct terms) and `ancestors` (hierarchy closure) as
{id, term} pairs. The registry supplies no per-condition-text edge and no
branch-to-term edge (browseBranches is an unlinked {abbrev, name} list), so
rows are study-level and `branch` stays NULL; a condition's MeSH identity is
joined at trial grain, not per condition string.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from src.transform.normalize_conditions import ConditionTaxonomy
from src.transform.normalize_locations import GeographyRules
from src.utils.dates import parse_partial_date
from src.utils.hashing import sha256_json
from src.utils.text import normalize_text

# Group identity for profiles with no condition taxonomy: conditions keep
# their raw text, every row falls back, and MeSH rows carry the browsable
# identity (Issue #52). Snake_case like the taxonomy group slugs.
UNMAPPED_CONDITION_GROUP = "unmapped"


def extract_condition_mesh(study: dict[str, Any]) -> list[dict[str, str | bool]]:
    """Return deduplicated {id, term, is_ancestor} MeSH rows for a study.

    Direct terms win over ancestors for the same id (a term both applied
    directly and reached as an ancestor is one direct identity, not two).
    Registry order is otherwise preserved. Rows missing id or term are
    dropped: mesh_id is the join identity and an unlabelled node is not
    browsable.
    """
    module = dig(study, "derivedSection", "conditionBrowseModule") or {}
    rows: list[dict[str, str | bool]] = []
    seen: set[str] = set()
    for is_ancestor, key in ((False, "meshes"), (True, "ancestors")):
        for mesh in module.get(key) or []:
            mesh_id = mesh.get("id")
            term = mesh.get("term")
            if not mesh_id or not term or mesh_id in seen:
                continue
            seen.add(mesh_id)
            rows.append({"id": mesh_id, "term": term, "is_ancestor": is_ancestor})
    return rows


def dig(obj: Any, *keys: str) -> Any:
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _struct_date(status_module: dict[str, Any], key: str) -> str | None:
    val = dig(status_module, key, "date")
    return str(val) if isinstance(val, str) else None


def normalize_phases(phases: list[str] | None) -> tuple[str | None, str]:
    """Returns (phase_raw, phase_normalized). Multi-phase studies join with '/'.
    Missing → UNKNOWN; NA → NOT_APPLICABLE."""
    if not phases:
        return None, "UNKNOWN"
    raw = "|".join(phases)
    normalized = [("NOT_APPLICABLE" if p == "NA" else p) for p in phases]
    return raw, "/".join(normalized)


def trial_quality_flags(row: dict[str, Any]) -> str:
    """Comma-joined issue codes, or 'ok'."""
    issues: list[str] = []
    if not row.get("overall_status"):
        issues.append("missing_overall_status")
    if not row.get("study_type"):
        issues.append("missing_study_type")
    start = parse_partial_date(row.get("start_date"))
    completion = parse_partial_date(row.get("completion_date"))
    if start and completion and start > completion:
        issues.append("start_after_completion")
    first_post = parse_partial_date(row.get("study_first_post_date"))
    results_post = parse_partial_date(row.get("results_first_post_date"))
    if first_post and results_post and results_post < first_post:
        issues.append("results_before_first_post")
    enrollment = row.get("enrollment_count")
    if enrollment is not None and enrollment < 0:
        issues.append("negative_enrollment")
    return ",".join(issues) if issues else "ok"


def flatten_study(
    study: dict[str, Any],
    ingestion_run_id: str,
    snapshot_timestamp_utc: str,
    taxonomy: ConditionTaxonomy | None,
    geography: GeographyRules,
    indication_profile_id: str = "adrd",
    retrieved_at_utc: str | None = None,
    raw_page_reference: str | None = None,
    raw_study_ordinal: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """One study → rows for each silver entity.

    Args:
        taxonomy: The profile's condition taxonomy overlay. None means the
            profile has none (Issue #52): condition rows fall back
            (mapping_source text_fallback, group "unmapped") and the MeSH
            browse module carries the condition identity.
        indication_profile_id: Profile that produced this run. Written to every
            silver entity row so the shared silver/gold tables can be filtered or
            partitioned by indication. Defaults to "adrd" for backward compatibility
            with callers that predate the profile system.
    """
    protocol = study.get("protocolSection", {})
    identification = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    sponsors_module = protocol.get("sponsorCollaboratorsModule", {})
    eligibility = protocol.get("eligibilityModule", {})

    nct_id = identification.get("nctId")
    source_json_hash = sha256_json(study)
    base = {
        "ingestion_run_id": ingestion_run_id,
        "indication_profile_id": indication_profile_id,
        "nct_id": nct_id,
        "source_json_hash": source_json_hash,
        "snapshot_id": ingestion_run_id,
        "retrieved_at_utc": retrieved_at_utc,
        "raw_page_reference": raw_page_reference,
        "raw_study_ordinal": raw_study_ordinal,
    }

    phase_raw, phase_normalized = normalize_phases(design.get("phases"))
    trial = {
        **base,
        "ingestion_run_id": ingestion_run_id,
        "indication_profile_id": indication_profile_id,
        "snapshot_timestamp_utc": snapshot_timestamp_utc,
        "nct_id": nct_id,
        "brief_title": identification.get("briefTitle"),
        "official_title": identification.get("officialTitle"),
        "study_type": design.get("studyType"),
        "overall_status": status.get("overallStatus"),
        "last_known_status": status.get("lastKnownStatus"),
        "status_verified_date": status.get("statusVerifiedDate"),
        "expanded_access_info": dig(status, "expandedAccessInfo", "hasExpandedAccess"),
        "start_date": _struct_date(status, "startDateStruct"),
        "primary_completion_date": _struct_date(status, "primaryCompletionDateStruct"),
        "completion_date": _struct_date(status, "completionDateStruct"),
        "study_first_post_date": _struct_date(status, "studyFirstPostDateStruct"),
        "results_first_post_date": _struct_date(status, "resultsFirstPostDateStruct"),
        "last_update_post_date": _struct_date(status, "lastUpdatePostDateStruct"),
        "phase_raw": phase_raw,
        "phase_normalized": phase_normalized,
        "enrollment_count": dig(design, "enrollmentInfo", "count"),
        "enrollment_type": dig(design, "enrollmentInfo", "type"),
        "allocation": dig(design, "designInfo", "allocation"),
        "primary_purpose": dig(design, "designInfo", "primaryPurpose"),
        "lead_sponsor_name": dig(sponsors_module, "leadSponsor", "name"),
        "responsible_party_type": dig(sponsors_module, "responsibleParty", "type"),
        "healthy_volunteers": eligibility.get("healthyVolunteers"),
        "minimum_age": eligibility.get("minimumAge"),
        "maximum_age": eligibility.get("maximumAge"),
        "sex": eligibility.get("sex"),
        "gender_based": eligibility.get("genderBased"),
        "gender_description": eligibility.get("genderDescription"),
        "std_age_groups": eligibility.get("stdAges"),
        "eligibility_criteria_text": eligibility.get("eligibilityCriteria"),
        "has_results_flag": bool(study.get("hasResults", False)),
        "source_json_hash": source_json_hash,
    }
    trial["record_quality_flag"] = trial_quality_flags(trial)

    conditions = []
    for condition_raw in dig(protocol, "conditionsModule", "conditions") or []:
        if taxonomy is not None:
            mapping = taxonomy.map_condition(condition_raw)
            # "low" confidence is the taxonomy's unmatched fallback; high/medium
            # mean a YAML overlay rule matched (exact → substring).
            mapping_source = (
                "yaml_overlay" if mapping.mapping_confidence != "low" else "text_fallback"
            )
        else:
            mapping = None
            mapping_source = "text_fallback"
        conditions.append(
            {
                **base,
                "condition_raw": condition_raw,
                # Normalization is a text utility, not taxonomy state: rows
                # keep it even when no overlay exists, so downstream text
                # consumers see one behaviour across profiles.
                "condition_normalized": (
                    mapping.condition_normalized if mapping else normalize_text(condition_raw)
                ),
                "condition_group": (
                    mapping.condition_group if mapping else UNMAPPED_CONDITION_GROUP
                ),
                "dementia_relevance_flag": (mapping.dementia_relevance_flag if mapping else False),
                "mapping_confidence": mapping.mapping_confidence if mapping else "low",
                "mapping_source": mapping_source,
            }
        )

    condition_mesh = [
        {
            **base,
            "mesh_id": mesh["id"],
            "mesh_term": mesh["term"],
            "is_ancestor": mesh["is_ancestor"],
            # browseBranches carries {abbrev, name} with no edge to terms, so
            # the registry supplies no branch attribution to copy (Issue #52).
            "branch": None,
        }
        for mesh in extract_condition_mesh(study)
    ]

    interventions = []
    for item in dig(protocol, "armsInterventionsModule", "interventions") or []:
        interventions.append(
            {
                **base,
                "intervention_name": item.get("name"),
                "intervention_type": item.get("type"),
                "intervention_description": item.get("description"),
                "intervention_normalized": normalize_text(item.get("name")),
            }
        )

    sponsors = []
    lead = sponsors_module.get("leadSponsor") or {}
    if lead.get("name"):
        sponsors.append(
            {
                **base,
                "sponsor_name": lead.get("name"),
                "sponsor_role": "lead_sponsor",
                "sponsor_class": lead.get("class"),
                "sponsor_normalized": normalize_text(lead.get("name")),
            }
        )
    for collaborator in sponsors_module.get("collaborators") or []:
        if collaborator.get("name"):
            sponsors.append(
                {
                    **base,
                    "sponsor_name": collaborator.get("name"),
                    "sponsor_role": "collaborator",
                    "sponsor_class": collaborator.get("class"),
                    "sponsor_normalized": normalize_text(collaborator.get("name")),
                }
            )

    locations = []
    for location_ordinal, loc in enumerate(
        dig(protocol, "contactsLocationsModule", "locations") or []
    ):
        norm = geography.normalize_location(
            loc.get("facility"), loc.get("city"), loc.get("state"), loc.get("country")
        )
        locations.append(
            {
                **base,
                "location_ordinal": location_ordinal,
                "facility_name": loc.get("facility"),
                "facility_normalized": normalize_text(loc.get("facility")),
                "city": loc.get("city"),
                "state": loc.get("state"),
                "state_normalized": norm.state_normalized,
                "zip_code": loc.get("zip"),
                "country": loc.get("country"),
                "geo_scope": norm.geo_scope,
                "latitude": dig(loc, "geoPoint", "lat"),
                "longitude": dig(loc, "geoPoint", "lon"),
                "location_status": loc.get("status"),
                "us_location_flag": norm.us_location_flag,
                "usable_geography_flag": norm.usable_geography_flag,
            }
        )

    outcomes = []
    outcomes_module = protocol.get("outcomesModule", {})
    for outcome_type, key in (
        ("primary", "primaryOutcomes"),
        ("secondary", "secondaryOutcomes"),
        ("other", "otherOutcomes"),
    ):
        for index, outcome in enumerate(outcomes_module.get(key) or []):
            outcomes.append(
                {
                    **base,
                    "outcome_type": outcome_type,
                    "outcome_index": index,
                    "outcome_measure": outcome.get("measure"),
                    "outcome_description": outcome.get("description"),
                    "time_frame": outcome.get("timeFrame"),
                }
            )

    return {
        "silver_trials": [trial],
        "silver_trial_conditions": conditions,
        "silver_trial_condition_mesh": condition_mesh,
        "silver_trial_interventions": interventions,
        "silver_trial_sponsors": sponsors,
        "silver_trial_locations": locations,
        "silver_trial_outcomes": outcomes,
    }


def iter_bronze_studies(run_dir: Path) -> Iterator[dict[str, Any]]:
    """Yield study dicts from every page file of a bronze run, in page order."""
    for page_path in sorted(run_dir.glob("page=*.json")):
        payload = json.loads(page_path.read_text(encoding="utf-8"))
        yield from payload.get("studies", [])


def iter_bronze_study_records(run_dir: Path) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield (study, provenance) in source order; ordinals are zero-based.

    Raw page references resolve relative to the configured bronze response root.
    Historical pages without sidecars retain unknown retrieval times.
    """
    for page_path in sorted(run_dir.glob("page=*.json")):
        payload = json.loads(page_path.read_text(encoding="utf-8"))
        metadata_path = run_dir / "_page_metadata" / page_path.name
        metadata = (
            json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        )
        for ordinal, study in enumerate(payload.get("studies", [])):
            yield (
                study,
                {
                    "retrieved_at_utc": metadata.get("retrieved_at_utc"),
                    "raw_page_reference": f"{run_dir.name}/{page_path.name}",
                    "raw_study_ordinal": ordinal,
                },
            )
