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

## Step 3 (#148): the dashboard selects a preset, not a profile id

A preset is a name, registry condition nodes and default filters, derived from the existing
`config/profiles/*.yml` and its taxonomy by `src/presets.py`. Its nodes are the taxonomy's
`group:<slug>` ids (every group plus the default group); their keys are `md5(node_id)`, the value
dbt stores as `registry_condition_key`. Readers filter on those keys, not on `indication_profile_id`:
condition-grain marts on `registry_condition_key`, trial-grain tables through
`bridge_trial_condition`, and profile-grain marts through the `dim_condition` profiles that hold a
key. Adding a preset is one YAML file (`tests/test_presets.py` proves it with a throwaway one).
`tests/test_presets.py` also holds the ADRD and NSCLC preset results to the #146 baseline, so the
baseline is unchanged and not regenerated.

| Step | Table / column | Expected difference | Why |
|------|----------------|---------------------|-----|
| 3 | ADRD and NSCLC dashboard results | none | The two groups' keys are disjoint, so a key scope returns exactly the profile's rows (ids and counts exact, rates within 1e-6). |
| 3 | Sidebar | "Indication preset" list with a "Default filters" caption; the "additive / counted once" caption is gone | The caption described profile-stamped rows; a preset's nodes are not double-counted. |
| 3 | `get_indication_profiles`, the pre-profile warehouse branch of `trial_explorer` | Removed | Presets come from config, and the key scope needs `bridge_trial_condition` and `dim_condition`. A warehouse built before the profile columns no longer renders. |

Limits (data and docs, no dbt change until #149):

- A preset whose taxonomy has no groups resolves to the single default node, which every such
  preset shares. `preset.mesh_terms` are descriptor labels and the marts carry no descriptor ids,
  so a MeSH-only preset shows data only where the marts carry its keys.
- Profile-grain marts (reliability, transparency, audit) cannot be narrowed below the profile: a
  preset reads every profile that holds one of its keys.
- A trial with no conditions has no `bridge_trial_condition` row, so it is outside every preset.
