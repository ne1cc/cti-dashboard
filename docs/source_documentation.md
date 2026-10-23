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
Defined in `config/project_config.yml` and verified against the live API
on 2026-07-24 (HTTP 200, `totalCount` 2,592 for the MVP query):

| Parameter | Value (MVP) | Notes |
|---|---|---|
| `query.cond` | `Alzheimer Disease` | condition query |
| `filter.overallStatus` | `RECRUITING\|ACTIVE_NOT_RECRUITING\|NOT_YET_RECRUITING\|COMPLETED` | pipe-joined list |
| `filter.advanced` | `AREA[StudyType]INTERVENTIONAL` | Essie expression |
| `format` | `json` | |
| `pageSize` | `100` | |
| `countTotal` | `true` | enables reconciliation vs `totalCount` |
| `pageToken` | from previous page's `nextPageToken` | followed until absent |

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

## Terms of use
ClinicalTrials.gov data is publicly available. This project stores
unmodified raw copies, attributes the source in every deliverable, and
adds interpretation guardrails because registry listings do not describe
trial conduct, site performance, or patient availability.

## U.S. Census Bureau Resident Population Estimates (Vintage 2024)

- **Source:** U.S. Census Bureau, Population Division, Population Estimates Program (PEP).
- **Dataset:** State Population Totals: 2020-2024 (`NST-EST2024-ALLDATA.csv`).
- **Official URL:** `https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/state/totals/NST-EST2024-ALLDATA.csv`
- **File Layout Specification:** `https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2020-2024/NST-EST2024-ALLDATA.pdf`
- **Estimate Date:** July 1, 2024 (`POPESTIMATE2024`).
- **Vintage:** Vintage 2024.
- **Retrieval Date:** October 2026.
- **Source File SHA-256:** `b8b50bd345d8a5f33d5c8a28c95aeaa0a50971356f000d9b48276a65fd9022a7`.
- **Scope & Filtering:** Filtered to individual state records (`SUMLEV = '040'`), comprising the 50 states, the District of Columbia, and Puerto Rico (52 rows). National and regional summary rows are excluded. State postal abbreviations are mapped via `config/geography_rules.yml`.
- **Purpose:** Static interim denominator seed (`dbt_clinical_trials/seeds/state_population.csv`) for calculating per-100k resident recruiting trial listing density (`recruiting_trials_per_100k`) in `mart_condition_geography_trends`.
