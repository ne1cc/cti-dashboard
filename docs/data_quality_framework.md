# Data Quality Framework

Five layers of automated checks; nothing is silently dropped or fixed.

## 1. Ingestion integrity (Python, per run)
- Run lifecycle: `running → success | partial | failed` in the manifest;
  manifests are written even when a run fails (finally-block).
- Reconciliation against the API's `totalCount` (`countTotal=true`).
- **Quarantine, not drop**: invalid records get reason codes
  (`NOT_AN_OBJECT`, `MISSING_NCT_ID`, `INVALID_NCT_ID_FORMAT`) and are
  counted in the manifest.
- Partial runs (page-capped) are excluded from incremental reuse, from
  silver by default, and from every metric.

## 2. Transform validation (Python, per run)
- NCT regex validation; within-run duplicate NCT IDs deduped kept-first
  and logged with counts.
- `record_quality_flag` per trial: `missing_overall_status`,
  `missing_study_type`, `start_after_completion`,
  `results_before_first_post`, `negative_enrollment`, else `ok`.
- Silver row counts reconciled against the manifest; profile JSON written
  per run (`data/silver/_profiles/`).

## 3. Warehouse tests (dbt)
The suite is 138 dbt tests (measured 2026-09-07 UTC with `uv run dbt parse` plus
a `resource_type` counter over `dbt_clinical_trials/target/manifest.json` — the same
manifest `tests/test_docs_describe_current_paths.py` reads; that guard fails the build
when a live document states a count with no date and no command behind it, which is why
this line carries both). Run them with `make dbt-test`; the last dated full green run
is recorded in [`docs/DEPLOY_FLY.md`](docs/DEPLOY_FLY.md).
- Schema tests: `not_null`, `unique`, `accepted_values`, `relationships`
  on every key and grain, staging through marts.
- Singular tests (`dbt_clinical_trials/tests/` holds the full set; the six below
  are the ones whose severity is load-bearing):
  | Test | Severity | Asserts |
  |---|---|---|
  | assert_valid_study_dates | warn | start ≤ completion where both exist |
  | assert_one_current_record_per_trial | error | ≤1 `current_record_flag` per NCT |
  | assert_trial_site_relationship_integrity | error | every site row joins dim_trial |
  | assert_valid_us_state | error | 2-letter state codes at the mart boundary |
  | assert_snapshot_completeness | warn | success runs reconcile with unique NCTs |
  | assert_feasibility_score_within_bounds | error | score ∈ [0, 1] |
- Privacy guardrail: `stg_trial_contacts` is structurally empty.

## 4. Cross-layer reconciliation (`src/quality/reconciliation.py`)
Per success run: bronze manifest vs silver rows; NCT uniqueness. Warehouse:
`dim_trial` vs latest silver distinct NCTs; current-record cap. Results render in
the regenerated report below — this file does not restate its pass count.

## 5. Schema drift (`src/quality/schema_drift.py`)
Observed bronze field paths (depth 3) vs a stored baseline
(`data/bronze/<profile_id>/_schema_baseline.json`, one baseline per profile —
observed-path counts drift with each registry release, so the number is not
repeated here). Drift produces a report
with added/removed paths; the baseline changes only via explicit
`--update-schema-baseline`. Drift is surfaced, never auto-accepted.

## Reporting
`make quality-report` → `reports/data_quality_report.md`: run reliability
table (from `mart_data_reliability`), reconciliation results, drift
status, and the interpretation guardrails. Unit/integration suite: 289 pytest
tests (measured 2026-09-07 UTC by `uv run pytest --collect-only`; regenerate
with `make test`), including config-sync tests that fail if score weights or band
thresholds diverge between YAML, the dbt seed, and dbt vars.

## Severity philosophy
- **error** = structural contract broken → fix before shipping metrics.
- **warn** = real-world registry messiness → investigate, document, and
  disclose in `mart_data_reliability` rather than block the pipeline.
