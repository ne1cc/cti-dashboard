"""ClinicalTrials.gov live upstream query builder and response normalizer.

Enforces ADR 0065 privacy guardrails: strictly zero coordinator, investigator,
or staff personal contact PII (no personal phone numbers or direct emails).
Official inquiries are directed to the canonical registry URL.
"""

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any

from src.api.models import (
    DataProvenance,
    EnrollmentInfo,
    GeoPoint,
    NearestSiteSummary,
    SponsorDetail,
    StructuredEligibility,
    StructuredEligibilitySummary,
    StudyCondition,
    StudyDates,
    StudyDesign,
    StudyIntervention,
    StudySponsors,
    TrialDetail,
    TrialSite,
    TrialSummary,
)
from src.transform.eligibility import parse_age_to_years
from src.transform.flatten_studies import normalize_phases
from src.utils.dates import parse_partial_date

EARTH_RADIUS_MILES = 3958.8
DEFAULT_ACTIVE_STATUSES = (
    "RECRUITING|NOT_YET_RECRUITING|ACTIVE_NOT_RECRUITING|ENROLLING_BY_INVITATION"
)


def haversine_distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points on Earth in miles."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * (
        math.sin(delta_lambda / 2.0) ** 2
    )
    # Clip for floating point inaccuracies
    a_clamped = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a_clamped), math.sqrt(1.0 - a_clamped))
    return EARTH_RADIUS_MILES * c


def build_geo_filter(lat: float, lon: float, radius: float | int | str = 50.0) -> str:
    """Build ClinicalTrials.gov v2 geographic distance filter syntax."""
    rad_str = str(radius).strip()
    if not (rad_str.endswith("mi") or rad_str.endswith("km")):
        rad_str = f"{rad_str}mi"
    return f"distance({lat},{lon},{rad_str})"


def build_upstream_search_params(
    cond: str | None = None,
    status: str | None = None,
    phase: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_mi: float | int | None = 50.0,
    cursor: str | None = None,
    limit: int = 20,
    count_total: bool = True,
) -> dict[str, str]:
    """Map public API query parameters to ClinicalTrials.gov v2 upstream parameters."""
    params: dict[str, str] = {
        "format": "json",
        "pageSize": str(limit),
    }
    if count_total:
        params["countTotal"] = "true"
    if cond:
        params["query.cond"] = cond.strip()
    if status:
        params["filter.overallStatus"] = status.strip()
    else:
        params["filter.overallStatus"] = DEFAULT_ACTIVE_STATUSES

    if lat is not None and lon is not None:
        eff_radius = radius_mi if radius_mi is not None else 50.0
        params["filter.geo"] = build_geo_filter(lat, lon, eff_radius)

    if cursor:
        params["pageToken"] = cursor.strip()

    return params


def _extract_date_str(struct: Any) -> str | None:
    """Extract standard ISO date string (YYYY-MM-DD) from dateStruct if present."""
    if not isinstance(struct, dict):
        return None
    raw_date = struct.get("date")
    if not raw_date or not isinstance(raw_date, str):
        return None
    parsed = parse_partial_date(raw_date)
    return parsed.isoformat() if parsed else raw_date


def _sha256_study(study: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of raw study dictionary."""
    encoded = json.dumps(study, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_study_summary(
    study: dict[str, Any],
    user_lat: float | None = None,
    user_lon: float | None = None,
) -> TrialSummary:
    """Normalize raw ClinicalTrials.gov study object into TrialSummary.

    Strictly complies with ADR 0065: personal contact PII is never parsed or returned.
    """
    protocol = study.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    sponsors = protocol.get("sponsorCollaboratorsModule", {})
    conditions_mod = protocol.get("conditionsModule", {})
    elig = protocol.get("eligibilityModule", {})
    contacts_loc = protocol.get("contactsLocationsModule", {})

    nct_id = ident.get("nctId", "NCT00000000")
    brief_title = ident.get("briefTitle", "Untitled Study")
    overall_status = status.get("overallStatus", "UNKNOWN")
    study_type = design.get("studyType", "INTERVENTIONAL")

    raw_phases = design.get("phases")
    _, phase_normalized = normalize_phases(raw_phases)

    lead_sponsor = sponsors.get("leadSponsor", {})
    lead_sponsor_name = lead_sponsor.get("name", "Unknown Sponsor")
    sponsor_class = lead_sponsor.get("class")

    start_date = _extract_date_str(status.get("startDateStruct"))
    last_update = _extract_date_str(status.get("lastUpdatePostDateStruct"))

    conditions: list[str] = [
        str(c) for c in conditions_mod.get("conditions", []) if isinstance(c, str)
    ]

    # Structured eligibility
    min_age_raw = elig.get("minimumAge")
    max_age_raw = elig.get("maximumAge")
    min_age_years = parse_age_to_years(min_age_raw) if min_age_raw else None
    max_age_years = parse_age_to_years(max_age_raw) if max_age_raw else None
    age_not_stated = min_age_years is None and max_age_years is None
    sex = elig.get("sex", "ALL")
    healthy_volunteers = elig.get("healthyVolunteers")
    std_ages = elig.get("stdAges")
    if not isinstance(std_ages, list):
        std_ages = ["ADULT", "OLDER_ADULT"] if not age_not_stated else []

    structured_eligibility = StructuredEligibilitySummary(
        minimum_age_years=min_age_years,
        maximum_age_years=max_age_years,
        age_not_stated=age_not_stated,
        sex=sex,
        accepts_healthy_volunteers=healthy_volunteers,
        std_age_groups=[str(g) for g in std_ages],
    )

    # Nearest site calculation (ADR 0065 compliant: no personal contacts)
    nearest_site: NearestSiteSummary | None = None
    locations = contacts_loc.get("locations", [])
    if user_lat is not None and user_lon is not None and isinstance(locations, list):
        closest_distance: float | None = None
        closest_facility: str | None = None
        closest_city: str | None = None
        closest_state: str | None = None

        for loc in locations:
            if not isinstance(loc, dict):
                continue
            geo = loc.get("geoPoint")
            if isinstance(geo, dict) and "lat" in geo and "lon" in geo:
                try:
                    site_lat = float(geo["lat"])
                    site_lon = float(geo["lon"])
                    dist = haversine_distance_miles(user_lat, user_lon, site_lat, site_lon)
                    if closest_distance is None or dist < closest_distance:
                        closest_distance = dist
                        closest_facility = loc.get("facility") or "Listed Site"
                        closest_city = loc.get("city") or ""
                        closest_state = loc.get("state")
                except (ValueError, TypeError):
                    continue

        if closest_distance is not None and closest_facility and closest_city is not None:
            nearest_site = NearestSiteSummary(
                facility_name=closest_facility,
                city=closest_city,
                state=closest_state,
                distance_miles=round(closest_distance, 1),
            )

    registry_url = f"https://clinicaltrials.gov/study/{nct_id}"

    return TrialSummary(
        nct_id=nct_id,
        brief_title=brief_title,
        overall_status=overall_status,
        phase=phase_normalized,
        study_type=study_type,
        lead_sponsor=lead_sponsor_name,
        sponsor_class=sponsor_class,
        start_date=start_date,
        last_update_post_date=last_update,
        conditions=conditions,
        structured_eligibility=structured_eligibility,
        nearest_site=nearest_site,
        registry_url=registry_url,
    )


def normalize_study_detail(
    study: dict[str, Any],
    user_lat: float | None = None,
    user_lon: float | None = None,
) -> TrialDetail:
    """Normalize raw ClinicalTrials.gov study object into complete TrialDetail.

    Strictly complies with ADR 0065: personal investigator and coordinator contact
    details are omitted. Inquirers are routed via registry_contact_url.
    """
    protocol = study.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    sponsors_mod = protocol.get("sponsorCollaboratorsModule", {})
    conditions_mod = protocol.get("conditionsModule", {})
    interventions_mod = protocol.get("armsInterventionsModule", {})
    elig = protocol.get("eligibilityModule", {})
    contacts_loc = protocol.get("contactsLocationsModule", {})

    nct_id = ident.get("nctId", "NCT00000000")
    brief_title = ident.get("briefTitle", "Untitled Study")
    official_title = ident.get("officialTitle")
    overall_status = status.get("overallStatus", "UNKNOWN")
    last_known_status = status.get("lastKnownStatus")
    status_verified_date = status.get("statusVerifiedDate")
    study_type = design.get("studyType", "INTERVENTIONAL")
    expanded_access = bool(status.get("expandedAccessInfo", {}).get("hasExpandedAccess", False))

    raw_phases = design.get("phases")
    phase_raw, phase_normalized = normalize_phases(raw_phases)
    design_info = design.get("designInfo", {})

    masking_info = design_info.get("maskingInfo", {})
    masking = masking_info.get("masking") if isinstance(masking_info, dict) else None

    study_design = StudyDesign(
        phases=raw_phases if isinstance(raw_phases, list) else None,
        phase_raw=phase_raw,
        phase_normalized=phase_normalized,
        allocation=design_info.get("allocation"),
        primary_purpose=design_info.get("primaryPurpose"),
        intervention_model=design_info.get("interventionModel"),
        masking=masking,
    )

    enrollment_info = design.get("enrollmentInfo", {})
    enrollment = EnrollmentInfo(
        count=enrollment_info.get("count"),
        type=enrollment_info.get("type"),
    )

    dates = StudyDates(
        start_date=_extract_date_str(status.get("startDateStruct")),
        primary_completion_date=_extract_date_str(status.get("primaryCompletionDateStruct")),
        completion_date=_extract_date_str(status.get("completionDateStruct")),
        study_first_post_date=_extract_date_str(status.get("studyFirstPostDateStruct")),
        results_first_post_date=_extract_date_str(status.get("resultsFirstPostDateStruct")),
        last_update_post_date=_extract_date_str(status.get("lastUpdatePostDateStruct")),
    )

    lead_sponsor_dict = sponsors_mod.get("leadSponsor", {})
    lead_sponsor = SponsorDetail(
        name=lead_sponsor_dict.get("name", "Unknown Sponsor"),
        sponsor_class=lead_sponsor_dict.get("class"),
    )
    collaborators: list[SponsorDetail] = []
    for c in sponsors_mod.get("collaborators", []):
        if isinstance(c, dict) and "name" in c:
            collaborators.append(SponsorDetail(name=c["name"], sponsor_class=c.get("class")))

    resp_party = sponsors_mod.get("responsibleParty", {})
    resp_party_type = resp_party.get("type") if isinstance(resp_party, dict) else None

    sponsors = StudySponsors(
        lead_sponsor=lead_sponsor,
        collaborators=collaborators,
        responsible_party_type=resp_party_type,
    )

    # Conditions
    conditions: list[StudyCondition] = []
    for raw_cond in conditions_mod.get("conditions", []):
        if isinstance(raw_cond, str) and raw_cond.strip():
            c_name = raw_cond.strip()
            conditions.append(
                StudyCondition(
                    condition_raw=c_name,
                    condition_normalized=c_name.lower(),
                    condition_group=c_name,
                    mesh_id=None,
                    dementia_relevance_flag=None,
                )
            )

    # Interventions
    interventions: list[StudyIntervention] = []
    for item in interventions_mod.get("interventions", []):
        if isinstance(item, dict) and "name" in item:
            interventions.append(
                StudyIntervention(
                    intervention_type=str(item.get("type", "OTHER")).upper(),
                    intervention_name=str(item.get("name", "")),
                    description=item.get("description"),
                )
            )

    # Structured Eligibility
    min_age_raw = elig.get("minimumAge")
    max_age_raw = elig.get("maximumAge")
    min_age_years = parse_age_to_years(min_age_raw) if min_age_raw else None
    max_age_years = parse_age_to_years(max_age_raw) if max_age_raw else None
    age_not_stated = min_age_years is None and max_age_years is None
    sex = elig.get("sex", "ALL")
    std_ages = elig.get("stdAges")
    if not isinstance(std_ages, list):
        std_ages = ["ADULT", "OLDER_ADULT"] if not age_not_stated else []

    eligibility = StructuredEligibility(
        minimum_age_raw=min_age_raw,
        maximum_age_raw=max_age_raw,
        minimum_age_years=min_age_years,
        maximum_age_years=max_age_years,
        age_not_stated=age_not_stated,
        sex=sex,
        gender_based=elig.get("genderBased"),
        gender_description=elig.get("genderDescription"),
        accepts_healthy_volunteers=elig.get("healthyVolunteers"),
        std_age_groups=[str(g) for g in std_ages],
        criteria_text=elig.get("eligibilityCriteria"),
    )

    # Sites / Facilities (ADR 0065: excludes contacts and investigator personal details)
    sites: list[TrialSite] = []
    for loc in contacts_loc.get("locations", []):
        if not isinstance(loc, dict):
            continue
        facility = loc.get("facility") or "Unknown Facility"
        city = loc.get("city") or "Unknown City"
        state = loc.get("state")
        zip_code = loc.get("zip")
        country = loc.get("country") or "United States"
        loc_status = loc.get("status")

        coords: GeoPoint | None = None
        distance_mi: float | None = None
        geo = loc.get("geoPoint")
        if isinstance(geo, dict) and "lat" in geo and "lon" in geo:
            try:
                lat = float(geo["lat"])
                lon = float(geo["lon"])
                coords = GeoPoint(lat=lat, lon=lon)
                if user_lat is not None and user_lon is not None:
                    distance_mi = round(haversine_distance_miles(user_lat, user_lon, lat, lon), 1)
            except (ValueError, TypeError):
                coords = None

        sites.append(
            TrialSite(
                facility_name=facility,
                city=city,
                state=state,
                zip_code=zip_code,
                country=country,
                location_status=loc_status,
                coordinates=coords,
                distance_miles=distance_mi,
            )
        )

    # URLs per ADR 0065
    registry_url = f"https://clinicaltrials.gov/study/{nct_id}"
    registry_contact_url = f"https://clinicaltrials.gov/study/{nct_id}#contacts-and-locations"

    # Data Provenance
    now = datetime.now(UTC)
    provenance = DataProvenance(
        ingestion_run_id="live-api",
        snapshot_id="live-api",
        snapshot_date=now.strftime("%Y-%m-%d"),
        snapshot_timestamp_utc=now.isoformat(),
        source_json_hash=_sha256_study(study),
        record_quality_flag="ok",
        retrieved_at_utc=now.isoformat(),
        indication_profile_id="live",
    )

    return TrialDetail(
        nct_id=nct_id,
        brief_title=brief_title,
        official_title=official_title,
        overall_status=overall_status,
        last_known_status=last_known_status,
        status_verified_date=status_verified_date,
        study_type=study_type,
        has_expanded_access=expanded_access,
        study_design=study_design,
        enrollment=enrollment,
        dates=dates,
        sponsors=sponsors,
        conditions=conditions,
        interventions=interventions,
        eligibility=eligibility,
        sites=sites,
        registry_url=registry_url,
        registry_contact_url=registry_contact_url,
        provenance=provenance,
    )


def filter_and_sort_summaries(
    items: list[TrialSummary],
    min_age: int | None = None,
    max_age: int | None = None,
    sex: str | None = None,
    healthy_volunteers: bool | None = None,
    phase: str | None = None,
    sort: str | None = "-updated",
) -> list[TrialSummary]:
    """Filter and sort normalized summaries in-memory."""
    result: list[TrialSummary] = []

    for item in items:
        elig = item.structured_eligibility

        # Age bounds filtering (Issue #62: unstated bounds are never discarded)
        if min_age is not None:
            if not elig.age_not_stated and elig.maximum_age_years is not None:
                if elig.maximum_age_years < min_age:
                    continue

        if max_age is not None:
            if not elig.age_not_stated and elig.minimum_age_years is not None:
                if elig.minimum_age_years > max_age:
                    continue

        # Sex filter
        if sex is not None and sex.upper() != "ALL":
            if elig.sex.upper() not in ("ALL", sex.upper(), "UNKNOWN"):
                continue

        # Healthy volunteers filter
        if healthy_volunteers is not None:
            if elig.accepts_healthy_volunteers is not None:
                if elig.accepts_healthy_volunteers != healthy_volunteers:
                    continue

        # Phase filter
        if phase is not None and phase.strip():
            target_phase = phase.strip().upper()
            if target_phase not in item.phase.upper():
                continue

        result.append(item)

    # Sorting
    if sort == "distance":
        result.sort(
            key=lambda x: (
                x.nearest_site.distance_miles
                if x.nearest_site and x.nearest_site.distance_miles is not None
                else float("inf")
            )
        )
    elif sort == "-updated":
        result.sort(key=lambda x: x.last_update_post_date or "", reverse=True)
    elif sort == "updated":
        result.sort(key=lambda x: x.last_update_post_date or "")
    elif sort == "-start_date":
        result.sort(key=lambda x: x.start_date or "", reverse=True)
    elif sort == "start_date":
        result.sort(key=lambda x: x.start_date or "")

    return result
