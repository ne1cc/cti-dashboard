# Data Dictionary

Layers: **bronze** (immutable raw JSON), **silver** (normalized Parquet),
**gold** (dbt models in DuckDB: `main_staging`, `main_intermediate`,
`main_marts`, `main_seeds`).

Conventions: `nct_id` matches `^NCT\d{8}$`; `*_normalized` columns are
lowercased, punctuation-stripped text for matching only (display columns
keep original casing); `*_raw` preserves the source value whenever parsing
may lose information; all timestamps are UTC.

---

## Bronze

### `data/bronze/<profile_id>/api_responses/run_id=<id>/page=NNNNN.json`
Verbatim API v2 page payloads. Never edited after write.

### `data/bronze/<profile_id>/manifests/manifest_<run_id>.json`
| Field | Type | Description |
|---|---|---|
| ingestion_run_id | text | `<UTC compact timestamp>_<uuid8>` |
| query_hash | text | SHA-256 of the canonical query params |
| condition / mode / status | text | status ∈ running, success, partial, failed |
| profile | text | the profile directory the run wrote into; `indication_profile_id` in silver and every mart |
| started_at_utc / ended_at_utc | timestamp | run window |
| page_count / record_count | int | pages fetched, studies written |
| total_count_reported | int | API `totalCount` for reconciliation |
| quarantined_record_count | int | invalid records set aside, never dropped silently |

Also: `_schema_baseline.json` and `_schema_drift_<run_id>.json`, both written
beside a profile's `api_responses/` directory — one baseline per profile, not one
per repository (see data quality framework).

---

## Silver (one Parquet file per entity per run: `run_id=<id>.parquet`)

### `silver_trials` — grain: NCT ID × ingestion run
| Column | Type | Description |
|---|---|---|
| nct_id | text | registry identifier (validated) |
| ingestion_run_id / snapshot_date / snapshot_timestamp_utc | text/date/ts | provenance |
| brief_title / official_title | text | registry titles |
| overall_status | text | registry status at snapshot time |
| study_type | text | INTERVENTIONAL in MVP scope |
| phase_normalized | text | multi-phase joined "/", NA→NOT_APPLICABLE, null→UNKNOWN |
| enrollment_count / enrollment_type | int/text | planned or actual per registry |
| start_date, primary_completion_date, completion_date, study_first_post_date, results_first_post_date (+ `*_raw`) | date/text | partial dates parsed leniently (YYYY, YYYY-MM, YYYY-MM-DD) |
| lead_sponsor_name / lead_sponsor_normalized | text | lead sponsor |
| has_results_flag | bool | registry `hasResults` |
| record_quality_flag | text | `ok` or first failed check (see quality framework) |
| source_json_hash | text | SHA-256 of the study JSON |

### `silver_trial_conditions` — grain: NCT × condition × run
`condition_raw`, `condition_normalized`, `condition_group` (config-driven
taxonomy), `mapping_confidence` (high/medium/low), `dementia_relevance_flag`.

### `silver_trial_interventions` — grain: NCT × intervention × run
`intervention_type`, `intervention_name`, `intervention_name_normalized`.

### `silver_trial_sponsors` — grain: NCT × sponsor × role × run
`sponsor_name`, `sponsor_normalized`, `sponsor_role`
(`lead_sponsor`/`collaborator`), `sponsor_class` (registry agency class).

### `silver_trial_locations` — grain: profile × NCT × run × original location ordinal
`facility_name`, `facility_normalized`, `city`, `state_raw`,
`state_normalized` (2-letter code via `config/geography_rules.yml`),
`zip_code`, `country`, `latitude`, `longitude`, `location_status`,
`us_location_flag`, `usable_geography_flag`. **Facility names are not
stable unique site identifiers.** No contact or investigator fields exist
anywhere in this project.

### `silver_trial_outcomes` — grain: NCT × outcome × run
`outcome_type` (primary/secondary), `measure`, `time_frame`. Descriptive
text only — never interpreted clinically.

---

## Gold: staging (`main_staging`, views)
Typed 1:1 reads of silver plus `stg_trial_snapshots` (manifest rows) and
`stg_trial_contacts` — **deliberately empty** (`where 1=0`) privacy
guardrail.

Every grain in the three Gold sections below is additionally scoped by
`indication_profile_id`: the multi-indication migration made the trial grain
`(nct_id, indication_profile_id)`, and the column is carried from the bronze
manifest's `profile` field through staging into every intermediate, dimension,
fact, bridge and analytical mart.

## Gold: intermediate (`main_intermediate`, views)
| Model | Grain | Purpose |
|---|---|---|
| int_trial_status_history | NCT × complete snapshot date | previous_status, entered/left_recruiting flags, first_seen |
| int_current_trial_status | NCT | latest record + active_in_latest_snapshot_flag |
| int_trial_condition_mapping | NCT × condition_group × run | deduped taxonomy mapping |
| int_geography_normalized | usable U.S. location rows | gate for U.S.-scope marts |
| int_trial_site_activity | NCT × facility × snapshot | listing signal |
| int_condition_geography_activity | NCT × condition_group × state × snapshot | shared segment grain |
| int_sponsor_concentration | segment × snapshot | top_sponsor_share, sponsor_hhi |

## Gold: marts (`main_marts`, tables)

### Dimensions
| Model | Grain | Key |
|---|---|---|
| dim_trial | NCT ID (current record) | trial_key |
| dim_condition | condition_normalized | condition_key |
| dim_sponsor | sponsor_normalized | sponsor_key |
| dim_geography | U.S. state | geography_key (county/metro columns reserved, null) |
| dim_date | day, 1999 → +3y | date_day |

### Facts and bridges
| Model | Grain |
|---|---|
| fct_trial_snapshot | NCT × snapshot date (`snapshot_key`, `record_hash`, `current_record_flag`, condition/site counts) |
| fct_trial_site | NCT × facility × snapshot date |
| bridge_trial_condition | NCT × condition_group (current) |
| bridge_trial_sponsor | NCT × sponsor × role (current) |

### Analytical marts
| Model | Grain | Highlights |
|---|---|---|
| mart_trial_activity | snapshot × segment × status | listing counts, entered/left recruiting |
| mart_recruiting_competition | snapshot × segment (RECRUITING) | recruiting count, 30/90-day windows, HHI, `competition_signal_band` |
| mart_site_overlap | snapshot × facility | multi-trial facilities, phase_mix |
| mart_condition_geography_trends | month × condition_group × state | 3-month rolling baseline |
| mart_data_reliability | ingestion run | reconciliation + usability shares |
| mart_feasibility_priority_queue | segment @ latest snapshot | score, band, rank, deterministic explanation, `interpretation_note` |

Full column-level metric semantics: `docs/metric_definitions.md`.

## Audit provenance and contracted marts

All silver entities carry `indication_profile_id`, `snapshot_id` (immutable ingestion
run ID), nullable `retrieved_at_utc`, `raw_page_reference` relative to the configured
bronze API response root (normally `data/bronze/<profile>/api_responses`; the reference
path alone is `run_id=<id>/page=00001.json`), zero-based `raw_study_ordinal`, and canonical
`source_json_hash`. Historical receipt metadata is unknown, not reconstructed.
Page receipts live separately at
`data/bronze/<profile_id>/api_responses/run_id=<id>/_page_metadata/page=<number>.json`;
the original page body stays unchanged. Original locations carry zero-based
`location_ordinal`, preserving identical reported rows as distinct record identities.

| Mart | Contracted grain | Fields and meaning |
|---|---|---|
| `mart_study_snapshot_audit` | profile × successful snapshot ID × NCT ID | provenance; start/end UTC; snapshot date; overall status; raw/parsed first-post date and posted update; verification text/precision/month age; enrollment count/type/category; recorded/usable/missing-geography/missing-facility counts; geography category; confirmed-recruiting flag; base state inclusion/exclusion |
| `mart_location_snapshot_audit` | study audit key × original location ordinal | parent study lineage; original and normalized facility/city/state/country; coordinates; reported location status; geography category and usable flag; missing-facility warning; no enrollment allocation |

Successful-run joins retain multiple runs on one UTC date. Staged manifest and study
timestamps/dates explicitly normalize to UTC. Study geography is usable if any
original location is usable, otherwise missing when required fields/locations are
absent, otherwise unsupported. Filter-specific inside/outside/undetermined membership
is computed from original locations, not inferred solely from the study category.

`enrollment_category` is `estimated`, `actual`, `missing`, or `unknown_type`.
`verification_date_precision` is `day`, `month`, `year`, `missing`, or `unparseable`.
Source omissions and missing historical retrieval metadata remain nullable. Audit
keys are record identities, not durable facility identities. See
[metric definitions](metric_definitions.md) for clock formulas, coverage denominators,
filter decisions and JSON fields. Raw references depend on configured bronze retention.
