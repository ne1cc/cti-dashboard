# Density proxy falsification test

Tests whether `recruiting_trial_count` in `mart_recruiting_competition` (the recruiting-listing density behind the potential competition signal) relates to what happens to trials at the next weekly snapshot. It is a check on the proxy, not an enrollment prediction (see `docs/clinical_interpretation_guardrails.md`).

## Method
- Cohort: trial-in-segment observations that are RECRUITING at snapshot t (`src/analysis/density_proxy_test.py`, tables `fct_trial_snapshot`, `fct_trial_site`, `bridge_trial_condition`).
- Outcome at the profile's next snapshot t+1: left RECRUITING for TERMINATED/WITHDRAWN/SUSPENDED, or still RECRUITING. Trials missing at t+1 are counted separately (`absent_t1`) and excluded from rates.
- Density terciles (low/mid/high) are formed across states within the same profile, condition_group, phase and snapshot, to control for those confounders.

## Limits
- With fewer than 2 snapshots, or any bin with n<30, the report says INCONCLUSIVE; a null result is a valid result.
- Terciles use `ntile`, which splits ties arbitrarily; groups with one density value are dropped.
- Trials in several states or snapshot pairs appear more than once; no significance test is applied, so gaps are descriptive.
- Registry status lags operations; terminal exits within one week are rare.

## Run
`uv run python -m src.analysis.density_proxy_test --db data/warehouse/clinical_trials.duckdb`
