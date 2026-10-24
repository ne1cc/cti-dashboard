# Architecture

Clinical Trial Access & Recruitment Competition Intelligence is a local-first,
medallion-architecture analytics platform. Every layer is reproducible from the layer
below it, and the bronze layer is immutable so any downstream logic change can be
replayed against the exact bytes originally received from the source.

## 1. System overview

```mermaid
flowchart TB
    subgraph SRC["Source"]
        CTG["ClinicalTrials.gov API v2<br/>GET /api/v2/studies<br/>paginated JSON (nextPageToken)"]
    end

    subgraph ING["Ingestion (src/ingest)"]
        CLI["src.cli ingest"]
        CLIENT["ctg_client.py<br/>requests + tenacity retries + timeouts"]
        PAGES["pagination.py<br/>follow nextPageToken"]
        VAL["validate_api_payload.py<br/>pydantic envelope checks"]
        QUAR["quarantine<br/>malformed records + reason codes"]
    end

    subgraph BRONZE["Bronze — immutable raw"]
        RAWJSON["data/bronze/&lt;profile_id&gt;/api_responses/<br/>run_id=&lt;id&gt;/page=&lt;n&gt;.json<br/>(unmodified page bytes)"]
        MANIFEST["data/bronze/&lt;profile_id&gt;/manifests/<br/>manifest_&lt;run_id&gt;.json + summary parquet<br/>run times, params, page/response counts, status,<br/>profile (indication_profile_id from silver down)"]
    end

    subgraph SILVER["Silver — normalized (src/transform)"]
        FLAT["flatten_studies.py<br/>nested JSON → tabular"]
        ENTITIES["Parquet entities:<br/>silver_trials · silver_trial_conditions<br/>silver_trial_interventions · silver_trial_sponsors<br/>silver_trial_locations · silver_trial_outcomes"]
        TAX["condition_taxonomy.yml<br/>config-driven ADRD mapping"]
        GEO["geography_rules.yml<br/>US state normalization"]
    end

    subgraph GOLD["Gold — dimensional (dbt + DuckDB)"]
        STG["staging models"]
        INT["intermediate models<br/>status history · site activity<br/>sponsor concentration"]
        MARTS["dims · facts · bridges ·<br/>analytical marts incl.<br/>mart_feasibility_priority_queue"]
    end

    subgraph QA["Quality (src/quality)"]
        PROF["profiling · schema drift ·<br/>reconciliation · DQ report"]
        DBTTEST["dbt tests"]
    end

    subgraph OUT["Delivery"]
        APP["Streamlit dashboard<br/>(reads Gold only)"]
        MEMODOC["Executive memo template"]
    end

    CTG --> CLIENT
    CLI --> CLIENT --> PAGES --> RAWJSON
    PAGES --> MANIFEST
    CLIENT --> VAL --> QUAR
    RAWJSON --> FLAT --> ENTITIES
    TAX --> ENTITIES
    GEO --> ENTITIES
    ENTITIES --> STG --> INT --> MARTS
    MANIFEST --> STG
    ENTITIES --> PROF
    MARTS --> DBTTEST
    MARTS --> APP
    MARTS --> MEMODOC
```

## 2. Layer contracts

### Bronze — immutable raw
- One JSON file per API page per ingestion run: `run_id=<run_id>/page=<page>.json`,
  saved byte-for-byte before any parsing beyond envelope validation.
- One manifest per run: run ID (UTC timestamp + UUID), start/end time, endpoint,
  full query parameters, page count, response count, success/failure state.
- Never edited, never deleted by pipeline code. Re-runs create new run IDs;
  incremental mode can reuse a recent successful run within the configured
  [reuse window](source_documentation.md#incremental-reuse).
- Malformed records go to a quarantine report with reason codes — never dropped
  silently.

### Silver — clean normalized entities
- Flattened, typed, standardized Parquet per entity, grain = source entity ×
  ingestion run (see `docs/data_dictionary.md`, Phase 4+).
- Config-driven condition taxonomy and geography rules (version-controlled YAML;
  no runtime LLM classification).
- Data-quality flags (`record_quality_flag`, `usable_geography_flag`,
  `dementia_relevance_flag`) computed here, not in dashboards.
- All records preserved, including non-U.S. locations excluded from MVP marts.

### Gold — business-ready dimensional model
- Built exclusively by dbt (dbt-duckdb) inside `data/warehouse/clinical_trials.duckdb`.
- Staging → intermediate → marts; each layer documents and tests its grain.
- Snapshot facts turn repeated ingestion runs into longitudinal status history —
  the source API only serves current records.
- The dashboard reads Gold marts only; no dashboard-side business logic.

### Grain contract at every layer

| Layer | Object | Grain |
|---|---|---|
| Bronze | JSON page | API page × ingestion run |
| Silver | `trials` | NCT ID × indication profile × ingestion run |
| Silver | `trial_locations` | profile × NCT ID × run × original location ordinal |
| Gold | `dim_trial` | indication profile × NCT ID (current record) |
| Gold | `fct_trial_snapshot` | indication profile × NCT ID × snapshot date |
| Gold | `fct_trial_site` | indication profile × NCT ID × facility × city × state × snapshot date |
| Gold | `mart_feasibility_priority_queue` | profile × condition group × state × phase × latest snapshot |

Authoritative per-model grain statements live in the dbt model definitions (`dbt_clinical_trials/models/**/_*.yml`), and composite keys are enforced by unique schema tests.

## 3. Snapshot-history design

```mermaid
sequenceDiagram
    participant W as Weekly run
    participant B as Bronze
    participant G as fct_trial_snapshot
    W->>B: run_id=2026-07-24T…-uuid (pages 1..N)
    B->>G: one row per NCT ID per snapshot date
    Note over G: status_changed_from_previous_snapshot_flag<br/>compares consecutive snapshots
    W->>B: run_id=2026-07-31T…-uuid
    B->>G: new snapshot rows — current_record_flag moves
```

Key consequence: history depth equals the age of this project's own snapshot
collection. This is documented, not hidden.

## 4. Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Dependency mgmt | uv | Fast, lockfile-based, reproducible |
| HTTP | requests + tenacity | Timeouts, typed exceptions, exponential backoff |
| Validation | pydantic v2 | Envelope/schema validation with clear errors |
| Storage | Parquet + DuckDB | Local-first, columnar, zero-ops warehouse |
| Modeling | dbt-duckdb | Tested, documented, grain-safe SQL layers |
| Dashboard | Streamlit + Plotly | Fast decision-ready delivery from Gold marts |
| CI | GitHub Actions | Lint, unit tests, dbt build on synthetic fixtures |

## 5. Reproducibility and operations

- The [Makefile command reference](../README.md#development-and-pipeline-commands) covers pipeline order, refresh, checks, cleanup, and DuckDB access.
- All query parameters, taxonomy, geography rules, score weights, and scenario
  assumptions live in `config/*.yml` under version control.
- `.env` supplies optional local overrides to the legacy config loader; the public API requires no key.
- Logging via loguru with run-context fields; no sensitive data is ever logged
  (the source is public, but site contacts/investigators are still excluded from
  all delivery surfaces).

### Configuration reference

| File | Responsibility |
|---|---|
| [`config/project_config.yml`](../config/project_config.yml) | Legacy single-config API/query, paths, ingestion/reuse, retention, scope, and guardrails; used by `load_config` / `get_config` |
| `config/profiles/*.yml` | Indication metadata, API/query/HTTP settings, bronze/quarantine paths, reuse/retention policy, and taxonomy/score-weight references; CLI ingest/transform/orchestrate use the registry |
| [`config/profiles/adrd.yml`](../config/profiles/adrd.yml) / [`config/profiles/full_catalog.yml`](../config/profiles/full_catalog.yml) | ADRD profile / opt-in registry-wide, ingest-only profile |
| [`config/shared_paths.yml`](../config/shared_paths.yml) | Silver, gold, and DuckDB destinations injected into profile configs, overriding those profile path fields |
| `config/condition_taxonomy*.yml` / [`config/geography_rules.yml`](../config/geography_rules.yml) | Profile-selected condition mapping/confidence rules / state-name normalization |
| [`config/score_weights.yml`](../config/score_weights.yml) | Score weights, min-max normalization, and priority bands; weights mirror the dbt seed and bands mirror dbt vars, checked by pytest |
| [`config/similarity_weights.yml`](../config/similarity_weights.yml) | Comparability factor weights mirrored in the similarity dbt seed, checked by pytest |
| [`config/roi_assumptions.yml`](../config/roi_assumptions.yml) | Scenario-calculator assumptions and disclaimer |
| [`dbt_clinical_trials/dbt_project.yml`](../dbt_clinical_trials/dbt_project.yml) / `dbt_clinical_trials/profiles.yml` | Schema routing, seed types, and score-band vars / dbt connection settings (created from the example at setup) |
| `.env` / [`.env.example`](../.env.example) | Optional local environment settings / sample values; loader behavior is described below |

| Environment variable | Effect |
|---|---|
| `CTI_PROJECT_ROOT` | Root for Python configuration and relative data paths; unset or `.` auto-detects the repository root |
| `CTI_CONFIG_PATH` | Selects the legacy YAML file (default `config/project_config.yml`); an explicit `load_config` path takes precedence |
| `CTI_HTTP_TIMEOUT_SECONDS` / `CTI_MAX_RETRIES` | Override HTTP timeout / retry-attempt limit in the legacy loader |
| `CTI_LOG_LEVEL` | Loguru level, default `INFO` |
| `CTI_REFRESHABLE_PROFILES` | Comma-separated allowlist for registry refreshable profiles and dashboard choices; unset includes all non-ingest-only profiles; unknown or empty selections fail |
| `DBT_PROFILES_DIR` | dbt profiles-directory setting; Makefile commands explicitly pass their own profiles directory |

[`src/config.py`](../src/config.py) loads `.env` with existing process variables taking
precedence. [`src/profiles.py`](../src/profiles.py) reads profile YAML and shared paths
directly: `CTI_CONFIG_PATH` and the HTTP overrides above do not override registry
profiles. Set their HTTP values in profile YAML. Export variables needed by paths,
logging, or the registry in the launching process. dbt connection paths follow its
own profile and working directory; `CTI_PROJECT_ROOT` does not redirect dbt.
The sample `CTI_DATA_DIR`, `CTI_BRONZE_DIR`, `CTI_SILVER_DIR`, `CTI_GOLD_DIR`, and
`CTI_DUCKDB_PATH` entries are not read by the current Python loaders; change YAML
paths (and dbt connection settings) instead. Container refresh-loop settings are
documented in the [Fly deployment runbook](DEPLOY_FLY.md).

## 6. Scaling path (implemented extensions, and the roadmap beyond them)

- **Full-catalog bronze ingestion (implemented, opt-in).** `config/profiles/full_catalog.yml`
  drops the `query.cond`/`filter.*` params entirely and runs against a parallel
  `data/bronze/full_catalog/` tree (`make ingest-full-catalog`), snapshotting the
  entire ClinicalTrials.gov registry (~600k+ studies, estimated) instead of just
  ADRD/US. It shares every ingestion primitive with the default profile
  (`iter_pages`, `CTGClient`, manifest/reuse logic) — only the config differs —
  and is invisible to `make transform`/`dbt-run`/dashboard: the profile is marked
  `ingest_only`, so `transform --profile full_catalog` is refused with exit code 2
  rather than writing a taxonomy-less silver tree, and the default transform reads
  only `data/bronze/adrd/manifests/`.
- **Multi-indication (implemented).** An indication profile is one YAML file in
  `config/profiles/`, the registry loads all of them, and `make orchestrate`
  refreshes every profile that is not `ingest_only` into the *same* warehouse. The
  trial grain is `(nct_id, indication_profile_id)`, carried through staging,
  intermediate and mart models, while silver, gold and the DuckDB file stay shared
  (`config/shared_paths.yml`) — the profile column is what keeps two indications
  from cross-joining on a shared date. Reconciliation is per profile
  (`src/quality/reconciliation.py` binds `indication_profile_id` into every check),
  the dashboard serves one profile at a time (`dashboard/components/profile.py`
  renders the selector, `dashboard/components/data.py` binds it into every query),
  and `make prune-data` keeps `bronze_runs_to_keep` *success* runs per profile —
  the horizon is declared in that profile's `retention:` block, not in code
  (`src/utils/retention.py` refuses a bronze horizon deeper than the snapshot
  horizon). Adding an indication means adding a config file and a taxonomy, not
  forking the pipeline: `config/profiles/oncology_nsclc.yml` is the worked example.
- **Chunked silver transform (implemented).** `SilverRunWriter`
  (`src/transform/export_parquet.py`) streams flattened rows into one Parquet
  file per entity per run, flushing a row group every 50k rows against fixed
  `ENTITY_ARROW_SCHEMAS` (pandas inference cannot survive chunking: an all-null
  chunk would infer Arrow `null`), staging to `.tmp` and atomically renaming on
  close. `build_silver_for_run` no longer materializes a full run in memory;
  profiling (`src/quality/profiling.py`) is a single DuckDB streaming aggregate
  instead of a full pandas re-read. Every profile `transform` accepts writes
  through this same writer into the single shared silver root
  (`data/silver/`, from `config/shared_paths.yml`); gold, dbt, and the dashboard
  read that root, so a profile reaches the warehouse only once it has a taxonomy
  and is no longer `ingest_only`.
- Extending `condition_taxonomy.yml` and `geography_rules.yml` beyond their
  current ADRD/US-only scope (planned): the prerequisite before full-catalog
  silver data is meaningful in the marts.
- Swap dbt-duckdb profile for BigQuery/Snowflake; models are ANSI-leaning by design.
- Wrap CLI stages as Dagster/Airflow tasks keyed by `ingestion_run_id`.
- Add ACS population and CDC/ATSDR SVI layers only after the ClinicalTrials.gov-only
  MVP is complete, tested, and documented.
