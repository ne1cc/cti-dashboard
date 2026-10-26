# Source Documentation — ClinicalTrials.gov API v2

## Source
- Registry: [ClinicalTrials.gov](https://clinicaltrials.gov), operated by
  the U.S. National Library of Medicine (NLM).
- Public data; no API key required; no personal/participant data served.
- Docs: https://clinicaltrials.gov/data-api/api ·
  OpenAPI: `https://clinicaltrials.gov/api/oas/v2`
  (note: the `.yaml` variant of that URL returned 404 during verification;
  the path above returned the spec).

## Endpoint used
`GET https://clinicaltrials.gov/api/v2/studies`

## Parameters (all config-driven, none hard-coded)
Authoritatively defined in `config/profiles/adrd.yml` (and referenced in `config/project_config.yml` via `default_profile: adrd`). Verified against the live API on 2026-07-24 (HTTP 200, historical `totalCount` 2,592 for the initial 4-status query; expanded under authoritative 8-status profile scope):

| Parameter | Value (Authoritative ADRD Profile) | Notes |
|---|---|---|
| `query.cond` | `Alzheimer Disease` | condition query |
| `filter.overallStatus` | `RECRUITING\|ACTIVE_NOT_RECRUITING\|NOT_YET_RECRUITING\|COMPLETED\|TERMINATED\|WITHDRAWN\|SUSPENDED\|ENROLLING_BY_INVITATION` | pipe-joined list (all 8 statuses) |
| `filter.advanced` | `AREA[StudyType]INTERVENTIONAL` | Essie expression |
| `format` | `json` | |
| `pageSize` | `100` | |
| `countTotal` | `true` | enables reconciliation vs `totalCount` |
| `pageToken` | from previous page's `nextPageToken` | followed until absent |

### Downstream Implications of the 8-Status Scope
- **Tracked Trial Totals**: The ingestion and warehouse layers retain all 8 statuses, ensuring total trial metrics (`dim_trial`, `trial_count`) reflect the entire recorded interventional trial landscape rather than only active/completed studies.
- **Stopped Studies & Why-Stopped Context**: Studies that ended prematurely (`TERMINATED`, `WITHDRAWN`, `SUSPENDED`) are captured with their reasons, powering why-stopped and feasibility risk analyses.
- **Similarity Pool vs. Recruiting Competition**: The trial similarity candidate pool includes all 8 statuses to identify operational and scientific precedents across all historical trials, whereas competition metrics (`recruiting_trial_count`, `new_recruiting_30d/90d`, `sponsor_hhi`) and priority scoring isolate confirmed active recruiting studies (`overall_status = 'RECRUITING'`).

## Pagination contract
Each response contains `studies[]` and optionally `nextPageToken`. The
ingester follows tokens until absent; an optional `--max-pages` cap marks
the run **partial**, and partial runs are excluded from reuse and from all
downstream layers by default.

## Retry / politeness
`requests` + `tenacity`: exponential backoff (1s → 60s), max 5 attempts,
retried statuses 429/500/502/503/504 (all in config, overridable via env).

## The history caveat (core design driver)
**The API serves only the current version of each study record.** There is
no changes feed in this pipeline's scope. Longitudinal status history is
therefore constructed from this project's own repeated snapshots:
- every run stores verbatim pages under a unique `ingestion_run_id`;
- `int_trial_status_history` derives transitions between *complete*
  snapshots only;
- transition-based metrics (e.g. `new_recruiting_90d`) honestly report 0
  until multiple snapshots have accrued, and registry-date proxies
  (`study_first_post_date`) are always labeled as proxies.

## Record validation and quarantine
Records failing validation are quarantined with reason codes — never
silently dropped: `NOT_AN_OBJECT`, `MISSING_NCT_ID`,
`INVALID_NCT_ID_FORMAT` (`^NCT\d{8}$`). Counts appear in the manifest and
the data-quality report.

## What is deliberately NOT collected
Central/overall contacts and investigator names, emails, and phone
numbers. They are never extracted from bronze, and the staging layer ships
an intentionally empty `stg_trial_contacts` model so any accidental
downstream reference fails visibly.

## U.S. Census Bureau Resident Population Estimates (Vintage 2024)

For population-normalized state listing metrics (`recruiting_trials_per_100k`), the project incorporates a static denominator seed extracted from the U.S. Census Bureau Population Estimates Program:

- **Source Dataset:** U.S. Census Bureau Vintage 2024 State Population Totals (`NST-EST2024-ALLDATA.csv`).
- **Source URL:** `https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/state/totals/NST-EST2024-ALLDATA.csv`
- **Retrieval Date:** 2026-10-23
- **SHA-256 Checksum:** `b8b50bd345d8a5f33d5c8a28c95aeaa0a50971356f000d9b48276a65fd9022a7`
- **Scope & Filtering:**
  - Filtered strictly to state-level rows where summary level `SUMLEV == '040'`, covering 50 U.S. states, the District of Columbia, and Puerto Rico (52 rows).
  - National summary (`SUMLEV == '010'`) and regional summary rows (`020`, `030`) are excluded.
- **Fields Extracted:**
  - `STATE` (2-digit zero-padded string `fips_code`, e.g. `'01'`, `'06'`, `'72'`)
  - `NAME` (`state_name`, e.g. `'Alabama'`, `'California'`, `'Puerto Rico'`)
  - `POPESTIMATE2024` (resident population estimate as of July 1, 2024, `population`)
  - Postal abbreviation mapped via `config/geography_rules.yml` (`state_code`, e.g. `'AL'`, `'CA'`, `'PR'`).
  - Fixed metadata: `estimate_date: '2024-07-01'`, `vintage: 'Vintage 2024'`, and `source_url`.
- **Handling of Unmatched Jurisdictions:**
  - Territories without Vintage 2024 estimates (e.g. Guam, U.S. Virgin Islands, American Samoa, Northern Mariana Islands) remain in raw listing counts and data tables, with null population values and null per-100k rates. Different Census vintages are not mixed.
