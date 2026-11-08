# Mart baseline (profile retirement, step 1 of 4)

Row-level snapshot of every table in the `main_marts` schema (except `dim_date`) built from the
default two-profile fixture warehouse: ADRD run `20260901T120000Z_fixture01` and NSCLC run
`20260901T120001Z_fixture02`, the same warehouse as the `fixture_project_root` pytest fixture.

Each `<table>.json` holds `columns` and `rows` (sorted, one row per line). It was generated from
`main` at `be16a2c`, before any registry-wide key column existed, with:

```bash
uv run python -m tests.mart_baseline --root "$(mktemp -d)/baseline"
```

`tests/test_mart_baseline.py` projects the live fixture warehouse onto these columns and compares:
ids, counts and strings exactly, floats to 1e-6 relative. New columns are ignored; changed rows,
row counts or removed columns fail.

Regenerate only when a later step intentionally changes a mart output, and record the change in
`docs/profile-retirement-expected-diffs.md` in the same PR.

## #166 shared-trial and distinct-snapshot companion

The committed JSON remains the original default fixture (19 marts, 329 rows).
`tests/preset_helpers.py:shared_trial_runs` defines a separate synthetic input variant using
this repository's `tests/fixtures/bronze_snapshot/page=00001.json`, not copied mart rows:

- ADRD: September 1 and 15; NSCLC: September 8, over the same ten NCT IDs.
- NCT00000001 has distinct status, enrollment and sponsor fields in each run, plus shared
  direct/ancestor MeSH nodes; NCT00000010 has no raw condition or derived MeSH.
- `shared_trial_project_root` in `tests/conftest.py` runs the real transform and dbt build.
  The identity test requires both profile keys to equal `md5(nct_id)` and be non-NULL;
  the snapshot test pins all three observations and each profile's current version.

This companion is not a replacement baseline. Reproduction, measured results and a temporary
profile-dependent-hash mutation check are in `docs/profile-retirement-expected-diffs.md`.
