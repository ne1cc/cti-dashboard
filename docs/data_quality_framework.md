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
The suite is 157 dbt tests (measured 2026-10-03 UTC with `uv run dbt parse
--project-dir dbt_clinical_trials --profiles-dir dbt_clinical_trials` plus
a `resource_type` counter over `dbt_clinical_trials/target/manifest.json` — the same
manifest `tests/test_docs_describe_current_paths.py` reads; that guard fails the build
when a live document states a dbt or pytest count, phrased the way the guard
recognises (the recognised shapes are listed in that file), with no date and no
command behind it — which is why this line carries both). Run them with
`make dbt-test`; the last dated full green run is recorded in
[`docs/DEPLOY_FLY.md`](docs/DEPLOY_FLY.md).
- Schema tests: `not_null`, `unique`, `accepted_values`, `relationships`
  on every key and grain, staging through marts.
- Singular tests (`dbt_clinical_trials/tests/` holds the full set of 14 SQL
  assertion files; the six below are the ones this document names, and their
  severity is the point — four fail the build at dbt's default `error` and two are
  deliberately `warn`, set in the test file itself):
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
status, and the interpretation guardrails. The step is a gate as well as a
print: it exits non-zero when a reconciliation check fails, which is what
`make pipeline` ends on. Unit/integration suite: 371 pytest
tests (measured 2026-10-03 UTC by `uv run pytest --collect-only`; regenerate
with `make test`), including config-sync tests that fail if score weights or band
thresholds diverge between YAML, the dbt seed, and dbt vars.

## Severity philosophy
- **error** = structural contract broken → fix before shipping metrics.
- **warn** = real-world registry messiness → investigate, document, and
  disclose in `mart_data_reliability` rather than block the pipeline.

## Competition audit checks and caveats

Isolated fixture builds execute the real dbt build without refreshing the registry
or production warehouse. Enforced audit contracts check study/location key uniqueness,
non-null identities, and parent relationships. `assert_audit_exclusion_reconciliation`
checks base inclusion plus exclusive exclusions; `assert_audit_enrollment_join_invariance`
checks that usable-location joins do not multiply study-level enrollment totals.
Python checks cover filter-specific denominators, overlapping flags, exact contributor
IDs, snapshot/profile scope, null denominators, legacy receipt nulls, and JSON exports.
Dashboard AppTest checks actual filter reruns and downloaded export bytes against
independent fixture SQL contributors. Structural checks do not validate registry
claims clinically or measure market completeness.

The audit panel shows source omissions before geography exclusion, separately from
known outside-state records and undetermined membership. Older posted updates warn
strictly after 180 days and remain included; missing/unparseable dates are explicit.
The legacy operational completeness adjustment is disclosed with its formula, not
presented as a scientific-confidence percentage. Missing source fields are not
assumed to be extraction failures: immutable pages, ordinals, hashes and extraction
fixtures support the mapping review while retained bronze is available.

One low, nonblocking Task 2 review item remains: the UTC-boundary fixture checks
staging/date agreement but does not pin DuckDB to a non-UTC session timezone. A UTC
runner could therefore fail to detect regression to the old session-dependent cast.
The implementation explicitly uses `timezone('UTC', ...)`; additional timezone
fixture hardening is outside this documentation task.
