# Read API Source Field Traceability Matrix

This document establishes the end-to-end data lineage and transformation rules mapping each field in the Read API contract ([OpenAPI Specification](file:///docs/api/openapi.yaml)) back through the Gold dimensional marts, Silver normalized Parquet entities, and upstream raw ClinicalTrials.gov API v2 payloads.

Cross-references:
- [Data Dictionary](file:///docs/data_dictionary.md)
- [Architecture](file:///docs/architecture.md)
- [ADR 0061: Server-Rendered Patient Pages & Dual Surfaces](file:///docs/architecture.md#adr-0061)
- [ADR 0065: Central Contacts & Study Inquiries Policy](file:///docs/clinical_interpretation_guardrails.md)
- [Issue #62: Structured Eligibility Fields](file:///docs/data_dictionary.md#silver_trials)

---

## 1. Study Protocol & Summary (`/v1/trials` and `/v1/trials/{nct_id}`)

| API Field | Upstream ClinicalTrials.gov v2 Source | Silver Entity & Column | Gold Mart & Column | Transformation & Governance Rules |
|---|---|---|---|---|
| `nct_id` | `protocolSection.identificationModule.nctId` | `silver_trials.nct_id` | `dim_trial.nct_id` | Validated against regex `^NCT\d{8}$`. Immutable natural key. |
| `brief_title` | `protocolSection.identificationModule.briefTitle` | `silver_trials.brief_title` | `dim_trial.current_brief_title` | Preserved verbatim for user display. |
| `official_title` | `protocolSection.identificationModule.officialTitle` | `silver_trials.official_title` | `stg_trials.official_title` | Full formal protocol title; nullable. |
| `overall_status` | `protocolSection.statusModule.overallStatus` | `silver_trials.overall_status` | `dim_trial.current_overall_status` | Normalized enum: `RECRUITING`, `COMPLETED`, etc. |
| `last_known_status` | `protocolSection.statusModule.lastKnownStatus` | `silver_trials.last_known_status` | `stg_trials.last_known_status` | Fallback status when registry updates lapse. |
| `status_verified_date` | `protocolSection.statusModule.statusVerifiedDate` | `silver_trials.status_verified_date` | `stg_trials.status_verified_date_raw` | Lenient string (YYYY or YYYY-MM) as reported by sponsor. |
| `study_type` | `protocolSection.designModule.studyType` | `silver_trials.study_type` | `dim_trial.current_study_type` | Interventional vs observational. |
| `has_expanded_access` | `protocolSection.statusModule.expandedAccessInfo.hasExpandedAccess` | `silver_trials.expanded_access_info` | `stg_trials.has_expanded_access` | Boolean flag. |
| `phase` / `phase_normalized` | `protocolSection.designModule.phases[]` | `silver_trials.phase_normalized` | `dim_trial.current_phase` | Multi-phase joined with `/`, `NA` normalized to `NOT_APPLICABLE`, null to `UNKNOWN`. |
| `phase_raw` | `protocolSection.designModule.phases[]` | `silver_trials.phase_raw` | `stg_trials.phase_raw` | Preserved raw array representation from registry. |
| `allocation` | `protocolSection.designModule.designInfo.allocation` | `silver_trials.allocation` | `stg_trials.allocation` | `RANDOMIZED`, `NON_RANDOMIZED`, or null. |
| `primary_purpose` | `protocolSection.designModule.designInfo.primaryPurpose` | `silver_trials.primary_purpose` | `stg_trials.primary_purpose` | `TREATMENT`, `PREVENTION`, `DIAGNOSTIC`, etc. |
| `enrollment.count` | `protocolSection.designModule.enrollmentInfo.count` | `silver_trials.enrollment_count` | `dim_trial.enrollment_count` | Parsed integer. |
| `enrollment.type` | `protocolSection.designModule.enrollmentInfo.type` | `silver_trials.enrollment_type` | `dim_trial.enrollment_type` | `ESTIMATED` or `ACTUAL`. |
| `lead_sponsor` | `protocolSection.sponsorCollaboratorsModule.leadSponsor.name` | `silver_trials.lead_sponsor_name` | `dim_trial.current_lead_sponsor` | Organization name. |
| `sponsor_class` | `protocolSection.sponsorCollaboratorsModule.leadSponsor.class` | `silver_trial_sponsors.sponsor_class` | `dim_sponsor.sponsor_class` | `INDUSTRY`, `NIH`, `FED`, `OTHER`. |
| `start_date` | `protocolSection.statusModule.startDateStruct.date` | `silver_trials.start_date` | `dim_trial.start_date` | Parsed date via `parse_partial_date` macro. |
| `primary_completion_date` | `protocolSection.statusModule.primaryCompletionDateStruct.date` | `silver_trials.primary_completion_date` | `dim_trial.primary_completion_date` | Parsed date via `parse_partial_date` macro. |
| `completion_date` | `protocolSection.statusModule.completionDateStruct.date` | `silver_trials.completion_date` | `dim_trial.completion_date` | Parsed date via `parse_partial_date` macro. |
| `study_first_post_date` | `protocolSection.statusModule.studyFirstPostDateStruct.date` | `silver_trials.study_first_post_date` | `dim_trial.study_first_post_date` | Date first published to ClinicalTrials.gov. |
| `results_first_post_date` | `protocolSection.statusModule.resultsFirstPostDateStruct.date` | `silver_trials.results_first_post_date` | `stg_trials.results_first_post_date` | Nullable date of first results publication. |
| `last_update_post_date` | `protocolSection.statusModule.lastUpdatePostDateStruct.date` | `silver_trials.last_update_post_date` | `stg_trials.last_update_post_date` | Most recent sponsor record update date. |
| `registry_url` | Derived | N/A | `dim_trial.registry_url` | Constructed canonical URL: `'https://clinicaltrials.gov/study/' \|\| nct_id`. |
| `registry_contact_url` | Derived per **ADR 0065** | N/A | Derived in API layer | Mandated by ADR 0065: `'https://clinicaltrials.gov/study/' \|\| nct_id \|\| '#contacts-and-locations'`. Directs participants to verified official contacts without scraping or persisting personal PII. |

---

## 2. Structured Eligibility Criteria (Issue #62)

Issue #62 formalizes structured participant criteria and enforces explicit unstated age handling to prevent silently dropping trials without explicit age limits.

| API Field | Upstream CTG v2 Field | Silver Column | Warehouse Model & Column | Business Rule & Derivation |
|---|---|---|---|---|
| `minimum_age_raw` | `protocolSection.eligibilityModule.minimumAge` | `silver_trials.minimum_age` | `stg_trials.minimum_age` | Raw string (e.g. "18 Years", "6 Months"). |
| `maximum_age_raw` | `protocolSection.eligibilityModule.maximumAge` | `silver_trials.maximum_age` | `stg_trials.maximum_age` | Raw string (e.g. "85 Years"). |
| `minimum_age_years` | Derived from `minimumAge` | Derived in silver/gold | `int_trial_comparability_features.minimum_age_years` | Standardized decimal years via `parse_age_years` macro ("18 Years" -> 18.0; "6 Months" -> 0.5; null/unstated -> null). |
| `maximum_age_years` | Derived from `maximumAge` | Derived in silver/gold | `int_trial_comparability_features.maximum_age_years` | Standardized decimal years via `parse_age_years` macro. |
| `age_not_stated` | Derived | N/A | Derived in Gold / API layer | Boolean: `minimum_age_years IS NULL AND maximum_age_years IS NULL`. Displayed to prevent silent dropping during filtering. |
| `sex` | `protocolSection.eligibilityModule.sex` | `silver_trials.sex` | `stg_trials.sex` | `ALL`, `FEMALE`, `MALE`, or `UNKNOWN`. |
| `gender_based` | `protocolSection.eligibilityModule.genderBased` | `silver_trials.gender_based` | `stg_trials.gender_based` | Boolean flag. |
| `gender_description` | `protocolSection.eligibilityModule.genderDescription` | `silver_trials.gender_description` | `stg_trials.gender_description` | Narrative description when sex eligibility is gender-based. |
| `accepts_healthy_volunteers` | `protocolSection.eligibilityModule.healthyVolunteers` | `silver_trials.healthy_volunteers` | `stg_trials.healthy_volunteers` | Boolean flag. |
| `std_age_groups` | `protocolSection.eligibilityModule.stdAges[]` | `silver_trials.std_age_groups` | `stg_trials.std_age_groups` | Array of `CHILD`, `ADULT`, `OLDER_ADULT`. |
| `criteria_text` | `protocolSection.eligibilityModule.eligibilityCriteria` | `silver_trials.eligibility_criteria_text` | `stg_trials.eligibility_criteria_text` | Unstructured inclusion and exclusion text block. |

---

## 2b. Participant-Relevant Design Data (arms, masking, stop reason, timing)

All rows below store registry text verbatim — never generated, paraphrased or
interpreted. Additive on the `/v1/trials/{nct_id}` detail payload.

| API Field | Upstream CTG v2 Field | Silver Column | Warehouse Model & Column | Business Rule & Derivation |
|---|---|---|---|---|
| `arms[]` | `protocolSection.armsInterventionsModule.armGroups[]` (`label`, `type`, `description`, `interventionNames`) | `silver_trial_arms.arm_label` / `arm_type` / `arm_description` / `intervention_names` | `stg_trial_arms` (grain: profile × NCT × run × `arm_ordinal`) | Arm list preserved in registry order via zero-based `arm_ordinal`. `arm_type` includes `PLACEBO_COMPARATOR`; null when the registry omits it. |
| `has_placebo_arm` | Derived from `armGroups[].type` | `silver_trials.has_placebo_arm` | `dim_trial.has_placebo_arm` | True when any arm group declares type `PLACEBO_COMPARATOR`. |
| `study_design.masking` | `protocolSection.designModule.designInfo.maskingInfo.masking` | `silver_trials.masking` | `dim_trial.current_masking` | Masking level (`NONE`, `SINGLE`, `DOUBLE`, `TRIPLE`, `QUADRUPLE`); null when unstated. |
| `study_design.intervention_model` | `protocolSection.designModule.designInfo.interventionModel` | `silver_trials.intervention_model` | `dim_trial.current_intervention_model` | `PARALLEL`, `SINGLE_GROUP`, `FACTORIAL`, etc. |
| `why_stopped` | `protocolSection.statusModule.whyStopped` | `silver_trials.why_stopped` | `dim_trial.why_stopped` | Verbatim registry stop reason; populated for terminated/withdrawn/suspended trials. |
| `brief_summary` | `protocolSection.descriptionModule.briefSummary` | `silver_trials.brief_summary` | `dim_trial.brief_summary` | Verbatim registry-provided brief summary; never generated. |
| `primary_outcome_time_frame` | `protocolSection.outcomesModule.primaryOutcomes[0].timeFrame` | `silver_trials.primary_outcome_time_frame` | `dim_trial.primary_outcome_time_frame` | Verbatim timing of the first primary outcome (e.g. "78 weeks"). |
| `dates.start_date` / `dates.primary_completion_date` | `protocolSection.statusModule.startDateStruct.date` / `primaryCompletionDateStruct.date` | `silver_trials.start_date` / `primary_completion_date` | `dim_trial.start_date` / `primary_completion_date` | Already covered in section 1; listed here as the participant-facing "how long?" dates. |

---

## 3. Study Locations & Sites (ADR 0065 Privacy Guardrails)

In strict adherence to **ADR 0065**, facility-level and central investigator names, personal email addresses, and personal telephone numbers are **deliberately excluded** from the data pipeline and read API models.

| API Field | Upstream CTG v2 Field | Silver Column | Warehouse Model & Column | Governance & Redaction Notes |
|---|---|---|---|---|
| `facility_name` | `locations[].facility` | `silver_trial_locations.facility_name` | `fct_trial_site.facility_name` | Public facility name. Note: Facility names are not unique site IDs. |
| `city` | `locations[].city` | `silver_trial_locations.city` | `fct_trial_site.city` | Listed city. |
| `state` | `locations[].state` | `silver_trial_locations.state_normalized` | `fct_trial_site.state_normalized` | Normalized 2-letter postal abbreviation via `config/geography_rules.yml`. |
| `zip_code` | `locations[].zip` | `silver_trial_locations.zip_code` | `fct_trial_site.zip_code` | Postal code. |
| `country` | `locations[].country` | `silver_trial_locations.country` | `silver_trial_locations.country` | ISO country name. |
| `location_status` | `locations[].status` | `silver_trial_locations.location_status` | `fct_trial_site.location_status` | Site recruitment status (`RECRUITING`, etc.). |
| `coordinates.lat` | `locations[].geoPoint.lat` | `silver_trial_locations.latitude` | `silver_trial_locations.latitude` | Geocoded latitude. |
| `coordinates.lon` | `locations[].geoPoint.lon` | `silver_trial_locations.longitude` | `silver_trial_locations.longitude` | Geocoded longitude. |
| `distance_miles` | Calculated | N/A | Calculated in API layer | Computed on-the-fly using great-circle Haversine formula from client's query `lat` and `lon`. |
| *Investigator PII* | `locations[].contacts[]` | **REDACTED** (`stg_trial_contacts` where 1=0) | **NONE** | **EXCLUDED PER ADR 0065**. No individual person names, phone numbers, or emails are persisted or served. |

---

## 4. Conditions & Taxonomy (`/v1/conditions`)

| API Field | Upstream CTG v2 Field | Silver Column | Warehouse Model & Column | Transformation & Ontology Notes |
|---|---|---|---|---|
| `condition_name` | `conditionsModule.conditions[]` | `silver_trial_conditions.condition_raw` | `dim_condition.condition_display_name` | Registry condition string. |
| `condition_normalized` | Derived | `silver_trial_conditions.condition_normalized` | `dim_condition.condition_normalized` | Lowercased, punctuation-stripped matching key. |
| `condition_group` | Config mapping | `silver_trial_conditions.condition_group` | `dim_condition.condition_group` | Standardized condition cluster via `config/condition_taxonomy.yml`. |
| `mesh_id` | MeSH heading | External ontology reference | `dim_condition.mesh_id` | Unique Medical Subject Headings descriptor ID (e.g. D000544). |
| `synonyms` | NLM MeSH term list | `config/condition_taxonomy.yml` | `dim_condition.synonyms` | Array of clinical synonyms and common patient search phrases. |
| `trial_count` | Aggregated | N/A | `dim_condition.trial_count` | Distinct NCT IDs associated with condition within profile scope. |

---

## 5. Pairwise Study Similarity (`/v1/trials/{nct_id}/related`)

Derived from deterministic scoring in Gold mart `mart_trial_similarity`. Exposes objective comparability without clinical equivalence claims.

| API Field | Warehouse Mart & Column | Scoring Weight | Mathematical Derivation & Logic |
|---|---|---|---|
| `similarity_score` | `mart_trial_similarity.similarity_score` | 1.0 (Sum) | Weighted sum: $\sum (w_c \times \text{matched}_c)$, rounded to 4 decimals. |
| `similarity_rank` | `mart_trial_similarity.similarity_rank` | N/A | Dense rank partition by `(indication_profile_id, nct_id_a)` ordered by score desc. Bounded to top 25. |
| `same_condition` | `mart_trial_similarity.same_condition` | 0.25 | 1 if intersection of trial condition groups is non-empty, else 0. |
| `same_phase` | `mart_trial_similarity.same_phase` | 0.20 | 1 if normalized phases match exactly, else 0. |
| `geography_overlap` | `mart_trial_similarity.geography_overlap` | 0.15 | 1 if trials share one or more U.S. states in listed active sites, else 0. |
| `intervention_type_overlap` | `mart_trial_similarity.intervention_type_overlap` | 0.15 | 1 if trials share intervention types (e.g. Drug, Biological), else 0. |
| `study_design_match` | `mart_trial_similarity.study_design_match` | 0.10 | 1 if `study_type`, `allocation`, and `primary_purpose` match, else 0. |
| `eligibility_compatible` | `mart_trial_similarity.eligibility_compatible` | 0.085 | 1 if sex is compatible, healthy volunteers match, and age windows overlap. |
| `enrollment_band_match` | `mart_trial_similarity.enrollment_band_match` | 0.05 | 1 if enrollment sizes fall in the same tier (small <50, medium 50-200, large >200). |
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
| `latest_snapshot_id` | `data/bronze/<profile>/manifests/manifest_<run_id>.json` | `stg_trial_snapshots.snapshot_id` | Timestamped run identifier serving current records. |
| `latest_snapshot_date` | Manifest timestamp | `dim_date.date_day` | Calendar date of latest snapshot. |
| `source_api` | Ingestion configuration | N/A | Upstream source name ("ClinicalTrials.gov API v2"). |
| `source_api_version` | Ingestion configuration | N/A | Registry API version ("v2.0"). |
| `schema_baseline_version` | Bronze baseline file | `_schema_baseline.json` | Version baseline for drift detection. |
| `total_studies_tracked` | Manifest summary parquet | `mart_data_reliability.total_studies` | Count of unique studies tracked in profile. |
| `quality_summary.ok_records_percent`| `mart_data_reliability` | `mart_data_reliability.ok_rate` | Percentage of studies passing all quality gates. |
| `quarantined_records_count` | Manifest `quarantined_record_count` | `stg_trial_snapshots.quarantined_count` | Records isolated due to schema violations. |
