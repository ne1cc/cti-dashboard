# Profile retirement: expected output differences

Step 1 of 4 (#146) is additive: it adds `registry_trial_key` and `registry_condition_key` beside
`indication_profile_id` and removes nothing. Baseline: `tests/fixtures/mart_baseline/`.

| Step | Table / column | Expected difference | Why |
|------|----------------|---------------------|-----|
| 1 | none | none | Only new columns are added; `tests/test_mart_baseline.py` proves every pre-existing column and row is unchanged. |

Later steps append their rows here, with the reason, before regenerating the baseline.
