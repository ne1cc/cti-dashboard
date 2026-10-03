# Final review fix wave

Verification date: 2026-10-03 UTC.
Base: `1523e6f99e2d917753c0709f8fa5b6b1cb3925e0`.
Status: all seven final-review findings implemented and verified in one fix wave.

## Review scope and implementation

Read the approved ledger and Task 2–5 reports. No registry requests, live warehouse refresh, or unrelated score changes were made. A fresh post-fix full-suite run is recorded below.

1. **Empty facility restriction:** `dashboard/components/audit.py` distinguishes unrestricted `facilities=[]` from `facility_restriction_applied=True` with an empty identity set. `audit_panel.py` passes the explicit restriction instead of deleting study rows. The pre-facility eligible denominator, study decisions, independent flags, snapshots, recorded locations, and export survive; zero contributors produce 0 coverage when the denominator is nonzero. Exclusions retain status/geography precedence and identify `empty_facility_selection` for otherwise eligible studies. Existing tests expecting erased/null coverage now require retained denominator, decisions and exclusions.

2. **Metric-specific growth/proxy contributors:** Added `recent_recruiting` and `first_post_proxy` audit modes without recomputing priority scores, HHI or unrelated measures. The observation loader calculates predecessor snapshot ID/date over all daily history before caller window filtering, carrying the source `previous_status`. The display loader also loads predecessor audit snapshots, even before the window, for raw page/ordinal/hash/status evidence. Entry events require current RECRUITING, a non-null previous non-RECRUITING status, and current event date inside the inclusive displayed window; continuing recruiters do not contribute. First-post proxy requires current RECRUITING and parsed source first-post date >= current metric snapshot minus 90 days. Its lower bound is inclusive and there is intentionally no upper bound, matching the source SQL. Both modes retain wider eligible and count-input contributor denominators separately from event contributors. Export includes event date/current snapshot, predecessor status/snapshot/date and raw lineage where available, first-post source evidence, exact filters, event boundary policy, event count and distinct contributor IDs. The UI displays event-specific evidence tables. `competition-audit-v2` identifies the changed semantics.

   `mart_study_snapshot_audit.sql` now carries existing staging `study_first_post_date_raw` and parsed `study_first_post_date`; `_marts.yml` contracts these fields as VARCHAR/DATE. No ingestion/silver extraction change is needed; rebuild dbt marts to expose the fields in an existing warehouse.

3. **Configurable project-defined warning:** Every Data coverage panel has a nonnegative integer day input, default 180. The selected threshold reaches `compute_audit`; export policy names its exact value. Posting age must be strictly greater than the threshold to warn. Studies remain eligible/included under the same metric rules. AppTest changes the threshold to zero and proves flags/policy change while contributors remain identical.

4. **Sponsor filter documentation:** `docs/metric_definitions.md` now states exact matching of displayed `lead_sponsor_name`, distinguishing normalized sponsor grouping for HHI.

5. **Raw reference resolution root:** `docs/data_dictionary.md`, staging `_sources.yml`, and the Task 5 report specify the configured bronze API response root, normally `data/bronze/<profile>/api_responses`; the reference path itself is `run_id=<id>/page=00001.json`.

6. **Deterministic UTC fixture:** The isolated audit/event dbt profile pins `TimeZone: America/Los_Angeles`. The audit mart test proves 00:15Z has a previous local calendar date while audit and staging retain aligned UTC timestamps/dates. Other fixture profiles remain unchanged unless explicitly requesting this timezone.

7. **Undetermined region exclusion:** Usable TX plus unresolved geography under CA now uses `no_confirmed_selected_region_match`, preserving `region_membership=undetermined` and an independent `undetermined_region_membership` flag. Known nonmatching geography continues to use `outside_selected_region`. The contributor rule remains unchanged.

Related current documentation was updated surgically: README rule version/warning/event semantics, quality/limitations docs' formerly unresolved timezone note, and the walkthrough correction. The docs guard required dating the walkthrough's historical count sentence; historical measurement numbers were preserved. Competitive positioning now records the actual live collection count. These changes correct statements made stale by the requested fixes, not a new design scope.

## Paths changed

- `dashboard/components/audit.py`: explicit empty restriction, event decisions/evidence, independent count-input denominators, version and policy metadata, clearer undetermined exclusion.
- `dashboard/components/audit_panel.py`: preserve denominator; load predecessor evidence; event/proxy selector and evidence display; threshold control.
- `dashboard/components/data.py`: enriched daily-history observation selection and event/proxy reader modes, with bound profile SQL.
- `dbt_clinical_trials/models/marts/mart_study_snapshot_audit.sql`, `_marts.yml`: raw/parsed first-post fields and contracts.
- `dbt_clinical_trials/models/staging/_sources.yml`: reference-base description.
- `tests/conftest.py`: optional isolated-session timezone and two-snapshot event fixture.
- `tests/test_audit.py`: empty restriction, undetermined reason, event evidence/filtering, boundary/future/missing proxy cases, independent real SQL contributor oracles.
- `tests/test_audit_marts.py`: first-post source clock and deterministic UTC boundary.
- `tests/test_dashboard_audit.py`: retained empty-facility denominator; threshold warning/export stability; transition/proxy AppTest filters and event exports.
- `docs/metric_definitions.md`, `docs/data_dictionary.md`, `docs/competitive_positioning.md`, `docs/data_quality_framework.md`, `docs/assumptions_and_limitations.md`, `README.md`: exact updated semantics and verified counts.
- `.scratch/competition-auditability/walkthrough.md`, `.superpowers/sdd/implementation_plan/task-5-report.md`: dated historical-count/reference clarification and final correction.
- `.superpowers/sdd/final-review-fix-report.md`: this audit trail.

## TDD RED/GREEN evidence

Initial RED command:

`uv run pytest tests/test_audit.py tests/test_dashboard_audit.py tests/test_audit_marts.py`

**11 failed, 40 passed in 22.14s**. Expected failures covered the undetermined reason, lost empty-facility denominator, absent event APIs/first-post field, missing threshold widget, and old union-contributor growth mode.

First GREEN after the core implementation: same command → **51 passed in 22.05s**. Additional actual event-fixture, non-UTC-session, and UI event-filter verification then expanded coverage. A temporary literal-wrapping edit during lint cleanup was corrected before final verification; no such edit was committed.

Docs RED after collection: `uv run pytest tests/test_docs_describe_current_paths.py` → **2 failed, 4 passed in 5.84s**. Failures were stale live positioning count and an undated historical walkthrough count sentence. Updated the live count and dated the historical sentence without rewriting its old measurement.

Final covering command:

`uv run pytest tests/test_audit.py tests/test_dashboard_audit.py tests/test_audit_marts.py tests/test_dbt_fixture_build.py tests/test_dashboard_smoke.py tests/test_dashboard_profile_scope.py tests/test_dashboard_resilience.py`

**134 passed, 9 skipped in 88.24s**, no warnings. This includes real isolated dbt builds/contracts and AppTests. Nine existing live-warehouse smoke cases skip because production marts are absent; independent fixture dashboard cases run.

`uv run pytest --collect-only` → **383 tests collected in 1.75s** (measured 2026-10-03 UTC). Twelve cases were added by this wave. Live docs guard independently confirms **34 dbt models / 157 dbt tests / 383 collected pytest cases**.

Docs GREEN: `uv run pytest tests/test_docs_describe_current_paths.py` → **6 passed in 5.19s**.

- `uv run ruff check src tests dashboard`: **All checks passed!**
- `uv run ruff format --check src tests dashboard`: **88 files already formatted**.
- `uv run mypy`: **Success: no issues found in 26 source files**.
- `git diff --check`: passed, no output.

Post-fix full-suite verification by the controller:

- `uv run --group orchestration pytest`: **374 passed, 9 skipped in 345.19s**; exit 0.
- `uv run pytest --collect-only`: **383 tests collected**; live documentation counts match 34 dbt models and 157 dbt tests.
- The full-suite run includes and passes the documentation-count guard.
- The focused suite, Ruff check/format, mypy, and diff check were also rerun after the final review changes; all passed.

## Self-review and boundaries

Reviewed profile/run/NCT join identity, the observation-selection boundary before filtering, actual predecessor lookup, raw/parsed first-post fields, exact proxy boundary including future dates, continuing recruiter exclusion, count-input versus event contributors, explicit-empty facility semantics, exclusion reconciliation, overlapping independent flags, threshold warn/retain behavior, UTC day alignment, rule/version exports, and scope of all docs edits.

No unrelated priority score or derived-measure recomputation was introduced. Active rule hashes remain active configuration identity, not invented historical rule versions. Historical receipts and raw evidence remain nullable when absent; raw references do not guarantee indefinite bronze retention. The event export enumerates distinct event observations plus distinct NCT IDs; sums across overlapping segments can still repeat studies/events. A full-suite run is intentionally left to the final controller as requested.
