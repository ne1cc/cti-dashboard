# Task 5 — Documentation and complete verification

Status: DONE_WITH_CONCERNS (the carried low/nonblocking timezone-fixture item only).
Verification date: 2026-10-03 UTC.

## Scope and outcome

Read the approved implementation plan, all four task reports including their fix
rounds, the Task 5 brief, and the progress ledger before documentation edits.
Implemented only six authorized public documentation updates plus the requested
walkthrough and this report. No production source, test, dependency, configuration,
warehouse data, or prior execution artifact was changed.

The docs now trace immutable raw page/receipt metadata through typed silver,
staging, successful study/location audit marts, actual metric observations,
filter-specific decisions and contributor IDs to JSON export. They specify profile/run/NCT
study grain and original ordinal location grain; deduplication across regions and
observations; exact status/state/facility rules; known outside versus undetermined;
pre-geography denominators and reconciling exclusive exclusions; overlapping flags;
three independent clocks and date precision; estimated/actual/missing/unknown-type
enrollment without site allocation; active rule version/hashes; threshold/evaluation
metadata; and legacy receipt/rebuild/bronze retention limits.

Exact listed-site identity includes per-study/state normalized facility/city distinct
pairs, duplicate collapse, missing-name exclusion, and null-city empty-text handling.
Site Overlap instead counts distinct study IDs by normalized facility/city/state.
Sponsor HHI/top share, profile-relative count percentile cuts, min-max/no-spread
normalization, active weights, fixed priority bands, transition/first-post proxy rules,
site-overlap denominator and inclusive monthly/90-day windows are documented.
The first-post proxy has a lower-bound date check without an upper bound. Derived
metrics remain warehouse calculations; the panel enumerates count inputs and exports
definitions, windows and displayed values, not a replacement derived computation.
The UI's 180-day threshold is explicitly distinguished from the configurable pure
computation parameter; no nonexistent panel threshold control is promised.

Public documentation uses the exact requested registry disclaimer and replaces
misleading density/confidence wording in the affected descriptions. The ledger-authorized
competitive-positioning edit only updates the live counts and measurement description.
Actual profile scope is ADRD and NSCLC, with full catalog ingestion-only.

## Changed files

1. `README.md` — audit entry point/export, grain, counts, rebuild command, limits.
2. `docs/metric_definitions.md` — complete rules/formulas/coverage/export contract.
3. `docs/data_dictionary.md` — provenance fields, sidecar and contracted mart grains.
4. `docs/data_quality_framework.md` — checks, current counts, UTC fixture limit.
5. `docs/assumptions_and_limitations.md` — interpretation, identity, history, retention.
6. `docs/competitive_positioning.md` — verified live count/measurement correction.
7. `.scratch/competition-auditability/walkthrough.md` — detailed cross-task audit record.
8. `.superpowers/sdd/implementation_plan/task-5-report.md` — this report.

The last two artifacts are force-added because the repository ignores their directories;
the dispatch explicitly requires them in the Task 5 commit.

## RED/GREEN and independent count evidence

Before editing: `uv run pytest tests/test_docs_describe_current_paths.py` returned
1 failed, 5 passed in 4.96s. Sole failure was the stale competitive-positioning count;
its live fixture reported 34 dbt models, 157 dbt tests and 371 pytest cases.
After update: the same command returned 6 passed in 4.61s; after final self-review
it returned 6 passed in 4.84s.

Independently reran `uv run dbt parse --project-dir dbt_clinical_trials --profiles-dir
 dbt_clinical_trials` (exit 0), then counted `resource_type` from its target manifest:
model=34, test=157, seed=4, analysis=4. `uv run pytest --collect-only` returned
371 tests collected in 1.21s. Counts are dated 2026-10-03 UTC and include their
regeneration commands. Singular assertion file count was inspected directly: 14 files.

## Verification

| Command | Actual result |
|---|---|
| `uv run pytest tests/test_extract_studies.py tests/test_build_silver.py tests/test_normalization.py tests/test_export_parquet.py` | 43 passed in 1.28s |
| `uv run pytest tests/test_audit_marts.py tests/test_dbt_fixture_build.py tests/test_audit.py` | 60 passed in 48.59s |
| `uv run pytest tests/test_dashboard_audit.py tests/test_dashboard_smoke.py tests/test_dashboard_profile_scope.py tests/test_dashboard_resilience.py` | 62 passed, 9 skipped in 39.50s |
| `uv run --group orchestration pytest` | 362 passed, 9 skipped in 314.12s (0:05:14); exit 0 |
| `uv run ruff check src tests dashboard` | All checks passed! |
| `uv run ruff format --check src tests dashboard` | 88 files already formatted |
| `uv run mypy` | Success: no issues found in 26 source files |
| `git diff --check` | no output; exit 0 |

The full run is after all production/review fixes from Tasks 1–4. It includes real
isolated fixture dbt builds and orchestration tests using the previously installed
optional orchestration group. No live registry refresh or production warehouse
replacement was used. Nine existing smoke cases require local production marts
and skip when absent; independent fixture-backed dashboard tests passed. Existing
Dagster retry delays account for much of the full-suite duration.

## Self-review and remaining limitations

Checked each edited statement against audit computation, UI panel, marts, actual
profile config, CLI force-rebuild behavior, task reports and ledger. Checked the
six-public-doc diff and ensured no code/test edits. Reviewed exact disclaimer,
threshold boundary, null denominators, month/year precision, selected snapshot
identity with empty contributors, actual history windows, exclusive-vs-overlapping
counts, and enrollment deduplication. No scientific trust score or reliability
percentage is introduced or promised.

The sole unresolved review item is Task 2's low/nonblocking UTC test hardening:
`tests/test_audit_marts.py`'s UTC-boundary case does not set DuckDB to a non-UTC
session timezone. Explicit production `timezone('UTC', ...)` is implemented, but a
UTC runner could miss a regression to prior session-dependent casts. This is
recorded in both public limitations/quality docs and walkthrough; no tests were
changed outside this documentation scope.

Other intended limits: unknown historical receipts stay unknown; active configuration
hashes do not establish historical rule versions; raw page references may stop
resolving after bronze pruning; normalized facility text does not resolve real sites;
coverage is captured-record inclusion, not market/scientific completeness; derived
metrics are not recomputed from the union contributor list. No browser inspection,
PR, external messages or retrospective publication was performed. A subsequent PR
request should carry this walkthrough into its description/retrospective and T3 registration.

After force-adding both audit artifacts, the docs guard was rerun against all eight
tracked files: `uv run pytest tests/test_docs_describe_current_paths.py` returned
**6 passed in 4.65s**. Final staged whitespace check passed.
