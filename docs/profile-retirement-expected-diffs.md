# Profile retirement: expected output differences

Step 1 of 4 (#146) is additive: it adds `registry_trial_key` and `registry_condition_key` beside
`indication_profile_id` and removes nothing. Baseline: `tests/fixtures/mart_baseline/`.

| Step | Table / column | Expected difference | Why |
|------|----------------|---------------------|-----|
| 1 | none | none | Only new columns are added; `tests/test_mart_baseline.py` proves every pre-existing column and row is unchanged. |

Later steps append their rows here, with the reason, before regenerating the baseline.

## Step 2 (#147): one registry-wide run, profiles as presets

A preset is MeSH node(s) (`preset.mesh_terms` in `config/profiles/<id>.yml`) plus the profile's
`scope.study_type` / `scope.statuses` filters, applied locally over the registry-wide run's pages
(`src/transform/preset_runs.py`). The ADRD and NSCLC sets are anchored in
`config/condition_taxonomy*.yml`. The fixture warehouse still builds each profile through
`run_transform`, so `tests/fixtures/mart_baseline/` is unchanged and `tests/test_mart_baseline.py`
passes without regeneration. The differences below appear only on a real run and were **not
measured**: a full registry pull is about 5 GB and was not run for this PR.

| Step | Table / column | Expected difference | Why |
|------|----------------|---------------------|-----|
| 2 | `dim_trial` and every mart, per profile | Row set can differ from the old per-profile pull | CTG expanded `query.cond` with its own synonym handling; the preset matches the MeSH terms CTG derived for each study (direct or ancestor), so a study matched only by a free-text synonym drops out, and one matched only through a MeSH ancestor comes in. |
| 2 | Studies with no derived MeSH (about 4% of the registry) | Never selected by any preset | The selector reads `derivedSection.conditionBrowseModule`; without it there is nothing to match. |
| 2 | Refresh mode of `orchestrate` / `make pipeline` | Every run after the first is an incremental registry pull (was: a full per-profile snapshot each run), except that a newest registry run written before this change (no MeSH terms in its field projection) makes the first pull a full one | A whole-registry snapshot is about 5 GB. No full snapshot is ever scheduled; force one with `make pipeline ORCHESTRATE_FLAGS=--full-refresh` (or `make orchestrate-full-refresh`). |
| 2 | A study the incremental registry run re-sent that no longer matches a preset (study type, status or MeSH changed) | Dropped from the preset's new run | The run replaces every study it touched; only untouched studies are carried forward. |
| 2 | A study that stops matching without the registry reporting a change (a `preset.mesh_terms` edit, a study withdrawn from the registry) | Stays in a preset's silver until the next full registry snapshot | An incremental run only holds changed studies and cannot see these. |
| 2 | Derived runs' bronze (`raw_page_reference`) | Points into the registry run's tree, not the preset's | A preset's bronze tree holds its manifest and summary only; pages stay in the registry run's tree. |
| 2 | Registry run retention | `bronze_runs_to_keep: 1` once every refreshable preset has derived from a run; a run some preset has not derived is kept up to `snapshot_runs_to_keep` (3, about 15 GB of pages) | The registry profile has no silver of its own, so `prune_profile` treats a run as safe once every refreshable preset has a success manifest with that `source_run_id`. A run pruned before a preset derives from it cannot be derived later: the preset's next derivation fails with an error (it never skips the run), and a preset with no derived run, or one behind a pruned run, needs a new full base snapshot (`orchestrate --full-refresh`). Registry runs are never held back for a lagging preset, so disk stays bounded at `snapshot_runs_to_keep`. |
| 2 | `ingest --incremental-refresh` for a named profile | Unchanged | Only `orchestrate` moved to the registry-wide pull. |
| 2 | `full_catalog` grain | Unchanged (profile-stamped) | Any registry-wide grain is decided in #148/#149. |
