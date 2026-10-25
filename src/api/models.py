"""Pydantic V2 models for the Clinical Trial Read API.

Conforms strictly to docs/api/openapi.yaml schema contracts and ADR 0065 privacy guardrails.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GeoPoint(BaseModel):
    """Geographic point coordinates."""

    model_config = ConfigDict(extra="ignore")

    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude")


class NearestSiteSummary(BaseModel):
    """Listed facility geographically closest to user query coordinates."""

    model_config = ConfigDict(extra="ignore")

    facility_name: str = Field(..., description="Facility name")
    city: str = Field(..., description="City")
    state: str | None = Field(default=None, description="State code or region")
    distance_miles: float | None = Field(
        default=None, description="Great-circle distance in miles from search coordinates"
    )


class StructuredEligibilitySummary(BaseModel):
    """Summary of core structured eligibility criteria per Issue #62."""

    model_config = ConfigDict(extra="ignore")

    minimum_age_years: float | None = Field(
        default=None, description="Lower participant age limit in years. Null when unstated."
    )
    maximum_age_years: float | None = Field(
        default=None, description="Upper participant age limit in years. Null when unstated."
    )
    age_not_stated: bool = Field(
        ..., description="Explicit flag indicating age restrictions are absent or unspecified."
    )
    sex: str = Field(
        ..., description="Allowed participant biological sex: ALL, FEMALE, MALE, UNKNOWN"
    )
    accepts_healthy_volunteers: bool | None = Field(
        default=None, description="Whether healthy volunteers are accepted."
    )
    std_age_groups: list[str] = Field(
        default_factory=list, description="Standard age cohorts (CHILD, ADULT, OLDER_ADULT)."
    )


class TrialSummary(BaseModel):
    """Compact trial summary for search listings."""

    model_config = ConfigDict(extra="ignore")

    nct_id: str = Field(..., pattern=r"^NCT\d{8}$", description="NCT Identifier")
    brief_title: str = Field(..., description="Protocol brief title")
    overall_status: str = Field(..., description="Recruitment status")
    phase: str = Field(..., description="Normalized phase (e.g. PHASE3, NOT_APPLICABLE)")
    study_type: str = Field(..., description="Study type (INTERVENTIONAL, OBSERVATIONAL)")
    lead_sponsor: str = Field(..., description="Lead sponsor organization")
    sponsor_class: str | None = Field(default=None, description="Sponsor category class")
    start_date: str | None = Field(default=None, description="Study start date (ISO)")
    last_update_post_date: str | None = Field(
        default=None, description="Most recent record update date (ISO)"
    )
    conditions: list[str] = Field(default_factory=list, description="Target conditions")
    structured_eligibility: StructuredEligibilitySummary = Field(
        ..., description="Eligibility summary"
    )
    nearest_site: NearestSiteSummary | None = Field(
        default=None, description="Closest participating site if coordinates supplied"
    )
    registry_url: str = Field(..., description="Canonical ClinicalTrials.gov study link")


class PaginationMetadata(BaseModel):
    """Pagination metadata for cursor-based keyset navigation."""

    model_config = ConfigDict(extra="ignore")

    next_cursor: str | None = Field(default=None, description="Opaque next page cursor")
    prev_cursor: str | None = Field(default=None, description="Opaque previous page cursor")
    limit: int = Field(..., ge=1, le=100, description="Items requested per page")
    total_count: int | None = Field(
        default=None, description="Total matching trials count when computed"
    )
    has_more: bool = Field(..., description="Whether additional results exist")


class TrialsListResponse(BaseModel):
    """Paginated collection of clinical trial summaries."""

    model_config = ConfigDict(extra="ignore")

    items: list[TrialSummary] = Field(default_factory=list, description="Trial summary items")
    pagination: PaginationMetadata = Field(..., description="Pagination metadata")


class StudyDesign(BaseModel):
    """Trial design characteristics."""

    model_config = ConfigDict(extra="ignore")

    phases: list[str] | None = Field(default=None, description="Raw phases array")
    phase_raw: str | None = Field(default=None, description="Raw joined phases")
    phase_normalized: str = Field(..., description="Standardized phase string")
    allocation: str | None = Field(default=None, description="Randomization allocation")
    primary_purpose: str | None = Field(default=None, description="Primary study purpose")
    intervention_model: str | None = Field(default=None, description="Intervention design model")
    masking: str | None = Field(default=None, description="Blinding/masking type")


class EnrollmentInfo(BaseModel):
    """Target participant enrollment."""

    model_config = ConfigDict(extra="ignore")

    count: int | None = Field(default=None, description="Participant count")
    type: str | None = Field(default=None, description="Count type: ESTIMATED or ACTUAL")


class StudyDates(BaseModel):
    """Significant protocol milestone dates."""

    model_config = ConfigDict(extra="ignore")

    start_date: str | None = Field(default=None, description="Start date (ISO)")
    primary_completion_date: str | None = Field(
        default=None, description="Primary completion date (ISO)"
    )
    completion_date: str | None = Field(default=None, description="Study completion date (ISO)")
    study_first_post_date: str | None = Field(
        default=None, description="Date first published to registry"
    )
    results_first_post_date: str | None = Field(
        default=None, description="Date results were first posted"
    )
    last_update_post_date: str | None = Field(
        default=None, description="Date of last sponsor update"
    )


class SponsorDetail(BaseModel):
    """Sponsor organization details."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(..., description="Sponsor name")
    sponsor_class: str | None = Field(
        default=None,
        serialization_alias="class",
        validation_alias="class",
        description="Sponsor class (INDUSTRY, NIH, etc.)",
    )


class StudySponsors(BaseModel):
    """Study lead sponsor and collaborators."""

    model_config = ConfigDict(extra="ignore")

    lead_sponsor: SponsorDetail = Field(..., description="Lead sponsor")
    collaborators: list[SponsorDetail] = Field(
        default_factory=list, description="Collaborating organizations"
    )
    responsible_party_type: str | None = Field(
        default=None, description="Responsible party classification"
    )


class StudyCondition(BaseModel):
    """Condition and taxonomy mapping."""

    model_config = ConfigDict(extra="ignore")

    condition_raw: str = Field(..., description="Raw condition name from registry")
    condition_normalized: str = Field(..., description="Normalized condition key")
    condition_group: str | None = Field(default=None, description="Standardized condition cluster")
    mesh_id: str | None = Field(default=None, description="MeSH descriptor ID")
    dementia_relevance_flag: bool | None = Field(
        default=None, description="Indication profile relevance flag"
    )


class StudyIntervention(BaseModel):
    """Investigational drug or intervention."""

    model_config = ConfigDict(extra="ignore")

    intervention_type: str = Field(..., description="Intervention type (DRUG, BIOLOGICAL, etc.)")
    intervention_name: str = Field(..., description="Intervention name")
    description: str | None = Field(default=None, description="Intervention details")


class StructuredEligibility(BaseModel):
    """Complete participant eligibility criteria model per Issue #62."""

    model_config = ConfigDict(extra="ignore")

    minimum_age_raw: str | None = Field(default=None, description="Raw minimum age string")
    maximum_age_raw: str | None = Field(default=None, description="Raw maximum age string")
    minimum_age_years: float | None = Field(default=None, description="Parsed minimum age in years")
    maximum_age_years: float | None = Field(default=None, description="Parsed maximum age in years")
    age_not_stated: bool = Field(
        ..., description="True when no age limits are specified in protocol."
    )
    sex: str = Field(..., description="Sex criteria: ALL, FEMALE, MALE, UNKNOWN")
    gender_based: bool | None = Field(
        default=None, description="Whether eligibility is gender-based"
    )
    gender_description: str | None = Field(
        default=None, description="Narrative description if gender-based"
    )
    accepts_healthy_volunteers: bool | None = Field(
        default=None, description="Whether healthy volunteers are accepted"
    )
    std_age_groups: list[str] = Field(
        default_factory=list, description="Standard age groups (CHILD, ADULT, OLDER_ADULT)"
    )
    criteria_text: str | None = Field(
        default=None, description="Full unstructured eligibility text block"
    )


class TrialSite(BaseModel):
    """Physical trial facility location.

    Strictly omits personal investigator contact details in accordance with ADR 0065.
    """

    model_config = ConfigDict(extra="ignore")

    facility_name: str = Field(..., description="Facility name")
    city: str = Field(..., description="City")
    state: str | None = Field(default=None, description="State abbreviation or region")
    zip_code: str | None = Field(default=None, description="Postal code")
    country: str = Field(..., description="Country name")
    location_status: str | None = Field(default=None, description="Site recruitment status")
    coordinates: GeoPoint | None = Field(default=None, description="Geocoded coordinates")
    distance_miles: float | None = Field(
        default=None, description="Proximity distance from search coordinates"
    )


class DataProvenance(BaseModel):
    """Metadata regarding data provenance and ingestion snapshot."""

    model_config = ConfigDict(extra="ignore")

    ingestion_run_id: str = Field(..., description="Ingestion run snapshot identifier")
    snapshot_id: str = Field(..., description="Snapshot identifier")
    snapshot_date: str = Field(..., description="Snapshot date (YYYY-MM-DD)")
    snapshot_timestamp_utc: str = Field(..., description="Snapshot timestamp (ISO UTC)")
    source_json_hash: str = Field(..., description="SHA-256 digest of unmodified study JSON")
    record_quality_flag: str = Field(..., description="Quality gate classification (e.g. ok)")
    retrieved_at_utc: str | None = Field(default=None, description="Retrieval timestamp (ISO UTC)")
    indication_profile_id: str = Field(..., description="Indication profile ID")


class TrialDetail(BaseModel):
    """Complete clinical trial protocol detail model.

    In strict adherence to ADR 0065, no personal investigator or coordinator contact PII
    is exposed; inquiries are directed to the canonical registry URL via registry_contact_url.
    """

    model_config = ConfigDict(extra="ignore")

    nct_id: str = Field(..., pattern=r"^NCT\d{8}$", description="NCT Identifier")
    brief_title: str = Field(..., description="Brief study title")
    official_title: str | None = Field(default=None, description="Formal protocol title")
    overall_status: str = Field(..., description="Recruitment status")
    last_known_status: str | None = Field(
        default=None, description="Fallback status when updates lapse"
    )
    status_verified_date: str | None = Field(
        default=None, description="Verification date from sponsor"
    )
    study_type: str = Field(..., description="Study type (INTERVENTIONAL, OBSERVATIONAL)")
    has_expanded_access: bool = Field(
        default=False, description="Whether expanded access is available"
    )
    study_design: StudyDesign = Field(..., description="Study design")
    enrollment: EnrollmentInfo = Field(..., description="Participant enrollment")
    dates: StudyDates = Field(..., description="Milestone dates")
    sponsors: StudySponsors = Field(..., description="Sponsors and collaborators")
    conditions: list[StudyCondition] = Field(default_factory=list, description="Target conditions")
    interventions: list[StudyIntervention] = Field(
        default_factory=list, description="Study interventions"
    )
    eligibility: StructuredEligibility = Field(
        ..., description="Structured eligibility criteria (Issue #62)"
    )
    sites: list[TrialSite] = Field(
        default_factory=list, description="Participating sites (ADR 0065 compliant)"
    )
    registry_url: str = Field(..., description="Canonical ClinicalTrials.gov study URL")
    registry_contact_url: str = Field(
        ..., description="Deep link to official registry contacts section (ADR 0065)"
    )
    provenance: DataProvenance = Field(..., description="Data snapshot provenance")


class InvalidParam(BaseModel):
    """Field-specific validation failure detail for RFC 7807 problem payloads."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., description="Parameter or field name causing validation error")
    reason: str = Field(..., description="Reason for validation failure")
    value: Any = Field(default=None, description="Rejected value")


class ProblemDetail(BaseModel):
    """RFC 7807 compliant problem details representation."""

    model_config = ConfigDict(extra="ignore")

    type: str = Field(..., description="URI reference identifying problem type")
    title: str = Field(..., description="Short summary of the problem type")
    status: int = Field(..., description="HTTP status code")
    detail: str = Field(..., description="Human-readable explanation of problem occurrence")
    instance: str = Field(..., description="URI reference of the request instance")
    code: str | None = Field(default=None, description="Machine-readable error category code")
    invalid_params: list[InvalidParam] | None = Field(
        default=None, description="Optional field-level errors"
    )


# Alias for compatibility with openapi.yaml schema naming
ProblemDetails = ProblemDetail
