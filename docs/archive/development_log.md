> [!NOTE]
> Historical build record. Engineering test/model/seed/analysis inventories were
> omitted during archival; measured dataset results and incident evidence are retained.

# Development Log — Clinical Trial Access & Recruitment Competition Intelligence

A chronological record of every step taken to build this project, including
verification steps, live checks, fixes, and design decisions. Updated at the end
of each phase.

---

## Phase 1 — Foundation (2026-07-24)

1. **Created the repository tree** under `clinical-trial-intelligence/` with
   `mkdir -p`: `data/{bronze/{api_responses,manifests},silver,gold,warehouse}`,
   `config/`, `docs/`, `src/{ingest,transform,quality,utils}`,
   `dbt_clinical_trials/{macros,seeds,models/{staging,intermediate,marts},tests}`,
   `analyses/`, `dashboard/{components,pages}`, `tests/`, `.github/workflows/`.
   Added `.gitkeep` files so git-ignored data directories survive cloning.
2. **Wrote `pyproject.toml`** — uv-managed, Python ≥3.11, hatchling build with
   `src` package. Runtime deps: httpx, tenacity, pydantic v2, pandas, pyarrow,
   duckdb, python-dotenv, loguru, pyyaml, dbt-core, dbt-duckdb, streamlit,
   plotly. Dev group: pytest, pytest-cov, respx (HTTP mocking), ruff.
3. **Wrote `.gitignore`** — excludes all bronze/silver/gold data, the DuckDB
   warehouse, `.env`, dbt `target/`/`profiles.yml`, Python caches.
4. **Wrote `.env.example`** — paths, log level, optional HTTP overrides. No
   secrets exist: the ClinicalTrials.gov API is public and unauthenticated.
5. **Wrote `LICENSE`** — MIT plus a data-source notice (ClinicalTrials.gov terms;
   portfolio demonstration, not clinical decision support).
6. **Wrote `Makefile`** — single entry point: `setup`, `ingest`, `full-refresh`,
   `transform`, `dbt-run`, `dbt-test`, `dbt-docs`, `quality-report`, `dashboard`,
   `pipeline`, `test`, `lint`, `format`, `clean`, self-documenting `help`.
7. **Verified API parameter syntax against the live API** (spec requirement
   before coding). Findings:
   - The docs page (`/data-api/api`) is JavaScript-rendered — not fetchable as text.
   - The published OpenAPI YAML URL `…/api/oas/v2/ctg-oas-v2.yaml` returns **404**;
     the working spec URL is `https://clinicaltrials.gov/api/oas/v2` (HTTP 200).
   - Live `curl` test confirmed: `query.cond`, `filter.overallStatus`
     (pipe-separated enums), `filter.advanced` with Essie expression
     `AREA[StudyType]INTERVENTIONAL`, `pageSize`, `countTotal=true` — HTTP 200,
     `totalCount: 2592` studies for the project scope.
8. **Wrote `config/project_config.yml`** — every API query parameter lives here
   (never hard-coded), plus HTTP retry/timeout settings, scope declaration,
   paths, ingestion mode defaults, and the guardrail disclaimer.
9. **Wrote `README.md`** — all 25 required sections (setup complete; later-phase
   sections marked with their phase), Mermaid architecture + ER diagrams.
10. **Wrote `docs/architecture.md`** — medallion layer contracts, ingestion
    sequence diagram, snapshot-history design, technology rationale, scaling path.
11. **Ran `uv lock`** — 99 packages resolved cleanly; `uv.lock` committed for
    reproducibility.

**Key decisions:** local-first (DuckDB + Parquet); bronze layer immutable;
history is constructed from this project's own repeated snapshots because the
API only serves current records.

---

## Phase 2 — API ingestion (2026-07-24)

1. **Wrote `src/utils/`**:
   - `paths.py` — project-root discovery (`CTI_PROJECT_ROOT` override), path
     resolution, `ensure_dir`.
   - `logging.py` — single loguru configuration, level from `CTI_LOG_LEVEL`.
   - `dates.py` — timezone-aware UTC helpers (`utc_now`, compact run-ID stamp).
   - `hashing.py` — SHA-256 of text and canonical JSON (stable key order).
2. **Wrote `src/config.py`** — pydantic-typed loader for `project_config.yml`
   (`ApiConfig`, `HttpConfig`, `PathsConfig`, `IngestionConfig`) with `.env`
   overrides for timeout/retries; `lru_cache`d `get_config()`.
3. **Wrote `src/ingest/retry_policy.py`** — tenacity `Retrying` with exponential
   backoff, retry on 429/500/502/503/504 (`RetryableHTTPStatusError`), timeouts,
   and transport errors; warning log before each sleep; `reraise=True`.
4. **Wrote `src/ingest/ctg_client.py`** — httpx client with timeout and
   User-Agent; `build_params()` renders config params (list filters joined with
   `|` per v2 syntax; `--condition` overrides `query.cond`); `fetch_page()`
   returns parsed payload **and** unmodified response text (bronze saves the
   exact bytes); context-manager support.
5. **Wrote `src/ingest/pagination.py`** — generator following `nextPageToken`
   until absent; optional `max_pages` cap for smoke tests.
6. **Wrote `src/ingest/validate_api_payload.py`** — pydantic envelope validation
   (`studies`, `nextPageToken`, `totalCount`); per-record screening producing
   quarantine records with reason codes (`NOT_AN_OBJECT`, `MISSING_NCT_ID`,
   `INVALID_NCT_ID_FORMAT` vs `^NCT\d{8}$`); quarantine report writer. Nothing
   is silently dropped.
7. **Wrote `src/ingest/snapshot_manifest.py`** — `IngestionManifest` model
   (run ID, query hash, endpoint, params, timings, page/record counts,
   `totalCount` reported, quarantine count, status, error); run IDs =
   UTC timestamp + UUID fragment; JSON manifest + one-row Parquet/CSV summary;
   `find_reusable_run()` implements incremental mode (skip re-download of a
   successful identical query within `reuse_window_hours`).
8. **Wrote `src/ingest/extract_studies.py`** — orchestrator: build params →
   query hash → incremental reuse check → create `run_id=<id>` directory →
   save each raw page as `page=NNNNN.json` → validate envelope → screen records
   → write manifest/summary/quarantine in a `finally` block (failure states are
   recorded, not lost).
9. **Refinement:** runs stopped by `--max-pages` while the API still had a
   `nextPageToken` are marked **`partial`** (not `success`) so they are never
   reused by incremental mode and never feed downstream metrics.
10. **Wrote `src/cli.py`** — `python -m src.cli ingest --condition … [--full-refresh]
    [--max-pages N]`; nonzero exit on failure.
11. **Wrote unit tests** (all HTTP mocked with respx; no network):
    - `tests/test_ctg_client.py` — param rendering (pipe-joined statuses,
      paging params), condition override, success path, retry-then-succeed on
      503, no retry on 400, retries exhausted raises after 3 attempts.
    - `tests/test_pagination.py` — follows `nextPageToken`, `max_pages` cap,
      single-page stop.
    - `tests/test_snapshot_manifest.py` — run-ID shape/uniqueness, manifest
      round-trip, reuse-window logic (ignores other-hash/failed/partial/stale),
      most-recent-wins, Parquet/CSV summary contents.
12. **Wrote `.github/workflows/ci.yml`** — uv setup → `ruff check` → `pytest`.
13. **Fixed 4 ruff findings** (two long lines, `datetime.UTC` alias, import
    order). Result: lint clean, **tests passing**.
14. **Live smoke test:** `python -m src.cli ingest --condition "Alzheimer Disease"
    --max-pages 2` → run `20260724T203236Z_cade2517`, 2 pages / 200 studies
    saved, 0 quarantined, `totalCount` 2,592, status correctly `partial`,
    manifest verified on disk.

---

## Phase 3 — Bronze-to-Silver transformation (in progress)

1. **Inspected real bronze JSON** to ground the flattening code in actual field
   names (not assumptions). Confirmed structure:
   - Top level: `protocolSection`, `resultsSection`, `derivedSection`, `hasResults`.
   - `statusModule`: `overallStatus`, `statusVerifiedDate`, `expandedAccessInfo.
     hasExpandedAccess`, `startDateStruct`, `primaryCompletionDateStruct`,
     `completionDateStruct`, `studyFirstPostDateStruct`,
     `resultsFirstPostDateStruct`, `lastUpdatePostDateStruct` (dates may be
     partial, e.g. `"2009-11"`).
   - `designModule`: `studyType`, `phases` (list), `enrollmentInfo{count,type}`.
   - `sponsorCollaboratorsModule`: `leadSponsor{name,class}`, `collaborators`,
     `responsibleParty{type}`.
   - `eligibilityModule`: `eligibilityCriteria`, `healthyVolunteers`, `sex`,
     `minimumAge`/`maximumAge`, `stdAges`.
   - `contactsLocationsModule.locations[]`: `facility`, `city`, `state`, `zip`,
     `country`, `geoPoint{lat,lon}`, optional `status`. Location contacts are
     deliberately **not** extracted (privacy guardrail).
   - `outcomesModule`: `primaryOutcomes`/`secondaryOutcomes` (+`otherOutcomes`)
     with `measure`, `description`, `timeFrame`.
2. **Wrote `config/condition_taxonomy.yml`** — deterministic, ordered ADRD
   taxonomy (alzheimers_disease, mild_cognitive_impairment, lewy_body_dementia,
   frontotemporal_dementia, vascular_dementia, parkinsons_disease_dementia,
   dementia_unspecified catch-all, cognitive_impairment_other, default
   non_dementia_other). First match wins; exact → high confidence, substring →
   medium, fallback → low. No runtime LLM classification.
3. **Wrote `config/geography_rules.yml`** — U.S. country aliases, valid state
   abbreviations (50 + DC + territories), full-name→abbreviation map, UNKNOWN
   sentinel. Non-U.S. records preserved and flagged, never dropped.
4. **Added `src/utils/text.py`** (`normalize_text`: lowercase, apostrophes
   removed, punctuation→spaces, whitespace collapsed) and
   `parse_partial_date` in `src/utils/dates.py` (handles YYYY / YYYY-MM /
   YYYY-MM-DD registry dates).
5. **Wrote `src/transform/normalize_conditions.py`** — YAML-driven
   `ConditionTaxonomy.map_condition()` returning normalized text, group,
   dementia-relevance flag, and mapping confidence.
6. **Wrote `src/transform/normalize_locations.py`** — YAML-driven state
   normalization, U.S. flags, `usable_geography_flag`, `geo_scope`
   (facility/city/state/country/unknown). No geocoding in MVP.
7. **Wrote `src/transform/flatten_studies.py`** — one study → rows for all six
   silver entities; per-study `source_json_hash`; phase normalization
   (multi-phase joined `PHASE1/PHASE2`, `NA` → `NOT_APPLICABLE`, missing →
   `UNKNOWN`); `record_quality_flag` codes (missing status/type,
   start_after_completion, results_before_first_post, negative_enrollment).
   Location contacts/investigators deliberately never extracted.
8. **Wrote `src/transform/export_parquet.py`** — per-entity per-run Parquet:
   `data/silver/<entity>/run_id=<id>.parquet`.
9. **Wrote `src/transform/build_silver_entities.py`** — processes
   status=`success` runs only by default (partial/failed excluded from silver);
   skips already-transformed runs unless `--force`; dedupes repeated NCT IDs
   within a run (kept-first, logged); reconciles silver row count against the
   manifest.
10. **Wrote `src/quality/profiling.py`** — per-entity row counts, null rates,
    distinct NCT IDs, manifest reconciliation; JSON report at
    `data/silver/_profiles/profile_<run_id>.json`.
11. **Extended `src/cli.py`** — `transform` subcommand (`--run-id`, `--force`),
    auto-profiles every processed run.
12. **Wrote `tests/test_normalization.py`** — tests for text/date helpers,
    taxonomy ordering (specific groups beat the dementia catch-all), default
    low-confidence fallback, state normalization, non-U.S. flagging, phase
    normalization, quality flags, and a full fixture-study flatten across all
    six entities. Fixed 4 ruff long-line findings via `ruff format`.
    **Result: tests passing, lint clean.**
13. **Live verification on real data:**
    - Full ingest: run `20260724T203950Z_46dc682b`, 26 pages, **2,592 studies**
      (matches API `totalCount`), 0 quarantined, status `success`.
    - Transform: silver_trials 2,592 · conditions 5,289 · interventions 4,898 ·
      sponsors 4,642 · locations 25,819 · outcomes 19,907 rows.
    - Reconciliation: counts match manifest; NCT IDs unique; all quality flags `ok`.
    - Sanity signals: phases mix led by NOT_APPLICABLE/PHASE2/PHASE1; condition
      groups led by alzheimers_disease (2,318 rows); 12,419 usable U.S.
      locations (top states FL, CA, NY, TX, AZ).

**Key decisions:** partial/failed runs can never reach silver by default;
duplicates and missing-NCT records are counted and logged, not silently
dropped; all mapping logic lives in version-controlled YAML.


---

## Phase 4 — dbt Warehouse: Staging, Intermediate, Marts (2026-07-24)

1. **Scaffolded the dbt project** — `dbt_clinical_trials/dbt_project.yml`
   (staging/intermediate as views, marts as tables, custom schemas per layer)
   and `profiles.yml.example` (DuckDB at `data/warehouse/clinical_trials.duckdb`).
   dbt is always invoked from the repo root via
   `--project-dir/--profiles-dir dbt_clinical_trials` so the relative Parquet
   paths in sources resolve consistently (same convention as the Makefile).
2. **Wrote 4 macros** — `safe_divide` (null on zero denominator),
   `generate_surrogate_key` (md5 of `||`-joined coalesced parts),
   `normalize_text` (SQL mirror of the Python normalizer),
   `parse_partial_date` (coalesced `try_strptime` for YYYY / YYYY-MM /
   YYYY-MM-DD registry dates).
3. **Wrote seeds** — `status_mapping` (14 statuses), `phase_mapping`
   (ordering + MVP scope), `feasibility_score_weights` (5 components summing
   to 1.0, consumed in Phase 5).
4. **Defined sources** (`_sources.yml`) — silver Parquet via
   `external_location: read_parquet('data/silver/<entity>/*.parquet',
   union_by_name=true)`; bronze manifests from `summary_*.parquet`.
5. **Wrote staging models** — snapshots, trials (partial-date parsing with
   `*_raw` preserved), conditions, interventions, sponsors, locations,
   outcomes, and **`stg_trial_contacts` as a deliberately empty model**
   (`where 1 = 0`) so the privacy guardrail is enforced structurally.
6. **Wrote intermediate models** — status history from complete snapshots
   only (QUALIFY dedupe, named window for previous-status / entered- /
   left-recruiting flags), current status, condition mapping, geography
   (usable U.S. rows), site activity, condition-geography activity (single
   shared grain feeding all geography marts), sponsor concentration
   (top share + HHI via `safe_divide` + `power`).
7. **Wrote mart models** — `dim_trial`, `dim_condition`, `dim_sponsor`,
   `dim_geography` (state grain), `dim_date` (1999 → +3y spine);
   `fct_trial_snapshot` (snapshot grain, `record_hash`,
   `current_record_flag`, condition/site counts); `fct_trial_site`
   (trial × facility × snapshot); `bridge_trial_condition` /
   `bridge_trial_sponsor` (current-snapshot grain, surrogate keys);
   `mart_trial_activity`; `mart_recruiting_competition` (density proxy =
   recruiting listing count, 30/90-day transition windows via
   `RANGE BETWEEN INTERVAL ... PRECEDING`, `newly_posted_90d_proxy` from
   registry first-post date, sponsor concentration join, and
   `competition_signal_band` from `percent_rank` — <0.5 low, <0.8 moderate,
   else elevated); `mart_site_overlap` (facility grain, phase mix via
   `string_agg`, `repeated_site_participation_flag`, no "overloaded"
   language); `mart_condition_geography_trends` (monthly, 3-month rolling
   baseline); `mart_data_reliability` (run-level reconciliation +
   missing-data shares).
8. **Wrote schema tests** (`_staging.yml`, `_marts.yml`) — not_null / unique /
   accepted_values / relationships across keys and grains — plus **singular
   tests**: `assert_valid_study_dates` (warn),
   `assert_one_current_record_per_trial`,
   `assert_trial_site_relationship_integrity`, `assert_valid_us_state`,
   `assert_snapshot_completeness` (warn).
9. **Errors found and fixed during live runs:**
   - `dim_trial` Binder Error: ambiguous `nct_id` in the surrogate key after
     the join → qualified to `c.nct_id`.
   - `bridge_trial_sponsor` used `'LEAD_SPONSOR'` but silver stores
     `'lead_sponsor'` → fixed casing.
   - `_marts.yml` referenced `state_normalized` on `dim_geography`, which
     exposes it as `state_code` → fixed column name.
   - `not_null_mart_site_overlap_facility_normalized` failed with 266 rows:
     real listings with no facility name. Decision: exclude them from the
     overlap mart only (identity required for overlap), keep them upstream.
10. **Live verification** (`dbt build`): **passing**, 0 warnings. Row counts: dim_trial 2,592 (= ingest),
    fct_trial_snapshot 2,592, fct_trial_site 12,244, bridges 4,037 / 4,642,
    mart_recruiting_competition 449 segments (230 low / 140 moderate /
    79 elevated), mart_site_overlap 6,241 facilities (top overlap:
    Massachusetts General Hospital, 7 recruiting trials), reliability mart
    reconciled=true, unique NCT=true for the success run; the earlier
    partial run is visible but carries no silver-derived metrics.

**Key decisions:** all snapshot-transition metrics honestly report 0/null
until multi-snapshot history accrues (registry first-post date is a labeled
proxy, not a substitute); the empty contacts model makes the privacy rule a
build-time artifact; partial runs surface in the reliability mart but never
feed analytical marts.

---

## Phase 5 — Feasibility Score, Scenarios, and Quality Framework (2026-07-24)

1. **Wrote `config/score_weights.yml`** — Python/dashboard-side mirror of the
   `feasibility_score_weights` dbt seed (weights sum to 1.0), min-max
   normalization rule, priority-band thresholds (review ≥ 0.45,
   priority_review ≥ 0.70), and data-confidence sub-weights (0.5 record
   quality + 0.5 location usability). Band thresholds are also declared as
   dbt `vars` in `dbt_project.yml`; `tests/test_metrics.py` enforces that
   YAML, seed CSV, and dbt vars never diverge.
2. **Wrote `mart_feasibility_priority_queue.sql`** — grain: condition_group ×
   state × phase at the latest complete snapshot. Components (each min-max
   normalized to 0..1, degenerate spread → 0): recruiting listing density,
   recent recruiting growth (snapshot transitions once history accrues;
   until then the registry first-post-date proxy, exposed via
   `growth_uses_registry_proxy_flag`), sponsor HHI, share of trials listing
   a multi-trial facility, and a data-confidence adjustment. Weights are
   read from the seed at build time; deterministic `priority_explanation`
   is assembled from fixed component phrases and every row carries an
   `interpretation_note` ("not a recruitment forecast; requires human
   feasibility review").
3. **Added tests for the queue** — schema tests (unique key, not-null score/
   explanation, accepted band values) plus singular
   `assert_feasibility_score_within_bounds` ([0, 1]).
4. **Wrote `config/roi_assumptions.yml` + `src/analysis/roi_scenarios.py`** —
   scenario calculator that only multiplies user-editable assumptions
   (conservative/base/optimistic multipliers); pydantic-validated, every
   output carries the "illustrative, not observed outcomes" disclaimer;
   `render_markdown` for the Phase 7 memo and Phase 6 dashboard.
5. **Wrote dbt analyses** — top priority segments, sponsor landscape,
   site-overlap hotspots, data-reliability trend (all `ref`-based).
6. **Wrote `src/quality/schema_drift.py`** — records observed bronze field
   paths (depth 3) per run; first run creates
   `data/bronze/_schema_baseline.json`; later runs produce drift reports
   with added/removed paths; baseline updates only by explicit
   `--update-schema-baseline`.
7. **Wrote `src/quality/reconciliation.py`** — cross-layer checks per
   success run (manifest vs silver rows, NCT uniqueness) plus warehouse
   checks (dim_trial vs latest silver, current-record cap).
8. **Wrote `src/quality/data_quality_report.py` + CLI `quality-report`** —
   Markdown report (`reports/data_quality_report.md`) combining the
   reliability mart, reconciliation results, schema-drift status, and the
   interpretation guardrails; wired to the existing `make quality-report`.
9. **Wrote `tests/test_metrics.py`** — tests for weight sums, YAML↔seed↔dbt
   var consistency, score boundedness (via in-memory DuckDB), degenerate
   normalization → 0, HHI math (0.375 fixture / 1.0 solo), ROI arithmetic
   and disclaimer propagation. One fix: DuckDB returns `Decimal` from
   VALUES literals → cast to float in the test.
10. **Live verification:** priority queue = 449 segments (75 review /
    374 watch, none ≥ 0.70 yet — expected with single-snapshot history);
    top segment alzheimers_disease × FL × PHASE3 (score 0.6263). Full
    `dbt build`: **passing.** Pytest: **passing.** Ruff clean.
    `quality-report` run live: 4/4 reconciliation checks passed, schema
    baseline created (125 field paths), report written.

**Key decisions:** score inputs and thresholds live in three synchronized,
version-controlled places (YAML for Python, seed for SQL, vars for banding)
with a test that fails on divergence; the growth component openly labels
its proxy source instead of pretending transition history exists; ROI
figures can never be presented without their disclaimer because it is
embedded in every result object.

---

## Phase 6 — Streamlit Dashboard (2026-07-24)

1. **Shared components** (`dashboard/components/`):
   - `data.py` — read-only cached DuckDB access (`st.cache_resource`
     connection + `st.cache_data` queries); `require_warehouse()` stops
     pages with build instructions when the warehouse is missing; one
     query helper per page.
   - `guardrails.py` — `page_setup()` renders the mandatory disclaimer
     banner ("potential competition signals … **not** recruitment
     forecasts, patient availability, site capacity, or trial outcomes")
     on every page, plus the snapshot-proxy note and a guarded footer.
   - `filters.py` — reusable condition/state/phase sidebar multiselects.
2. **Six pages + overview**:
   - `app.py` (Overview) — headline metrics, top-10 queue preview,
     "how to read this dashboard".
   - `1_Priority_Queue.py` — full ranked queue, band metrics, proxy
     warning when growth uses the registry-date fallback, horizontal
     stacked bar of normalized (unweighted) score components.
   - `2_Competition_Landscape.py` — density vs sponsor-HHI scatter
     (bubble = listed sites, color = signal band) + segment table.
   - `3_Geography_Trends.py` — USA choropleth per condition group, top
     states, monthly trend line (or an honest "needs more snapshots" note
     with the accrued count).
   - `4_Site_Overlap.py` — multi-trial facility table, states ranked by
     multi-trial facilities, explicit "not workload/performance" caption.
   - `5_Sponsor_Landscape.py` — top lead sponsors by recruiting listings,
     colored by sponsor class; "not market share" caption.
   - `6_Data_Reliability.py` — run reliability table, latest-run metrics,
     known limitations, and the assumption-driven scenario explorer
     (sliders adjust session-only copies of `roi_assumptions.yml`; the
     disclaimer always renders above results).
3. **Live verification:**
   - `streamlit run dashboard/app.py` served HTTP 200 headless.
   - Every page executed via Streamlit's `AppTest` harness — clean.
   - Debugging notes: a first combined AppTest run segfaulted while the
     dev server still held the DuckDB file — re-ran after stopping the
     server and all pages passed (kept pages tested without a live
     server as the standard procedure). Also learned this session's
     shell wrapper embeds command text in its own cmdline, so
     `pkill -f <pattern>` self-terminates; killed by exact port marker
     instead.
4. **Added `tests/test_dashboard_smoke.py`** — parametrized AppTest run of
   all 7 scripts (auto-skips when no warehouse, e.g. CI) plus a static
   check that every page calls `page_setup` (guardrail banner). Suite now
   **passing**, ruff clean.

**Key decisions:** the dashboard is read-only against the warehouse
(connection opened `read_only=True`); every page carries the disclaimer
via one shared function so no view can ship without it; scenario sliders
never write back to the assumptions file.

---

## Phase 7 — Documentation, executive memo & career materials

**Goal:** finish the documentation set, replace every README placeholder
with measured values, and produce the executive memo template plus
resume/interview materials — all grounded in the live 2026-07-24 build.

### Step 7.1 — Core documentation set (`docs/`)

Wrote eight documents, each using only numbers queried from the live
warehouse and guarded interpretation language:

- `data_dictionary.md` — every silver/gold object, column, grain.
- `source_documentation.md` — API v2 endpoint, parameters, pagination,
  snapshot-history rationale.
- `metric_definitions.md` — formal definition, denominator, and
  guardrail note for every metric (trial counts always
  `COUNT(DISTINCT nct_id)`).
- `clinical_interpretation_guardrails.md` — prohibited claims and the
  required "potential competition signal … not a recruitment forecast"
  framing.
- `assumptions_and_limitations.md` — registry lag, facility-name
  instability, no population adjustment, single-snapshot history.
- `data_quality_framework.md` — quarantine reason codes, manifest
  reconciliation, schema-drift baselines, dbt test inventory.
- `dashboard_spec.md` — page-by-page spec incl. the enforced
  `page_setup` guardrail banner.
- `executive_memo_template.md` — bracketed template with italic live
  example figures (2,592 trials; 419 recruiting; 449 segments; 75 in
  review band; 0 in priority band on single-snapshot history).

### Step 7.2 — README completion

- Section 17 now links `docs/metric_definitions.md`.
- Section 19 cites tested reality with links to the framework and guardrail docs (engineering-count inventories omitted during archival).
- Section 22's screenshot placeholders became a 10-row documentation
  index table.
- Section 24 resume bullets use measured values: 2,592 versioned trial
  records, tested dbt models, 419 recruiting trials across 50 states,
  6,241 normalized facilities, 449 ranked segments.
- Section 25 lists 8 numbered interview talking points.
- Removed all leftover `*(Phase N)*` markers from section headers now
  that every phase is complete.

### Step 7.3 — Final verification (all green)

| Check | Result |
|---|---|
| `uv run pytest -q` | passed |
| `uv run ruff check src tests dashboard` | clean |
| `dbt build` (root, `--project-dir`/`--profiles-dir`) | PASS — seeds, tables, views, and data tests green |

One accuracy fix during verification: corrected in README and `data_quality_framework.md` (engineering-count inventories omitted during archival).

**Key decisions:** documentation numbers are never typed from memory —
each figure was queried from the warehouse immediately before writing;
the memo is a *template* whose example values are explicitly labeled as
one build's output, not standing findings.

**Project complete.** All seven phases delivered and verified: ingestion
(bronze), normalization (silver), dbt marts + tests (gold), quality
framework, dashboard, and documentation.

---

## Phase 8 — Opt-in full-catalog ingestion profile (bronze) (2026-09-04)

**Goal:** add an opt-in ingestion profile that snapshots the *entire*
ClinicalTrials.gov registry (all conditions, worldwide, no status/type
filter, ~600k+ studies per live `countTotal`) into a completely separate
bronze tree, with zero effect on the default ADRD/US pipeline, dbt marts,
or dashboard. Deliberately bronze-only in this phase — a precursor to the
planned chunked/partitioned silver transform (see `docs/architecture.md`
§6 "Scaling path").

### Step 8.1 — Design: a config-only profile

Chose to express the new scope purely as configuration, reusing every
ingestion primitive (`CTGClient`, `iter_pages`, manifest/reuse/quarantine
logic) rather than forking an ingestion path:

- `config/full_catalog_config.yml` — new profile with intentionally
  empty `api.query_params: {}` (the CTG API's default scope is "all
  studies", so no `query.cond`/`filter.*` keys are sent),
  `page_size: 1000` (API maximum; keeps page count near ~600 for
  ~600k+ studies), `http.timeout_seconds: 120` (1000-study pages
  transfer slower than the default profile's 30s pages), and
  `reuse_window_hours: 720` (monthly effective cadence; a full-registry
  re-pull is far more expensive than the default profile's 24h window
  assumes).
- Parallel paths: `data/bronze_full_catalog/{api_responses,manifests}`,
  with reserved-but-unused `data/silver_full_catalog`, `data/gold_full_catalog`,
  and `data/warehouse/clinical_trials_full_catalog.duckdb` entries so
  `PathsConfig` validation still passes. All gitignored.
- Isolation guarantee: `run_ingestion` accepts an optional `config`
  override and uses it for *all* paths/API settings; the default
  transform/dbt/dashboard read only `data/bronze/manifests/` via
  `get_config()`, so a full-catalog run cannot leak into any downstream
  artifact.
- Lineage: `IngestionManifest` gained a `profile` field
  (`"default"` / `"full-catalog"`) so every run record states which
  scope produced it.

### Step 8.2 — Implementation

- `src/cli.py` — `ingest` gained `--profile {default,full-catalog}`;
  when `full-catalog`, `main()` loads
  `config/full_catalog_config.yml` via `load_config(path)` and passes it
  plus the profile name to `run_ingestion`. Default profile passes
  `config=None` (existing `get_config()` fallback unchanged).
- `src/ingest/extract_studies.py` — new `profile` parameter threaded
  into the manifest; `cfg = config or get_config()` already covered the
  config-override need.
- `src/ingest/extract_studies.py` (guard, follow-up in the same phase) —
  `run_ingestion` raises `ValueError` before any HTTP session or
  directory creation when `profile="full-catalog"` is combined with a
  condition filter; the CLI's existing exception handling turns this
  into a logged error and exit code 1. Condition handling mirrors
  `CTGClient.build_params` (`if condition:`), so an empty string is
  treated as "no condition".
- `src/ingest/snapshot_manifest.py` — `profile: str = "default"` field.
- `Makefile` — `ingest-full-catalog` and `full-catalog-full-refresh`
  targets; `setup` also creates the full-catalog bronze directories.
- `.gitignore` — new bronze/silver/gold full-catalog trees ignored;
  `.venv/` pattern changed to `.venv` because worktrees symlink `.venv`
  to a shared checkout and a symlink does not match a directory-only
  pattern.

### Step 8.3 — Tests

- `tests/test_cli.py` — parser defaults to `default`; `--profile
  full-catalog` parses; `main()` end-to-end (with `run_ingestion`
  monkeypatched) passes `profile="full-catalog"` plus a config whose
  `paths.bronze_manifests` ends in `bronze_full_catalog/manifests`;
  default path passes `config=None`.
- `tests/test_config.py` — full-catalog config has empty query params,
  `page_size == 1000`, and paths disjoint from the default config.
- `tests/test_ctg_client.py` — with empty `query_params`, `build_params`
  emits no `query.cond`/`filter.overallStatus`/`filter.advanced` keys.
- `tests/test_snapshot_manifest.py` — `profile` defaults and overrides.
- `tests/test_extract_studies.py` (new) — guard fires for full-catalog +
  condition; default profile + condition still proceeds past the guard
  (verified with a sentinel client, so tests never touch the network).
- `tests/test_cli.py` — combining `--condition` with `--profile
  full-catalog` exits 1 and never enters `CTGClient`.

### Step 8.4 — Documentation

- `README.md` — new §7a documenting the opt-in profile, its bronze-only
  status, and the isolation guarantee; setup section cross-links it.
- `docs/architecture.md` §6 — marks full-catalog bronze ingestion as
  *implemented (opt-in)* and names the blocking constraint for the next
  phase: `build_silver_entities.py` materializes an entire run in pandas
  before writing Parquet, so ~600k-study silver transforms need
  chunked/partitioned streaming first.
- `config/project_config.yml` — comment cross-referencing the profile.

### Verification (2026-09-04, worktree `scale-more-records`)

| Check | Command | Result |
|---|---|---|
| Unit tests | `uv run pytest -q -rs` | passed; dashboard smoke skipped |
| Lint | `make lint` (ruff on src/tests/dashboard) | clean |

The skips are `tests/test_dashboard_smoke.py` marts-not-built skips —
pre-existing behavior in a worktree without a built warehouse, unrelated
to this change.

### Live verification (2026-09-05 UTC, worktree `scale-more-records`)

First real full-catalog run, executed after a 2-page capped smoke run
(`--max-pages 2`, correctly marked `partial` and excluded from reuse):

- Run `20260905T071910Z_774560b2`: **status `success`**, 602 pages,
  **601,694 records** — exactly matching the API-reported
  `total_count_reported` (`countTotal`); the final page carried the
  remaining 694 studies; 0 quarantined; recorded params confirm only
  `format` / `pageSize=1000` / `countTotal` (no scope filters).
- Duration 5m56s (~0.6s per page); final `data/bronze_full_catalog/`
  footprint **9.7 GB**, matching the ~9.6 GB projection from measured
  page sizes (~16 MB/page).
- Incremental reuse behaved as designed: the earlier partial smoke run
  was not reused; the full run wrote a fresh `success` manifest.

### Decisions and rationale

- **Bronze-only scope.** Silver/gold/dbt cannot consume this volume yet;
  forcing it through the current pandas-materializing transform risks
  OOM and would silently expand every mart's grain. Deferring is
  documented, not hidden.
- **Config-only profile, no code fork.** The smallest change that gets
  full-registry snapshots: one YAML file, one CLI flag, one manifest
  field. All reuse/quarantine/summary machinery applies unchanged.

### Known limitations and open questions

- Resolved (same phase): combining `--condition` with `--profile
  full-catalog` is now rejected by `run_ingestion` with a `ValueError`
  before any HTTP activity or files are written; the CLI exits 1. See
  the guard bullet in Step 8.2 and the new tests in Step 8.3.
- Full-catalog data reaches nothing downstream yet by design; the
  profile is inert until the chunked transform phase.
- `config/full_catalog_config.yml` was untracked at implementation time
  and is included in the Phase 8 commit (`f422c0c`).

### Next step

Chunked/partitioned silver transform (stream `build_silver_entities.py`
and `export_parquet.py` batch-wise), then extend
`condition_taxonomy.yml` / `geography_rules.yml` beyond ADRD/US so the
full-catalog bronze data can reach the marts.

## Phase 9 — Chunked silver transform (memory-bounded streaming) (2026-09-05)

**Goal:** make the bronze→silver transform survive full-catalog volume.
`build_silver_for_run` accumulated six lists of row-dicts for an entire run
and `export_entity` materialized each entity into a pandas DataFrame before
writing Parquet — at 601,694 studies (the live Phase 8 run) this OOMs or
degrades severely. Profiling had the same problem one layer up
(`pd.read_parquet` of the whole entity). This phase replaces both with
bounded-memory streaming and wires `transform --profile full-catalog` so the
Phase 8 bronze tree can finally reach silver.

### Environment incident — worktree loss (recorded, not hidden)

The `scale-more-records` worktree was **deleted from disk by an external
process** after PR #5 merged (working tree was clean; nothing uncommitted
was lost). The gitignored 9.7 GB full-catalog bronze run was lost with it —
data, not code — so live verification in this phase re-ingests the catalog
first. `origin/main` also advanced during the gap (PRs #7 and #9 from
sibling sessions); this phase's branch `feat/chunked-silver-transform` is
cut from fresh `origin/main` (`6831886`), and all touched files were
re-verified against it before editing.

### Step 9.1 — Design: fixed Arrow schemas + row-group streaming

Two coupled problems had to be solved together:

1. **Chunked writes are unsafe with inferred schemas.** pandas/Arrow
   inference decides a column's type per chunk: an all-`None` chunk (e.g.
   `last_known_status`, which is entirely null in real data) infers Arrow
   `null`, and an int-only `enrollment_count` chunk infers `int64` — both
   collide with the next flush. Fix: `ENTITY_ARROW_SCHEMAS`, one fixed
   `pa.Schema` per entity, columns/order identical to `ENTITY_COLUMNS`.
   Types mirror what inference produced on real silver files (verified in
   Phase 8's live silver data): `enrollment_count`/`latitude`/`longitude`
   → `float64` (int-or-null widens cleanly), `outcome_index` → `int64`,
   six boolean flags → `bool`, everything else → `string`. Non-castable
   values raise `ArrowInvalid` — fail fast, no silent coercion.
2. **The silver contract is one file per entity per run**
   (`run_id=<id>.parquet`), consumed by dbt's
   `read_parquet('data/silver/{name}/*.parquet')`, `_already_transformed`,
   and reconciliation. Fix: stream *row groups inside one file* rather
   than sharding files. `SilverRunWriter` buffers rows per entity and
   flushes a row group every `DEFAULT_FLUSH_ROWS = 50_000` rows
   (per-entity threshold — locations/outcomes produce many rows per
   study), writing snappy-compressed Parquet to
   `run_id=<id>.parquet.tmp` and `os.replace`-ing it into place on
   `close()`. A crashed run therefore never leaves a half-written file
   for dbt globs or `_already_transformed` to pick up; `discard()`
   removes staged files. Entities that receive no rows still get a
   schema-only file via `schema.empty_table()` (preserves the empty-run
   contract existing tests pin). `export_entity` keeps its exact
   signature and delegates to a one-shot `SilverRunWriter`.

`build_silver_for_run` keeps its structure and every semantic:
run-global `seen_nct_ids` dedup (first occurrence wins), missing-NCT skip
counting, dedup/skip warnings, per-entity "wrote N rows" logs, the
`rows == record_count − duplicates − skips` reconciliation warning, plus a
new progress line every 100k studies. On exception the writer is
discarded and the error propagates. `FLUSH_ROWS` is a module-level alias
of `DEFAULT_FLUSH_ROWS` so tests can shrink it.

Profiling (`profile_entity`) became a single DuckDB streaming aggregate
(`count(*)`, per-column `count(col)` null rates, `count(distinct nct_id)`)
over the Parquet path — identical JSON output shape (`row_count`,
`column_count`, `null_rates` rounded to 4 with `{}` for empty files,
`distinct_nct_ids` only when the column exists), zero full materialization.

CLI: the `transform` subcommand gains `--profile {default,full-catalog}`
mirroring ingest, loading `config/full_catalog_config.yml` and passing the
config to **both** `run_transform` and `profile_run`. The latter also fixes
a latent bug present on `main`: the transform handler called
`profile_run(run_id)` without config, which would have profiled the wrong
(default-profile) tree under full-catalog mode. Added
`make transform-full-catalog`.

### Step 9.2 — Files changed

- `src/transform/export_parquet.py` — `ENTITY_ARROW_SCHEMAS`,
  `DEFAULT_FLUSH_ROWS`, `_table_from_rows`, `SilverRunWriter`;
  `export_entity` → one-shot delegate (pandas dependency dropped).
- `src/transform/build_silver_entities.py` — streaming accumulation via
  `SilverRunWriter`; `FLUSH_ROWS`/`PROGRESS_EVERY` module knobs.
- `src/quality/profiling.py` — DuckDB streaming `profile_entity`.
- `src/cli.py` — `transform --profile` + config pass-through (fixes the
  latent wrong-tree profiling bug).
- `Makefile` — `transform-full-catalog` target.
- Tests: `tests/test_export_parquet.py`, new
  `tests/test_build_silver.py`, `tests/test_quality.py`,
  `tests/test_cli.py`.
- Docs: `docs/architecture.md` §6 (chunked transform → implemented),
  `README.md` §7a, `config/full_catalog_config.yml` scope comments.

### Step 9.3 — Tests

New coverage pins the contracts the streaming rewrite could silently
break: schema names/order equal `ENTITY_COLUMNS`; 3 batches at
`flush_rows=50` produce exactly 3 row groups / 150 rows; type stability
across mixed chunks (int chunk then all-`None` chunk for the same columns,
the exact pandas-inference killer); extra keys dropped with column order
pinned; zero-row entities readable by DuckDB with exact column names;
`discard()` leaves no final or `.tmp` files; dedup across a flush boundary
(kept-first occurrence already flushed to disk, duplicate arrives two
pages later, reconciliation invariant holds); records without NCT ID are
skipped and counted; profiling on all-null / empty / no-`nct_id` files;
and CLI `--profile` pass-through asserts the full-catalog config reaches
both `run_transform` and `profile_run` (default profile passes `None` to
both).

### Verification (2026-09-05, worktree `feat-chunked-silver-transform`)

| Check | Command | Result |
|---|---|---|
| Unit tests | `uv run pytest` | passed; dashboard smoke skipped |
| Lint | `uv run ruff check src tests dashboard` | clean |

The skips are the pre-existing `test_dashboard_smoke.py` marts-not-built
skips, unrelated to this change.

### Incident during live verification: two flatten columns missing from the schema

The default-pipeline regression caught a real bug in the Phase 9 design
assumption. Planning had recorded "flatten_study emits exactly the
ENTITY_COLUMNS key sets" — **incomplete**: `flatten_study` emits two extra
trial columns, `allocation` and `primary_purpose`
(`src/transform/flatten_studies.py:108-109`). The old pandas export
silently wrote every emitted key, so the on-disk silver contract always
included them and dbt staging (`stg_trials`) selects them. The fixed-schema
writer correctly dropped "unknown" keys — deleting two live columns.

Symptom: `make dbt-run` failed with
`Binder Error: Referenced column "allocation" not found in FROM clause!`
(1 model error, 20 skips; downstream dbt tests errored).

Fix (`6f185d7`): add both columns to `ENTITY_COLUMNS["silver_trials"]`
(string type via the arrow-schema default) and pin the contract with
`test_flatten_emits_exactly_entity_columns`, which flattens a
fully-populated synthetic study and asserts every emitted row's keys equal
`ENTITY_COLUMNS[entity]` — a flatten change without a schema update now
fails loudly instead of silently losing columns.

Process note: the failed pytest run that preceded this fix was masked by a
pipe (`pytest | tail`), whose exit status hid the failures and let a
commit proceed — the failures were the partially-built warehouse in this
worktree, not the fix, but the command pattern was wrong regardless. Later
verification runs capture pytest's exit status directly.

### Live verification (2026-09-05 UTC, worktree `feat-chunked-silver-transform`)

Bronze was re-ingested (the 9.7 GB full-catalog tree was lost with the
deleted worktree; see the environment incident above):

- Run `20260905T184736Z_a62964b0`: **status `success`**, 602 pages,
  **601,694 records** — exactly `total_count_reported` (`countTotal`), 0
  quarantined, `profile: full-catalog`. Duration ~6m22s (18:47:36 →
  18:53:58 UTC), matching the Phase 8 run's shape precisely.

Default ADRD/US pipeline regression (default-profile data copied into the
fresh worktree):

- Streaming rebuild of run `20260905T012941Z_fe9f104f` reproduced the
  pandas-written silver **exactly**: 2,618 trials / 5,368 conditions /
  4,958 interventions / 4,674 sponsors / 25,935 locations / 20,255
  outcomes — identical counts before and after the writer swap.
- `make dbt-run`: **models PASS**; `make dbt-test`: **tests
  PASS** against streaming-writer silver.
- `uv run pytest`: **passed, none failed or skipped** (dashboard smoke
  tests now run for real against the rebuilt warehouse).

Full-catalog streaming transform (run `20260905T184736Z_a62964b0`,
601,694 studies, executed via `/usr/bin/time -l`):

- **Peak RSS 1,058,586,624 bytes ≈ 1.06 GB** (target < 1.5 GB; the old
  collect-then-materialize approach would have needed tens of GB for
  this run). Wall time **~7.5 minutes** (~85k studies/min); progress
  lines at every 100k studies behaved as designed.
- Rows written: silver_trials **601,694**; conditions 1,081,165;
  interventions 1,017,756; sponsors 959,315; locations 3,519,911;
  outcomes 3,755,941. No dedup/skip/reconciliation warnings fired.
- **Reconciliation: `bronze_manifest_vs_silver_rows` 601,694 == 601,694
  PASS; `silver_nct_ids_unique` 601,694 == 601,694 PASS** — rows equal
  distinct NCT IDs equal the manifest count, zero unexpected loss. The
  `warehouse_exists` check reports `missing` as expected: the
  full-catalog dbt warehouse is explicitly out of scope this phase
  (gold/dbt remain default-profile only).
- Profile JSON (`data/silver_full_catalog/_profiles/profile_…json`)
  produced by the DuckDB streaming profiler across the 3.76M-row
  outcomes file without materialization: 32 columns on silver_trials
  (30 original + `allocation` + `primary_purpose`), distinct NCT counts
  per child entity (conditions 600,670; interventions 540,752;
  sponsors 601,694; locations 541,210; outcomes 584,078).
- Row-group streaming confirmed on disk: silver_trials 13 row groups,
  locations 71, outcomes 76 (~50k rows each), one file per entity under
  the strict `run_id=<id>.parquet` contract. Total silver_full_catalog
  footprint **1.5 GB** (snappy) against 9.7 GB of bronze JSON.
