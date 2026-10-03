# Competition metric auditability walkthrough

Verification date: 2026-10-03 UTC. Approved scope: Tasks 1–5 in
`.scratch/competition-auditability/implementation_plan.md`, with recorded scope
rulings in `.superpowers/sdd/implementation_plan/progress.md`.

## Result and source chain

Tasks 1–4 preserve page receipt metadata separately from immutable API bytes,
carry page/study/location ordinal lineage into typed silver/staging, build two
contracted successful-snapshot audit marts, compute filter-aware decisions and
coverage, and integrate Data coverage and caveats on all seven affected pages.
Task 5 documents those implemented behaviors; it changes no production code.

Study identity is profile × immutable ingestion run × NCT ID. Original-location
identity adds location ordinal, preserving duplicate recorded rows. Daily metric
history uses the actual latest per-study/date selections, including multiple runs
within one date. The panel/export retain filters, rule version and active SHA-256
identifiers, actual selected run IDs/dates, count contributors and raw lineage.

Coverage retains eligible captured studies before geographic/status exclusions;
undetermined state membership is distinct from known outside. Confirmed recruiting
requires overall RECRUITING. Older posted updates strictly over 180 days warn and
remain. Three separate clocks preserve verification month precision. Enrollment
categories/totals stay at study grain, without location allocation or history fanout.
The panel enumerates count inputs; derived scores/growth/HHI/percentiles remain
warehouse calculations with exact formulas, scope, windows and displayed values disclosed.

## Task 5 changed files

- `README.md`: panel/export overview, original-location grain, legacy operational
  completeness label, measured suite counts, forced legacy silver rebuild guidance,
  retention/history limits, exact registry disclaimer.
- `docs/metric_definitions.md`: source chain, observations/filters, coverage and
  exclusion precedence, clocks/threshold, enrollment, exact listed-site identity,
  derived formulas/window boundaries, rule/export contract and disclaimer.
- `docs/data_dictionary.md`: page receipt sidecar, nullable provenance, original
  location ordinals, contracted study/location audit grains and field groups.
- `docs/data_quality_framework.md`: measured counts, current singular-test file
  count, isolated fixture/check coverage, structural-vs-clinical boundary, UTC fixture limitation.
- `docs/assumptions_and_limitations.md`: state/facility/retention/history limits,
  multi-profile scope, fixed-vs-percentile bands, unknown receipts and disclaimer.
- `docs/competitive_positioning.md`: ledger-authorized live count/measurement
  correction only; 34 dbt models, 157 dbt tests, 371 collected pytest cases.
- `.scratch/competition-auditability/walkthrough.md`: this immediate audit record.
- `.superpowers/sdd/implementation_plan/task-5-report.md`: Task 5 execution report.

## Verification evidence

Before editing, `uv run pytest tests/test_docs_describe_current_paths.py` returned
**1 failed, 5 passed in 4.96s**. On 2026-10-03 UTC before the final-review fixes, the sole failure expected the current
`157 dbt tests and a 371-test pytest suite` positioning statement; the live fixture
reported 34 models, 157 dbt tests and 371 pytest cases. After updating docs the same
command returned **6 passed in 4.61s**.

Independent live commands on 2026-10-03 UTC:

- `uv run dbt parse --project-dir dbt_clinical_trials --profiles-dir dbt_clinical_trials`:
  exit 0; dbt 1.11.14 / duckdb adapter 1.10.1. A `resource_type` counter over its
  manifest returned model=34, test=157, seed=4, analysis=4.
- `uv run pytest --collect-only`: **371 tests collected in 1.21s**.
- `uv run pytest tests/test_extract_studies.py tests/test_build_silver.py tests/test_normalization.py tests/test_export_parquet.py`:
  **43 passed in 1.28s**.
- `uv run pytest tests/test_audit_marts.py tests/test_dbt_fixture_build.py tests/test_audit.py`:
  **60 passed in 48.59s**; real dbt builds use isolated fixture warehouses.
- `uv run pytest tests/test_dashboard_audit.py tests/test_dashboard_smoke.py tests/test_dashboard_profile_scope.py tests/test_dashboard_resilience.py`:
  **62 passed, 9 skipped in 39.50s**.
- `uv run ruff check src tests dashboard`: **All checks passed!**
- `uv run ruff format --check src tests dashboard`: **88 files already formatted**.
- `uv run mypy`: **Success: no issues found in 26 source files**.

- `uv run --group orchestration pytest`: **362 passed, 9 skipped in 314.12s
  (0:05:14)**, exit 0. This is the first full-suite run after all Task 4 review
  fixes; the prior documentation count failure is resolved. Nine live-warehouse
  smoke cases skip because marts are not built in the local production warehouse;
  isolated fixture AppTests run and pass.
- Final `uv run pytest tests/test_docs_describe_current_paths.py` after documentation
  self-review: **6 passed in 4.84s**.
- `git diff --check`: no output, exit 0. Staged scope is limited to the eight
  documentation/audit-record files listed above.

## Limits and unresolved review item

Historical retrieval times remain null without receipt sidecars; forced silver
rebuild exposes fields but cannot recreate missing historical receipts. Active
rule hashes are not historical configuration evidence. Raw references resolve only
while profile bronze pages are retained; no indefinite archive is promised. Facility
text matching is best effort and original-location ordinals are not resolved site IDs.
Coverage describes captured-record inclusion, not market completeness or scientific
validity. Estimated enrollment remains a target, not site recruitment performance.

Task 2's low, nonblocking review item remains unresolved: its UTC-boundary fixture
does not pin DuckDB to a non-UTC session timezone. It asserts staging/date agreement,
but a UTC runner could miss regression to session-dependent casts. Production explicitly
normalizes with `timezone('UTC', ...)`. No test hardening was performed in this docs-only task.

No live registry requests, production warehouse refresh, PR, external messages or
browser inspection were performed. Existing Dagster retry-path tests explain much
of the full-suite runtime. Dashboard verification uses AppTest and real fixture SQL.

Registry-derived signals support preliminary feasibility review. They do not measure site-level recruitment performance or establish scientific validity. Counts reflect captured public records and the displayed inclusion rules.

After force-adding both audit artifacts, the docs guard was rerun against all eight
tracked files: `uv run pytest tests/test_docs_describe_current_paths.py` returned
**6 passed in 4.65s**. Final staged whitespace check passed.

## Final-review correction (2026-10-03 UTC)

The audit now retains the denominator for explicitly empty facility restrictions,
exports true recruiting-entry/proxy events with predecessor/source evidence, and
provides a nonnegative posted-update warning control (default 180 days). The prior
UTC fixture review item is resolved by pinned America/Los_Angeles dbt sessions and
explicit UTC alignment assertions. `uv run pytest --collect-only` now collects
383 tests; model/test counts remain 34/157. A fresh full `uv run --group orchestration pytest`
run returned **374 passed, 9 skipped in 345.19s**, exit 0, including the documentation-count
guard. Earlier Task 5 measurements above are historical.
