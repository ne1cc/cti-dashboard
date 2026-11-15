# Read API Source Field Traceability Matrix

This document establishes the end-to-end data lineage and transformation rules mapping each field in the Read API contract ([OpenAPI Specification](file:///docs/api/openapi.yaml)) back through the canonical registry-grain Gold dimensional marts, Silver normalized Parquet entities (`registry_*` and legacy `silver_trials` parity aliases), and upstream raw ClinicalTrials.gov API v2 payloads.

> **Registry-Grain Cutover (Phase 4d):** API warehouse readers now query canonical registry-grain models (`dim_registry_trial`, `dim_registry_condition`, `bridge_registry_trial_condition`, `bridge_preset_trial`, `fct_registry_trial_snapshot`, `fct_registry_trial_site`, `stg_registry_*`, `int_registry_preset_condition_mapping`) keyed by `registry_trial_key = md5(nct_id)`. Public API request and response fields remain unchanged via preset compatibility mappings (`indication_profile_id` maps to `preset_id`). Pre-cutover DuckDB warehouses on the old `dim_trial` grain require a full rebuild to materialize the `*_registry_*` tables.

Cross-references:
- [Data Dictionary](file:///docs/data_dictionary.md)
- [Architecture](file:///docs/architecture.md)
- [ADR 0061: Server-Rendered Patient Pages & Dual Surfaces](file:///docs/architecture.md#adr-0061)
- [ADR 0065: Central Contacts & Study Inquiries Policy](file:///docs/clinical_interpretation_guardrails.md)
- [Issue #62: Structured Eligibility Fields](file:///docs/data_dictionary.md#silver_trials)

---

## 1. Study Protocol & Summary (`/v1/trials` and `/v1/trials/{nct_id}`)

| API Field | Upstream ClinicalTrials.gov v2 Source | Silver Entity & Column | Gold Mart / Staging & Column | Transformation & Governance Rules |
|---|---|---|---|---|
| `nct_id` | `protocolSection.identificationModule.nctId` | `registry_trials.nct_id` (`silver_trials.nct_id`) | `dim_registry_trial.nct_id` (legacy `dim_trial.nct_id`) | Validated against regex `^NCT\d{8}$`. Immutable natural key. |
| `brief_title` | `protocolSection.identificationModule.briefTitle` | `registry_trials.brief_title` | `dim_registry_trial.current_brief_title` | Preserved verbatim for user display. |
| `official_title` | `protocolSection.identificationModule.officialTitle` | `registry_trials.official_title` | `stg_registry_trials.official_title` | Full formal protocol title; nullable. |
| `overall_status` | `protocolSection.statusModule.overallStatus` | `registry_trials.overall_status` | `dim_registry_trial.current_overall_status` | Normalized enum: `RECRUITING`, `COMPLETED`, etc. |
| `last_known_status` | `protocolSection.statusModule.lastKnownStatus` | `registry_trials.last_known_status` | `stg_registry_trials.last_known_status` | Fallback status when registry updates lapse. |
| `status_verified_date` | `protocolSection.statusModule.statusVerifiedDate` | `registry_trials.status_verified_date` | `stg_registry_trials.status_verified_date_raw` | Lenient string (YYYY or YYYY-MM) as reported by sponsor. |
| `study_type` | `protocolSection.designModule.studyType` | `registry_trials.study_type` | `dim_registry_trial.current_study_type` | Interventional vs observational. |
| `has_expanded_access` | `protocolSection.statusModule.expandedAccessInfo.hasExpandedAccess` | `registry_trials.expanded_access_info` | `stg_registry_trials.has_expanded_access` | Boolean flag. |
| `phase` / `phase_normalized` | `protocolSection.designModule.phases[]` | `registry_trials.phase_normalized` | `dim_registry_trial.current_phase` | Multi-phase joined with `/`, `NA` normalized to `NOT_APPLICABLE`, null to `UNKNOWN`. |
| `phase_raw` | `protocolSection.designModule.phases[]` | `registry_trials.phase_raw` | `stg_registry_trials.phase_raw` | Preserved raw array representation from registry. |
| `allocation` | `protocolSection.designModule.designInfo.allocation` | `registry_trials.allocation` | `stg_registry_trials.allocation` | `RANDOMIZED`, `NON_RANDOMIZED`, or null. |
| `primary_purpose` | `protocolSection.designModule.designInfo.primaryPurpose` | `registry_trials.primary_purpose` | `stg_registry_trials.primary_purpose` | `TREATMENT`, `PREVENTION`, `DIAGNOSTIC`, etc. |
| `enrollment.count` | `protocolSection.designModule.enrollmentInfo.count` | `registry_trials.enrollment_count` | `dim_registry_trial.enrollment_count` | Parsed integer. |
| `enrollment.type` | `protocolSection.designModule.enrollmentInfo.type` | `registry_trials.enrollment_type` | `dim_registry_trial.enrollment_type` | `ESTIMATED` or `ACTUAL`. |
| `lead_sponsor` | `protocolSection.sponsorCollaboratorsModule.leadSponsor.name` | `registry_trials.lead_sponsor_name` | `dim_registry_trial.current_lead_sponsor` | Organization name. |
| `sponsor_class` | `protocolSection.sponsorCollaboratorsModule.leadSponsor.class` | `registry_trial_sponsors.sponsor_class` | `stg_registry_trial_sponsors.sponsor_class` | `INDUSTRY`, `NIH`, `FED`, `OTHER`. |
| `start_date` | `protocolSection.statusModule.startDateStruct.date` | `registry_trials.start_date` | `dim_registry_trial.start_date` | Parsed date via `parse_partial_date` macro. |
| `primary_completion_date` | `protocolSection.statusModule.primaryCompletionDateStruct.date` | `registry_trials.primary_completion_date` | `dim_registry_trial.primary_completion_date` | Parsed date via `parse_partial_date` macro. |
| `completion_date` | `protocolSection.statusModule.completionDateStruct.date` | `registry_trials.completion_date` | `dim_registry_trial.completion_date` | Parsed date via `parse_partial_date` macro. |
| `study_first_post_date` | `protocolSection.statusModule.studyFirstPostDateStruct.date` | `registry_trials.study_first_post_date` | `dim_registry_trial.study_first_post_date` | Date first published to ClinicalTrials.gov. |
| `results_first_post_date` | `protocolSection.statusModule.resultsFirstPostDateStruct.date` | `registry_trials.results_first_post_date` | `stg_registry_trials.results_first_post_date` | Nullable date of first results publication. |
| `last_update_post_date` | `protocolSection.statusModule.lastUpdatePostDateStruct.date` | `registry_trials.last_update_post_date` | `stg_registry_trials.last_update_post_date` | Most recent sponsor record update date. |
| `registry_url` | Derived | N/A | `dim_registry_trial.registry_url` | Constructed canonical URL: `'https://clinicaltrials.gov/study/' \|\| nct_id`. |
| `registry_contact_url` | Derived per **ADR 0065** | N/A | Derived in API layer | Mandated by ADR 0065: `'https://clinicaltrials.gov/study/' \|\| nct_id \|\| '#contacts-and-locations'`. Directs participants to verified official contacts without scraping or persisting personal PII. |
| `provenance.indication_profile_id` | Preset YAML membership compatibility | `bridge_preset_trial.preset_id` | `bridge_preset_trial.preset_id` | Public compatibility identifier resolved from active non-unassigned preset membership (falling back to `'unassigned'` or `'warehouse'`). |

---

## 2. Structured Eligibility Criteria (Issue #62)

Issue #62 formalizes structured participant criteria and enforces explicit unstated age handling to prevent silently dropping trials without explicit age limits.

| API Field | Upstream CTG v2 Field | Silver Column | Warehouse Model & Column | Business Rule & Derivation |
|---|---|---|---|---|
| `minimum_age_raw` | `protocolSection.eligibilityModule.minimumAge` | `registry_trials.minimum_age` | `dim_registry_trial.minimum_age_raw` / `stg_registry_trials.minimum_age` | Raw string (e.g. "18 Years", "6 Months"). |
| `maximum_age_raw` | `protocolSection.eligibilityModule.maximumAge` | `registry_trials.maximum_age` | `dim_registry_trial.maximum_age_raw` / `stg_registry_trials.maximum_age` | Raw string (e.g. "85 Years"). |
| `minimum_age_years` | Derived from `minimumAge` | Derived in silver/gold | `dim_registry_trial.min_age_years` / `stg_registry_trials.minimum_age_years` | Standardized decimal years via `parse_age_years` macro ("18 Years" -> 18.0; "6 Months" -> 0.5; null/unstated -> null). |
| `maximum_age_years` | Derived from `maximumAge` | Derived in silver/gold | `dim_registry_trial.max_age_years` / `stg_registry_trials.maximum_age_years` | Standardized decimal years via `parse_age_years` macro. |
| `age_not_stated` | Derived | N/A | Derived in Gold / API layer | Boolean: `minimum_age_years IS NULL AND maximum_age_years IS NULL`. Displayed to prevent silent dropping during filtering. |
| `sex` | `protocolSection.eligibilityModule.sex` | `registry_trials.sex` | `dim_registry_trial.sex_eligibility` / `stg_registry_trials.sex` | `ALL`, `FEMALE`, `MALE`, or `UNKNOWN`. |
| `gender_based` | `protocolSection.eligibilityModule.genderBased` | `registry_trials.gender_based` | `dim_registry_trial.gender_based` | Boolean flag. |
| `gender_description` | `protocolSection.eligibilityModule.genderDescription` | `registry_trials.gender_description` | `dim_registry_trial.gender_description` | Narrative description when sex eligibility is gender-based. |
| `accepts_healthy_volunteers` | `protocolSection.eligibilityModule.healthyVolunteers` | `registry_trials.healthy_volunteers` | `dim_registry_trial.accepts_healthy_volunteers` | Boolean flag. |
| `std_age_groups` | `protocolSection.eligibilityModule.stdAges[]` | `registry_trials.std_age_groups` | `dim_registry_trial.std_age_groups` | Array of `CHILD`, `ADULT`, `OLDER_ADULT`. |
| `criteria_text` | `protocolSection.eligibilityModule.eligibilityCriteria` | `registry_trials.eligibility_criteria_text` | `stg_registry_trials.eligibility_criteria_text` | Unstructured inclusion and exclusion text block. |

---

## 2b. Participant-Relevant Design Data (arms, masking, stop reason, timing)

All rows below store registry text verbatim — never generated, paraphrased or
interpreted. Additive on the `/v1/trials/{nct_id}` detail payload.

| API Field | Upstream CTG v2 Field | Silver Column | Warehouse Model & Column | Business Rule & Derivation |
|---|---|---|---|---|
| `arms[]` | `protocolSection.armsInterventionsModule.armGroups[]` (`label`, `type`, `description`, `interventionNames`) | `registry_trial_arms.arm_label` / `arm_type` / `arm_description` / `intervention_names` | `stg_registry_trial_arms` (grain: NCT × snapshot × `arm_ordinal`) | Arm list preserved in registry order via zero-based `arm_ordinal`. `arm_type` includes `PLACEBO_COMPARATOR`; null when the registry omits it. |
| `has_placebo_arm` | Derived from `armGroups[].type` | `registry_trials.has_placebo_arm` | `dim_registry_trial.has_placebo_arm` | True when any arm group declares type `PLACEBO_COMPARATOR`. |
| `study_design.masking` | `protocolSection.designModule.designInfo.maskingInfo.masking` | `registry_trials.masking` | `dim_registry_trial.current_masking` | Masking level (`NONE`, `SINGLE`, `DOUBLE`, `TRIPLE`, `QUADRUPLE`); null when unstated. |
| `study_design.intervention_model` | `protocolSection.designModule.designInfo.interventionModel` | `registry_trials.intervention_model` | `dim_registry_trial.current_intervention_model` | `PARALLEL`, `SINGLE_GROUP`, `FACTORIAL`, etc. |
| `why_stopped` | `protocolSection.statusModule.whyStopped` | `registry_trials.why_stopped` | `dim_registry_trial.why_stopped` | Verbatim registry stop reason; populated for terminated/withdrawn/suspended trials. |
| `brief_summary` | `protocolSection.descriptionModule.briefSummary` | `registry_trials.brief_summary` | `dim_registry_trial.brief_summary` | Verbatim registry-provided brief summary; never generated. |
| `primary_outcome_time_frame` | `protocolSection.outcomesModule.primaryOutcomes[0].timeFrame` | `registry_trials.primary_outcome_time_frame` | `dim_registry_trial.primary_outcome_time_frame` | Verbatim timing of the first primary outcome (e.g. "78 weeks"). |
| `dates.start_date` / `dates.primary_completion_date` | `protocolSection.statusModule.startDateStruct.date` / `primaryCompletionDateStruct.date` | `registry_trials.start_date` / `primary_completion_date` | `dim_registry_trial.start_date` / `primary_completion_date` | Already covered in section 1; listed here as the participant-facing "how long?" dates. |

---

## 3. Study Locations & Sites (ADR 0065 Privacy Guardrails)

In strict adherence to **ADR 0065**, facility-level and central investigator names, personal email addresses, and personal telephone numbers are **deliberately excluded** from the data pipeline and read API models.

| API Field | Upstream CTG v2 Field | Silver Column | Warehouse Model & Column | Governance & Redaction Notes |
|---|---|---|---|---|
| `facility_name` | `locations[].facility` | `registry_trial_locations.facility_name` | `fct_registry_trial_site.facility_name` / `stg_registry_trial_locations.facility_name` | Public facility name. Note: Facility names are not unique site IDs. |
| `city` | `locations[].city` | `registry_trial_locations.city` | `fct_registry_trial_site.city` / `stg_registry_trial_locations.city` | Listed city. |
| `state` | `locations[].state` | `registry_trial_locations.state_normalized` | `fct_registry_trial_site.state_normalized` / `stg_registry_trial_locations.state_normalized` | Normalized 2-letter postal abbreviation via `config/geography_rules.yml`. |
| `zip_code` | `locations[].zip` | `registry_trial_locations.zip_code` | `fct_registry_trial_site.zip_code` / `stg_registry_trial_locations.zip_code` | Postal code. |
| `country` | `locations[].country` | `registry_trial_locations.country` | `stg_registry_trial_locations.country` | ISO country name. |
| `location_status` | `locations[].status` | `registry_trial_locations.location_status` | `fct_registry_trial_site.location_status` / `stg_registry_trial_locations.location_status` | Site recruitment status (`RECRUITING`, etc.). |
| `coordinates.lat` | `locations[].geoPoint.lat` | `registry_trial_locations.latitude` | `stg_registry_trial_locations.latitude` | Geocoded latitude. |
| `coordinates.lon` | `locations[].geoPoint.lon` | `registry_trial_locations.longitude` | `stg_registry_trial_locations.longitude` | Geocoded longitude. |
| `distance_miles` | Calculated | N/A | Calculated in API layer | Computed on-the-fly using great-circle Haversine formula from client's query `lat` and `lon`. |
| *Investigator PII* | `locations[].contacts[]` | **REDACTED** (`stg_trial_contacts` where 1=0) | **NONE** | **EXCLUDED PER ADR 0065**. No individual person names, phone numbers, or emails are persisted or served. |

---

## 4. Conditions & Taxonomy (`/v1/conditions`)

| API Field | Upstream CTG v2 Field | Silver Column | Warehouse Model & Column | Transformation & Ontology Notes |
|---|---|---|---|---|
| `condition_name` | `conditionsModule.conditions[]` | `registry_trial_conditions.condition_raw` | `dim_registry_condition.condition_display_name` | Registry condition string. |
| `condition_normalized` | Derived | `registry_trial_conditions.condition_normalized` | `dim_registry_condition.condition_normalized` | Lowercased, punctuation-stripped matching key. |
| `condition_group` | Config mapping | `int_registry_preset_condition_mapping.condition_group` | `dim_registry_condition.condition_group` / `int_registry_preset_condition_mapping.condition_group` | Preset-scoped taxonomy cluster via `config/condition_taxonomy*.yml` or MeSH browse term. |
| `mesh_id` | MeSH heading | `registry_trial_condition_mesh.mesh_id` | `dim_registry_condition.mesh_id` | Unique Medical Subject Headings descriptor ID (e.g. D000544). |
| `mesh_tree_code` | MeSH Tree Number | External ontology reference | N/A | Primary MeSH hierarchical tree location code. |
| `synonyms` | NLM MeSH term list | `src/api/resources/mesh_conditions.json` | N/A | Array of clinical synonyms and common patient search phrases. |
| `trial_count` | Aggregated | N/A | `dim_registry_condition.trial_count` (or `bridge_registry_trial_condition` × `bridge_preset_trial` when `indication_profile_id` is supplied) | Distinct NCT IDs associated with condition across the registry or within the requested preset scope (`unassigned` for non-preset trials). |
| `active_trial_count` | Aggregated | N/A | `bridge_registry_trial_condition` × `dim_registry_trial` (filtered by `bridge_preset_trial` when scoped) | Distinct NCT IDs in recruiting or active status. |
| `has_children` | MeSH Tree Hierarchy | External ontology reference | N/A | True if sub-branches / narrower conditions exist in MeSH. |
| `broader` | MeSH Parent Nodes | External ontology reference | N/A | Array of parent MeSH concepts enabling users to widen search. |
| `narrower` | MeSH Child Nodes | External ontology reference | N/A | Array of child MeSH concepts enabling users to narrow search. |

For ontology provenance, licensing, and terms of use, see `docs/mesh_licensing.md`.

---

## 5. Pairwise Study Similarity (`/v1/trials/{nct_id}/related`)

Computed dynamically at registry grain (`src/api/sql/related.sql`) from `dim_registry_trial`, `fct_registry_trial_site`, `stg_registry_*`, `int_registry_preset_condition_mapping`, and `similarity_score_weights` (with legacy `mart_trial_similarity` fallback during cutover). Exposes objective comparability without clinical equivalence claims.

| API Field | Warehouse Source & Column | Scoring Weight | Mathematical Derivation & Logic |
|---|---|---|---|
| `similarity_score` | Computed in `src/api/sql/related.sql` (legacy `mart_trial_similarity.similarity_score`) | 1.0 (Sum) | Weighted sum: $\sum (w_c \times \text{matched}_c)$, rounded to 4 decimals. |
| `similarity_rank` | `dense_rank()` over `similarity_score desc, nct_id asc` | N/A | Ranked up to top 25 registry-wide peers (`limit` capped at 25). |
| `same_condition` | `int_registry_preset_condition_mapping` keyed by `(preset_id, condition_group)` | 0.25 | 1 if intersection of trial `(preset_id, condition_group)` pairs is non-empty, else 0. |
| `same_phase` | `dim_registry_trial.current_phase` | 0.20 | 1 if normalized phases match exactly, else 0. |
| `geography_overlap` | `fct_registry_trial_site.state_normalized` | 0.15 | 1 if trials share one or more U.S. states in latest site snapshot, else 0. |
| `intervention_type_overlap` | `stg_registry_trial_interventions.intervention_type` | 0.15 | 1 if trials share intervention types (e.g. Drug, Biological), else 0. |
| `study_design_match` | `stg_registry_trials` (`study_type`, `allocation`, `primary_purpose`) | 0.10 | 1 if `study_type`, `allocation`, and `primary_purpose` match, else 0. |
| `eligibility_compatible` | `dim_registry_trial` (sex, healthy volunteers, min/max age years) | 0.085 | 1 if sex is compatible, healthy volunteers match, and age windows overlap. |
| `enrollment_band_match` | `dim_registry_trial.enrollment_count` | 0.05 | 1 if enrollment sizes fall in the same tier (small <50, medium 50-200, large >200). |
| `explanation_summary` | Derived in SQL | N/A | Semicolon-delimited explanation of active matching factors. |

---

## 6. Geographic Access & Competition (`/v1/geo/access`)

Derived from Gold marts `mart_condition_geography_trends` and `mart_site_overlap`.

| API Field | Warehouse Mart & Column | Derivation & Calculation Rule |
|---|---|---|
| `state` | `mart_condition_geography_trends.state_normalized` | 2-letter U.S. state code. |
| `condition_group` | `mart_condition_geography_trends.condition_group` | Indication taxonomy group. |
| `active_sites_count` | `mart_condition_geography_trends.active_sites_count` | Count of distinct physical facilities with recruiting studies. |
| `recruiting_trials_count` | `mart_condition_geography_trends.active_trials_count` | Count of active recruiting trials in the state for the condition. |
| `sponsor_hhi` | `int_sponsor_concentration.sponsor_hhi` | Sum of squared sponsor market shares: $\sum (\text{share}_i \times 100)^2$. Range: 0 to 10,000. |
| `top_sponsor_share` | `int_sponsor_concentration.top_sponsor_share` | Market share of largest sponsor by active study arms ($0.0 - 1.0$). |
| `competition_intensity_tier` | Derived from HHI & site density | Categorical tier: `LOW` (HHI < 1500), `MODERATE` (1500-2500), `HIGH` (>2500), `SATURATED` (>4000 with high density). |
| `access_gap_index` | Derived metric | Deficit score based on facility count relative to population center density ($0.0 - 1.0$). |
| `travel_burden_summary` | Synthetic/Census integration | Estimated miles to nearest active recruiting facility for state population. |

---

## 7. Metadata, Provenance & Freshness (`/v1/meta/freshness`)

| API Field | Ingestion & Manifest Source | Warehouse Staging & Model | Description |
|---|---|---|---|
| `latest_snapshot_id`, `latest_ingestion_run_id` | `.freshness.json` `last_success.profiles.<profile>.run_id` | N/A | Ingestion run that the warehouse currently serves. |
| `latest_snapshot_date`, `latest_snapshot_timestamp_utc` | `.freshness.json` `captured_at_utc` | N/A | Capture time of that run. |
| `source_api` | Constant | N/A | Upstream source name ("ClinicalTrials.gov API v2"). |
| `source_api_version` | `.freshness.json` `last_success.source_api_version` | N/A | Registry API version ("v2"). |
| `total_studies_tracked`, `row_counts` | `.freshness.json` `row_counts` | `dim_registry_trial` (or legacy `dim_trial`), `fct_registry_trial_snapshot` | Per-preset/profile mart row counts recorded at refresh time (`total_studies_tracked` prefers `dim_registry_trial` with `dim_trial` fallback). |
| `last_success_at_utc` | `.freshness.json` `last_success.pipeline_completed_at_utc` | N/A | When the latest verified refresh completed. |
| `reconciliation` | `.freshness.json` `last_success.reconciliation` | N/A | Bronze/silver/warehouse reconciliation check totals. |
| `system_status` | Derived | N/A | `HEALTHY`, `STALE` (capture older than `CTI_FRESHNESS_MAX_AGE_DAYS`) or `DEGRADED` (refresh running). |
