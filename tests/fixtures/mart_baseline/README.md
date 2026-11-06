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
