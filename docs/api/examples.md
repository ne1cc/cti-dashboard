# Read API Request and Response Examples

This document provides **synthetic and illustrative** request and response examples for endpoints declared in [OpenAPI Specification](openapi.yaml). Payloads in Sections 1–2 are synthetic fixtures rather than production observations. Local verification composes against `http://localhost:8000` (for example, `http://localhost:8000/v1/trials` and `http://localhost:8000/v1/trials/NCT00000001`, with no doubled `/v1`). `https://api.cti.example.org` is an **illustrative placeholder only** — it is not a deployed gateway and must never be contacted.

Three execution paths remain distinct across the project:
- **Warehouse API backend** (`CTI_WAREHOUSE_PATH` set; see [Warehouse Serving Engine](serving.md)): DuckDB Gold/staging queries, keyset cursor pagination, and `If-None-Match` -> `304 Not Modified`.
- **Mocked-live API backend** (no `CTI_WAREHOUSE_PATH`; see [Live Backend](live-backend.md)): single-page upstream query + in-memory post-filtering/sorting + 6-hour SQLite cache (`X-Cache-Status: HIT|MISS|STALE`, synchronous stale-if-error).
- **Batch / recorded landscape evidence** ([October 3, 2026 Feasibility Case Study](../feasibility_case_study.md)): direct DuckDB/audit reads (`docs/evidence/`); no API endpoint is required for every dashboard chart.

All example responses conform strictly to:
- **ADR 0061**: Dual delivery surfaces (server-rendered patient app + analyst workbench).
- **ADR 0065**: Contact privacy guardrail (official registry deep link `registry_contact_url` without investigator/facility PII).
- **Issue #62**: Structured eligibility criteria preserving unstated bounds (`age_not_stated: true`).
- **RFC 7807**: Problem Details for HTTP APIs (`application/problem+json`).

---

## 1. Clinical Trials Search (`GET /v1/trials`)

### 1.1 Selected Walkthrough — Warehouse Backend (`status=RECRUITING`, Local Base Composition)

> **Data & Backend Label:** **Synthetic / Illustrative — Warehouse Backend** (`CTI_WAREHOUSE_PATH` configured).
> **Base Composition:** `http://localhost:8000` + `/v1/trials` -> `http://localhost:8000/v1/trials` (no doubled `/v1`).
> **Status Filter Note:** Always pass `status=RECRUITING` explicitly when recruiting-only matches are intended. When `status` is omitted, the **live** backend defaults to `RECRUITING|NOT_YET_RECRUITING|ACTIVE_NOT_RECRUITING|ENROLLING_BY_INVITATION`, whereas the **warehouse** backend applies no status filter.

**Request:**
```http
GET /v1/trials?cond=Alzheimer+Disease&status=RECRUITING&lat=37.7749&lon=-122.4194&radius_mi=50&min_age=65&limit=2 HTTP/1.1
Host: localhost:8000
Accept: application/json
```

**Response Headers (Warehouse Backend — Implemented Behavior):**
```http
HTTP/1.1 200 OK
Content-Type: application/json
Data-Snapshot: 2026-07-31T140000_3f5a473e
Data-Updated: 2026-07-31T14:00:00Z
ETag: W/"9f8a3c2e1b4a7d10"
Cache-Control: public, max-age=300
X-Request-ID: 8b9e6c43-1f2a-4c9b-9d10-000000000001
```
*(No `RateLimit-*` or `stale-while-revalidate` headers are emitted by the implemented handlers. Sending a subsequent warehouse `GET` with `If-None-Match: W/"9f8a3c2e1b4a7d10"` returns `304 Not Modified` with an empty body while preserving these cache headers.)*

**Response Body (Synthetic / Illustrative — Warehouse Backend):**
<!-- example: trials_search_success -->
```json
{
  "items": [
    {
      "nct_id": "NCT04245644",
      "brief_title": "Phase 3 Evaluation of Novel Tau-Targeted Therapy in Early Alzheimer Disease",
      "overall_status": "RECRUITING",
      "phase": "PHASE3",
      "study_type": "INTERVENTIONAL",
      "lead_sponsor": "Biogen Inc.",
      "sponsor_class": "INDUSTRY",
      "start_date": "2020-04-15",
      "last_update_post_date": "2026-06-12",
      "conditions": [
        "Alzheimer Disease",
        "Mild Cognitive Impairment"
      ],
      "structured_eligibility": {
        "minimum_age_years": 55.0,
        "maximum_age_years": 85.0,
        "age_not_stated": false,
        "sex": "ALL",
        "accepts_healthy_volunteers": false,
        "std_age_groups": [
          "ADULT",
          "OLDER_ADULT"
        ]
      },
      "nearest_site": {
        "facility_name": "UCSF Memory and Aging Center",
        "city": "San Francisco",
        "state": "CA",
        "distance_miles": 3.2
      },
      "registry_url": "https://clinicaltrials.gov/study/NCT04245644"
    },
    {
      "nct_id": "NCT03887455",
      "brief_title": "A Study to Evaluate the Efficacy and Safety of Lecanemab in Participants With Early Alzheimer Disease",
      "overall_status": "RECRUITING",
      "phase": "PHASE3",
      "study_type": "INTERVENTIONAL",
      "lead_sponsor": "Eisai Inc.",
      "sponsor_class": "INDUSTRY",
      "start_date": "2019-03-22",
      "last_update_post_date": "2026-05-30",
      "conditions": [
        "Alzheimer Disease"
      ],
      "structured_eligibility": {
        "minimum_age_years": 50.0,
        "maximum_age_years": 90.0,
        "age_not_stated": false,
        "sex": "ALL",
        "accepts_healthy_volunteers": false,
        "std_age_groups": [
          "ADULT",
          "OLDER_ADULT"
        ]
      },
      "nearest_site": {
        "facility_name": "Stanford Center for Memory Disorders",
        "city": "Palo Alto",
        "state": "CA",
        "distance_miles": 28.5
      },
      "registry_url": "https://clinicaltrials.gov/study/NCT03887455"
    }
  ],
  "pagination": {
    "next_cursor": "eyJvZmZzZXQiOjIsImxpbWl0IjoyLCJzb3J0IjoiZGlzdGFuY2UifQ==",
    "prev_cursor": null,
    "limit": 2,
    "total_count": 14,
    "has_more": true
  }
}
```

**Opaque Cursor Continuation (`next_cursor`):**
Clients treat `pagination.next_cursor` as an opaque string and pass it back unchanged as `cursor`:
```http
GET /v1/trials?cond=Alzheimer+Disease&status=RECRUITING&lat=37.7749&lon=-122.4194&radius_mi=50&min_age=65&limit=2&cursor=eyJvZmZzZXQiOjIsImxpbWl0IjoyLCJzb3J0IjoiZGlzdGFuY2UifQ== HTTP/1.1
Host: localhost:8000
Accept: application/json
```

---

### 1.2 Empty Result Set (Synthetic / Illustrative — Warehouse Backend)

**Request:**
```http
GET /v1/trials?cond=Huntington+Disease&status=RECRUITING&lat=37.7749&lon=-122.4194&radius_mi=5 HTTP/1.1
Host: localhost:8000
```

**Response (Synthetic / Illustrative — Warehouse Backend):**
<!-- example: trials_search_empty -->
```json
{
  "items": [],
  "pagination": {
    "next_cursor": null,
    "prev_cursor": null,
    "limit": 20,
    "total_count": 0,
    "has_more": false
  }
}
```

---

### 1.3 Post-Filtered Empty Page & Cursor Continuation (Synthetic / Illustrative — Mocked-Live Backend)

> **Data & Backend Label:** **Synthetic / Illustrative — Mocked-Live Backend** (no `CTI_WAREHOUSE_PATH`; stubbed/cached upstream registry path).

In the live backend, `eligibility`, `phase`, `std_age_group`, `sponsor_class`, `sort`, and the recruiting-site radius post-filter run **in memory on a single fetched upstream page**. Therefore:
- `items: []` with `has_more: true` requires **cursor continuation** (`cursor=<next_cursor>`), not an exhausted-search conclusion.
- `total_count: null` means **unknown** (because post-filtering or malformed-study omissions occurred), **not** zero.
- `intervention_type` and `updated_since` remain unsupported on the live backend and return `422` `UNSUPPORTED_FILTER`.
- Repeating this request within the 6-hour SQLite cache TTL returns `200 OK` with `X-Cache-Status: HIT` (live routes do not implement `If-None-Match` -> `304`). The 6-hour TTL is a freshness window before attempting an upstream refresh, **not** a maximum stale age: if the upstream refresh hits an outage (timeout, connection error, 429/5xx, or open breaker), synchronous `stale_if_error` returns `200 OK` with `X-Cache-Status: STALE`, `Cache-Control: no-cache`, and `Warning: 110 "Response is Stale"`.

**Request:**
```http
GET /v1/trials?cond=Alzheimer+Disease&status=RECRUITING&phase=PHASE3&min_age=65&limit=20 HTTP/1.1
Host: localhost:8000
Accept: application/json
```

**Response Headers (Mocked-Live Backend — Implemented Behavior):**
```http
HTTP/1.1 200 OK
Content-Type: application/json
Data-Snapshot: live-api
Data-Updated: 2026-07-31T14:02:18Z
ETag: W/"4b7e21d90c3a81f2"
Cache-Control: public, max-age=300
X-Cache-Status: MISS
X-Request-ID: 8b9e6c43-1f2a-4c9b-9d10-000000000002
```

**Response Body (Synthetic / Illustrative — Mocked-Live Backend):**
<!-- example: trials_search_live_postfiltered_empty -->
```json
{
  "items": [],
  "pagination": {
    "next_cursor": "eyJwYWdlIjoyfQ==",
    "prev_cursor": null,
    "limit": 20,
    "total_count": null,
    "has_more": true
  }
}
```

---

## 2. Trial Protocol Detail (`GET /v1/trials/{nct_id}`)

### 2.1 Selected Walkthrough — Full Protocol, ADR 0065 Deep Link, and Provenance Clocks

> **Data & Backend Label:** **Synthetic / Illustrative — Warehouse Backend** (same payload shape on the live backend, with `Data-Snapshot: live-api`).
> **Base Composition:** `http://localhost:8000` + `/v1/trials/{nct_id}` substitutes `nct_id` directly (for example, `http://localhost:8000/v1/trials/NCT04245644` below, or `http://localhost:8000/v1/trials/NCT00000001` in the synthetic warehouse contract fixture).

**Request:**
```http
GET /v1/trials/NCT04245644?lat=37.7749&lon=-122.4194 HTTP/1.1
Host: localhost:8000
Accept: application/json
```

**Response Headers (Warehouse Backend — Implemented Behavior):**
```http
HTTP/1.1 200 OK
Content-Type: application/json
Data-Snapshot: 2026-07-31T140000_3f5a473e
Data-Updated: 2026-07-31T14:00:00Z
ETag: W/"7c1d9e4a2b60f318"
Cache-Control: public, max-age=300
X-Request-ID: 8b9e6c43-1f2a-4c9b-9d10-000000000003
```

**Response Body (Synthetic / Illustrative — Warehouse Backend):**
<!-- example: trial_detail_success -->
```json
{
  "nct_id": "NCT04245644",
  "brief_title": "Phase 3 Evaluation of Novel Tau-Targeted Therapy in Early Alzheimer Disease",
  "official_title": "A Randomized, Double-Blind, Placebo-Controlled Study of BIIB080 in Participants With Early Alzheimer Disease",
  "overall_status": "RECRUITING",
  "last_known_status": null,
  "status_verified_date": "2026-06",
  "study_type": "INTERVENTIONAL",
  "has_expanded_access": false,
  "study_design": {
    "phases": [
      "PHASE3"
    ],
    "phase_raw": "Phase 3",
    "phase_normalized": "PHASE3",
    "allocation": "RANDOMIZED",
    "primary_purpose": "TREATMENT",
    "intervention_model": "PARALLEL",
    "masking": "QUADRUPLE"
  },
  "enrollment": {
    "count": 450,
    "type": "ESTIMATED"
  },
  "dates": {
    "start_date": "2020-04-15",
    "primary_completion_date": "2027-02-28",
    "completion_date": "2027-06-30",
    "study_first_post_date": "2020-01-24",
    "results_first_post_date": null,
    "last_update_post_date": "2026-06-12"
  },
  "sponsors": {
    "lead_sponsor": {
      "name": "Biogen Inc.",
      "class": "INDUSTRY"
    },
    "collaborators": [
      {
        "name": "Ionis Pharmaceuticals, Inc.",
        "class": "INDUSTRY"
      }
    ],
    "responsible_party_type": "SPONSOR"
  },
  "conditions": [
    {
      "condition_raw": "Alzheimer Disease",
      "condition_normalized": "alzheimer disease",
      "condition_group": "Alzheimer Disease",
      "mesh_id": "D000544",
      "dementia_relevance_flag": true
    },
    {
      "condition_raw": "Mild Cognitive Impairment",
      "condition_normalized": "mild cognitive impairment",
      "condition_group": "Mild Cognitive Impairment",
      "mesh_id": "D060825",
      "dementia_relevance_flag": true
    }
  ],
  "interventions": [
    {
      "intervention_type": "DRUG",
      "intervention_name": "BIIB080",
      "description": "Antisense oligonucleotide targeting tau synthesis administered intrathecally"
    },
    {
      "intervention_type": "DRUG",
      "intervention_name": "Placebo",
      "description": "Matching placebo administered intrathecally"
    }
  ],
  "eligibility": {
    "minimum_age_raw": "55 Years",
    "maximum_age_raw": "85 Years",
    "minimum_age_years": 55.0,
    "maximum_age_years": 85.0,
    "age_not_stated": false,
    "sex": "ALL",
    "gender_based": false,
    "gender_description": null,
    "accepts_healthy_volunteers": false,
    "std_age_groups": [
      "ADULT",
      "OLDER_ADULT"
    ],
    "criteria_text": "Inclusion Criteria:\n1. Age 55 to 85 years at screening.\n2. Meets NIA-AA criteria for probable Alzheimer disease dementia or mild cognitive impairment due to Alzheimer disease.\n3. Confirmed presence of elevated brain amyloid on amyloid PET or CSF examination.\n\nExclusion Criteria:\n1. Non-Alzheimer neurodegenerative disorder.\n2. Clinically significant psychiatric disorder."
  },
  "sites": [
    {
      "facility_name": "UCSF Memory and Aging Center",
      "city": "San Francisco",
      "state": "CA",
      "zip_code": "94143",
      "country": "United States",
      "location_status": "RECRUITING",
      "coordinates": {
        "lat": 37.7631,
        "lon": -122.4578
      },
      "distance_miles": 3.2
    },
    {
      "facility_name": "Stanford Center for Memory Disorders",
      "city": "Palo Alto",
      "state": "CA",
      "zip_code": "94304",
      "country": "United States",
      "location_status": "RECRUITING",
      "coordinates": {
        "lat": 37.4358,
        "lon": -122.1762
      },
      "distance_miles": 28.5
    }
  ],
  "registry_url": "https://clinicaltrials.gov/study/NCT04245644",
  "registry_contact_url": "https://clinicaltrials.gov/study/NCT04245644#contacts-and-locations",
  "provenance": {
    "ingestion_run_id": "2026-07-31T140000_3f5a473e",
    "snapshot_id": "2026-07-31T140000_3f5a473e",
    "snapshot_date": "2026-07-31",
    "snapshot_timestamp_utc": "2026-07-31T14:00:00Z",
    "source_json_hash": "8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4",
    "record_quality_flag": "ok",
    "retrieved_at_utc": "2026-07-31T14:02:18Z",
    "indication_profile_id": "adrd"
  }
}
```

---

### 2.2 Preserving Unstated/Unknown Eligibility Bounds (Issue #62 Compliance)

When a study's protocol does not state age limits, `minimum_age_years` and `maximum_age_years` are returned as `null`, and `age_not_stated` is set to `true`. This guarantees that search filters do not silently discard trials with unrestricted or unstated eligibility.

<!-- example: trial_detail_unknown_criteria -->
```json
{
  "nct_id": "NCT05123456",
  "brief_title": "Observational Biomarker Study in Early Cognitive Impairment",
  "official_title": "A Multi-Center Observational Biomarker Study",
  "overall_status": "RECRUITING",
  "last_known_status": null,
  "status_verified_date": "2026-04",
  "study_type": "INTERVENTIONAL",
  "has_expanded_access": false,
  "study_design": {
    "phases": [
      "NOT_APPLICABLE"
    ],
    "phase_raw": null,
    "phase_normalized": "NOT_APPLICABLE",
    "allocation": null,
    "primary_purpose": "DIAGNOSTIC",
    "intervention_model": "SINGLE_GROUP",
    "masking": null
  },
  "enrollment": {
    "count": 100,
    "type": "ESTIMATED"
  },
  "dates": {
    "start_date": "2022-01-10",
    "primary_completion_date": "2026-12-31",
    "completion_date": "2027-01-31",
    "study_first_post_date": "2021-11-15",
    "results_first_post_date": null,
    "last_update_post_date": "2026-04-01"
  },
  "sponsors": {
    "lead_sponsor": {
      "name": "Academic Medical Center",
      "class": "OTHER"
    },
    "collaborators": [],
    "responsible_party_type": "PRINCIPAL_INVESTIGATOR"
  },
  "conditions": [
    {
      "condition_raw": "Mild Cognitive Impairment",
      "condition_normalized": "mild cognitive impairment",
      "condition_group": "Mild Cognitive Impairment",
      "mesh_id": "D060825",
      "dementia_relevance_flag": true
    }
  ],
  "interventions": [
    {
      "intervention_type": "DIAGNOSTIC_TEST",
      "intervention_name": "Plasma Biomarker Assay",
      "description": "Blood draw for novel p-tau217 phosphorylation assay"
    }
  ],
  "eligibility": {
    "minimum_age_raw": null,
    "maximum_age_raw": null,
    "minimum_age_years": null,
    "maximum_age_years": null,
    "age_not_stated": true,
    "sex": "UNKNOWN",
    "gender_based": null,
    "gender_description": null,
    "accepts_healthy_volunteers": null,
    "std_age_groups": [],
    "criteria_text": "Inclusion Criteria:\n1. Suspected mild cognitive symptoms.\n2. Willingness to provide blood samples.\n\nExclusion Criteria:\nNone stated."
  },
  "sites": [
    {
      "facility_name": "Mayo Clinic Rochester",
      "city": "Rochester",
      "state": "MN",
      "zip_code": "55905",
      "country": "United States",
      "location_status": "RECRUITING",
      "coordinates": {
        "lat": 44.0225,
        "lon": -92.4666
      },
      "distance_miles": null
    }
  ],
  "registry_url": "https://clinicaltrials.gov/study/NCT05123456",
  "registry_contact_url": "https://clinicaltrials.gov/study/NCT05123456#contacts-and-locations",
  "provenance": {
    "ingestion_run_id": "2026-07-31T140000_3f5a473e",
    "snapshot_id": "2026-07-31T140000_3f5a473e",
    "snapshot_date": "2026-07-31",
    "snapshot_timestamp_utc": "2026-07-31T14:00:00Z",
    "source_json_hash": "a4d32e1858a8a4f91040529d812d312bc88b59b2dc327aa49f8a3c2e1b4a3c2e",
    "record_quality_flag": "ok",
    "retrieved_at_utc": "2026-07-31T14:03:00Z",
    "indication_profile_id": "adrd"
  }
}
```

---

### 2.3 Provenance Clocks, Field-to-Evidence Trace (`overallStatus`), and Boundaries

**Three distinct provenance clocks** appear across headers and `TrialDetail`:
1. **Capture / snapshot clock:** `Data-Snapshot` header and `provenance.snapshot_id` / `snapshot_date` / `snapshot_timestamp_utc` identify the warehouse snapshot (`live-api` on the live backend). On the warehouse backend, the `Data-Updated` header carries this snapshot/capture timestamp (`snap_ts` from `apply_cache_headers_and_check_etag` in `src/api/app.py`), matching `provenance.snapshot_timestamp_utc`.
2. **Retrieval / cache clock:** `provenance.retrieved_at_utc` records the per-study retrieval timestamp when CTI fetched the study JSON. On the live backend, the `Data-Updated` header records the cache retrieval time (`entry.created_at` from `set_live_response_headers` in `src/api/app.py`).
3. **Source-update clock:** `dates.last_update_post_date` and `status_verified_date` reflect sponsor-reported updates on ClinicalTrials.gov, independent of CTI capture or cache timing.

**Field-to-evidence trace (`overallStatus`):**
- **Upstream source:** `protocolSection.statusModule.overallStatus` in ClinicalTrials.gov API v2 (see [Source Field Traceability Matrix](source-field-map.md)).
- **Live API path:** `src/api/live.py` (`normalize_study_summary` / `normalize_study_detail`) maps `statusModule.overallStatus` (defaulting missing values to `"UNKNOWN"`) into `overall_status`; `build_upstream_search_params` defaults omitted `status` to `RECRUITING|NOT_YET_RECRUITING|ACTIVE_NOT_RECRUITING|ENROLLING_BY_INVITATION`.
- **Batch & warehouse path:** `src/transform/flatten_studies.py` (`flatten_study` -> `silver_trials.overall_status`; `trial_quality_flags` flags `missing_overall_status`) flows through `dbt_clinical_trials/models/staging/stg_trials.sql` (`overall_status`) into `dbt_clinical_trials/models/marts/dim_trial.sql` (`current_overall_status`) and `src/api/sql/search.sql` (which filters `overall_status` only when `$status IS NOT NULL`). Downstream, `dbt_clinical_trials/models/marts/mart_study_snapshot_audit.sql` computes `confirmed_recruiting_flag` (`overall_status = 'RECRUITING'`) and `dashboard/components/audit.py` enforces `row.overall_status == "RECRUITING"` under `competition-audit-v2`.
- **Recorded / historical landscape evidence ([October 3, 2026 Feasibility Case Study](../feasibility_case_study.md)):** Run `20261003T080013Z_c1b234b8` (source commit `aff62f7`, rules `competition-audit-v2`) records **57** rows (**50** states, 1 national union, 6 additional jurisdictions), **221** study–state memberships, **18** national contributors, a **35**-study recruiting denominator, and **274** broader cohort studies (18 national contributors + 239 `not_confirmed_recruiting` exclusions + 17 `unsupported_geography` exclusions).

**Explicit capability and cohort boundaries:**
- **Batch/evidence vs. API:** The October 3 case study reads frozen DuckDB/audit artifacts directly; no API endpoint is required for every dashboard chart.
- **Phase matching:** API `phase=PHASE3` matches `/` phase tokens (including combined strings such as `PHASE2/PHASE3`) in both `src/api/live.py` and `src/api/sql/search.sql`. The API example does **not** claim to reproduce the case study's exact-`PHASE3` cohort or 50-state union.
- **Live `/related`:** Calling `GET /v1/trials/{nct_id}/related` without a warehouse returns `200 OK` with `related_trials: []` because the live backend does not compute similarity (there is no registry-wide discovery without a warehouse in the `/related` handler in `src/api/app.py`; related trials come from the warehouse path in `src/api/sql/related.sql`). An empty list on the live backend indicates unsupported backend capability, not proof that no comparable studies exist.
- **Unimplemented `/v1/geo/access`:** Section 5 below documents the OpenAPI schema shape for `GET /v1/geo/access`, which has no implemented FastAPI handler in `src/api/app.py` and is explicitly outside this executable walkthrough (its implement/defer/remove policy remains pending).

---

## 3. Conditions Autocomplete & MeSH Browsing (`GET /v1/conditions`)

**Request:**
```http
GET /v1/conditions?q=alz&indication_profile_id=adrd&limit=1 HTTP/1.1
Host: api.cti.example.org
```

**Response:**
<!-- example: conditions_autocomplete -->
```json
{
  "items": [
    {
      "condition_name": "Alzheimer Disease",
      "condition_normalized": "alzheimer disease",
      "condition_group": "Alzheimer Disease",
      "mesh_id": "D000544",
      "mesh_tree_code": "C10.228.140.380.100",
      "synonyms": [
        "Alzheimer Dementia",
        "Alzheimers",
        "AD",
        "Primary Senile Degenerative Dementia"
      ],
      "trial_count": 320,
      "active_trial_count": 84,
      "has_children": true
    }
  ],
  "total_count": 1
}
```

---

## 4. Related Trials & Design Comparability (`GET /v1/trials/{nct_id}/related`)

**Request:**
```http
GET /v1/trials/NCT04245644/related?limit=1 HTTP/1.1
Host: api.cti.example.org
```

**Response:**
<!-- example: trial_related_success -->
```json
{
  "target_nct_id": "NCT04245644",
  "related_trials": [
    {
      "nct_id": "NCT03887455",
      "brief_title": "A Study to Evaluate the Efficacy and Safety of Lecanemab in Participants With Early Alzheimer Disease",
      "overall_status": "ACTIVE_NOT_RECRUITING",
      "phase": "PHASE3",
      "lead_sponsor": "Eisai Inc.",
      "similarity_score": 0.885,
      "similarity_rank": 1,
      "comparability_factors": {
        "same_condition": {
          "matched": true,
          "weight": 0.25,
          "weighted_score": 0.25
        },
        "same_phase": {
          "matched": true,
          "weight": 0.2,
          "weighted_score": 0.2
        },
        "geography_overlap": {
          "matched": true,
          "weight": 0.15,
          "weighted_score": 0.15
        },
        "intervention_type_overlap": {
          "matched": true,
          "weight": 0.15,
          "weighted_score": 0.15
        },
        "study_design_match": {
          "matched": true,
          "weight": 0.1,
          "weighted_score": 0.1
        },
        "eligibility_compatible": {
          "matched": true,
          "weight": 0.085,
          "weighted_score": 0.085
        },
        "enrollment_band_match": {
          "matched": false,
          "weight": 0.05,
          "weighted_score": 0.0
        },
        "explanation_summary": "shared condition mapping; same phase (PHASE3); overlapping U.S. states; shared intervention type; matching study type, allocation, and primary purpose; compatible eligibility criteria"
      },
      "registry_url": "https://clinicaltrials.gov/study/NCT03887455"
    }
  ],
  "methodology_disclaimer": "Deterministic design comparability signal based on public registry attributes. NOT clinical equivalence or medical advice."
}
```

---

## 5. Population Access Analytics (`GET /v1/geo/access`)

**Request:**
```http
GET /v1/geo/access?state=CA&condition_group=Alzheimer+Disease HTTP/1.1
Host: api.cti.example.org
```

**Response:**
<!-- example: geo_access_success -->
```json
{
  "state": "CA",
  "condition_group": "Alzheimer Disease",
  "snapshot_date": "2026-07-31",
  "active_sites_count": 48,
  "recruiting_trials_count": 32,
  "sponsor_hhi": 1820.5,
  "top_sponsor_share": 0.285,
  "competition_intensity_tier": "HIGH",
  "access_gap_index": 0.34,
  "travel_burden_summary": {
    "average_distance_miles": 24.6,
    "median_distance_miles": 12.4,
    "isolated_population_percent": 14.8
  },
  "regional_distribution": [
    {
      "region_name": "San Francisco Bay Area",
      "site_count": 18,
      "trial_count": 15
    },
    {
      "region_name": "Greater Los Angeles",
      "site_count": 22,
      "trial_count": 19
    }
  ]
}
```

---

## 6. Freshness & Ingestion Metadata (`GET /v1/meta/freshness`)

**Request:**
```http
GET /v1/meta/freshness HTTP/1.1
Host: api.cti.example.org
```

**Response:**
<!-- example: meta_freshness_success -->
```json
{
  "profiles": [
    {
      "indication_profile_id": "adrd",
      "latest_snapshot_id": "20261020T120000Z_adrd",
      "latest_snapshot_date": "2026-10-20",
      "latest_snapshot_timestamp_utc": "2026-10-20T12:00:00Z",
      "latest_ingestion_run_id": "20261020T120000Z_adrd",
      "source_api": "ClinicalTrials.gov API v2",
      "source_api_version": "v2",
      "total_studies_tracked": 1482,
      "row_counts": {
        "dim_trial": 1482,
        "fct_trial_snapshot": 1482,
        "mart_trial_similarity": 900
      }
    },
    {
      "indication_profile_id": "oncology_nsclc",
      "latest_snapshot_id": "20261020T120000Z_oncology_nsclc",
      "latest_snapshot_date": "2026-10-20",
      "latest_snapshot_timestamp_utc": "2026-10-20T12:00:00Z",
      "latest_ingestion_run_id": "20261020T120000Z_oncology_nsclc",
      "source_api": "ClinicalTrials.gov API v2",
      "source_api_version": "v2",
      "total_studies_tracked": 840,
      "row_counts": {
        "dim_trial": 840,
        "fct_trial_snapshot": 840,
        "mart_trial_similarity": 0
      }
    }
  ],
  "system_status": "HEALTHY",
  "last_success_at_utc": "2026-10-24T14:30:00Z",
  "reconciliation": {
    "checks_total": 10,
    "checks_failed": 0
  },
  "checked_at_utc": "2026-10-24T14:50:00Z"
}
```

---

## 7. Error Scenarios (RFC 7807 Problem Details)

### 7.1 HTTP 400 Bad Request (Invalid Geographic Range)

**Response:**
<!-- example: error_400_bad_request -->
```json
{
  "type": "https://api.cti.example.org/errors/invalid-parameters",
  "title": "Invalid Query Parameters",
  "status": 400,
  "detail": "Latitude value 95.5 is outside the valid range of -90.0 to 90.0.",
  "instance": "/v1/trials?lat=95.5&lon=-122.4",
  "code": "INVALID_COORDINATES",
  "invalid_params": [
    {
      "name": "lat",
      "reason": "Must be between -90.0 and 90.0",
      "value": 95.5
    }
  ]
}
```

---

### 7.2 HTTP 404 Not Found (Missing NCT ID)

**Response:**
<!-- example: error_404_not_found -->
```json
{
  "type": "https://api.cti.example.org/errors/not-found",
  "title": "Resource Not Found",
  "status": 404,
  "detail": "Study identifier NCT99999999 was not found in active or historical snapshots.",
  "instance": "/v1/trials/NCT99999999",
  "code": "STUDY_NOT_FOUND"
}
```

---

### 7.3 HTTP 422 Unprocessable Entity (Semantic Validation Failure)

**Response:**
<!-- example: error_422_validation -->
```json
{
  "type": "https://api.cti.example.org/errors/validation-error",
  "title": "Unprocessable Query Combination",
  "status": 422,
  "detail": "Parameter 'sort=distance' requires both 'lat' and 'lon' coordinates to be specified.",
  "instance": "/v1/trials?sort=distance",
  "code": "MISSING_COORDINATES_FOR_SORT",
  "invalid_params": [
    {
      "name": "sort",
      "reason": "Cannot sort by distance without search coordinates",
      "value": "distance"
    }
  ]
}
```

---

### 7.4 HTTP 429 Too Many Requests (Rate Limit Exceeded)

**Response Headers:**
```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/problem+json
Retry-After: 30
RateLimit-Limit: 120
RateLimit-Remaining: 0
RateLimit-Reset: 30
```

**Response:**
<!-- example: error_429_rate_limited -->
```json
{
  "type": "https://api.cti.example.org/errors/rate-limit-exceeded",
  "title": "Too Many Requests",
  "status": 429,
  "detail": "Client has exceeded the quota of 120 requests per minute. Retry after 30 seconds.",
  "instance": "/v1/trials",
  "code": "RATE_LIMIT_EXCEEDED"
}
```
