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
dbt stores as `registry_condition_key`. A preset also belongs to exactly one profile (its YAML id,
`Preset.profile_id`), and every reader binds that profile as well as the keys: the key does not
include the profile, and two profiles may share a node id, so keys alone would read both profiles'
rows and count a shared trial twice. Condition-grain marts filter on profile and key, trial-grain
tables on the profile's trials under those keys in `bridge_trial_condition` (plus its trials with
no bridge row), and profile-grain marts on the profile. Adding a preset is one YAML file (`tests/test_presets.py` proves it with a throwaway one).
`tests/test_presets.py` also holds the ADRD and NSCLC preset results to the #146 baseline, so the
baseline is unchanged and not regenerated.

| Step | Table / column | Expected difference | Why |
|------|----------------|---------------------|-----|
| 3 | ADRD and NSCLC dashboard results | none | A profile-and-key scope returns exactly the profile's rows (ids and counts exact, rates within 1e-6). |
| 3 | Sidebar | "Indication preset" list with a "Default filters" caption; the "additive / counted once" caption is gone | The caption described profile-stamped rows; a preset's nodes are not double-counted. |
| 3 | `get_indication_profiles`, the pre-profile warehouse branch of `trial_explorer` | Removed | Presets come from config, and the key scope needs `bridge_trial_condition` and `dim_condition`. A warehouse built before the profile columns no longer renders. |
| 3 | `refreshable_profiles()` (`dashboard/components/profile.py`) | Removed | `render_preset_selector()` reads `load_presets()`, which already lists the refreshable profiles. |

Limits (data and docs, no dbt change until #149):

- A preset whose taxonomy has no groups resolves to the single default node, which every such
  preset shares. `preset.mesh_terms` are descriptor labels and the marts carry no descriptor ids,
  so a MeSH-only preset shows data only where the marts carry its keys.
- Profile-grain marts (reliability, transparency, audit) cannot be narrowed below the profile: a
  preset reads its whole owning profile.
- A trial with no mapped condition has no `bridge_trial_condition` row. It stays in its owning
  profile's trial counts, Trial Explorer and sponsor landscape (as in the #146 baseline), for every
  preset of that profile, including a narrower one, because no node excludes it.
- A preset whose profile is in the warehouse but none of whose nodes are (a renamed or misspelled
  taxonomy group) gets its own warning on the page; one whose profile has no rows yet gets the
  "not yet refreshed" warning. A preset with only some nodes missing is not flagged: a group with no
  trials has no `dim_condition` row.

## Step 4 (#149): shared paths and dead config out of YAML

Re-scoped from "drop the old grain": `indication_profile_id` is still the live grain (silver is
produced per profile and every preset reader binds the owning profile, step 3), so the column, every
dbt model and mart, and `tests/fixtures/mart_baseline/` are untouched. Dropping the column and
re-keying the marts on `nct_id` is #164. This step only moves the silver, gold and DuckDB paths into
`SHARED_*` constants in `src/profiles.py`, deletes the YAML that held them, the three path keys
every profile YAML declared and `src/profiles.py` overrode, and the `guardrails` block nothing read.

| Step | Table / column | Expected difference | Why |
|------|----------------|---------------------|-----|
| 4 | none | none | No path value, column or row changes; `tests/test_mart_baseline.py` and `tests/test_presets.py` pass without regenerating the baseline. A live warehouse needs no rebuild. |

## #166: measured fixture evidence before registry-grain migration

Measured on 2026-11-08 from #166 head `4c3317e` plus the review fixes. Production Python,
SQL, configuration and committed baseline JSON are unchanged. These are synthetic fixture
measurements, not a registry-wide before/after comparison.

| Input / comparison | Measured result | Interpretation |
|---|---|---|
| Default two-profile fixture against #146 | 19 baseline marts, 329 rows; zero changed prior-column rows, zero row-count differences | `test_existing_mart_outputs_match_baseline` compares every baseline cell (exact IDs/counts/strings, 1e-6 relative floats). No baseline regeneration. |
| Default ADRD/NSCLC preset readers against #146 | All 10 reader × preset comparisons pass, plus exact Trial Explorer IDs/counts | Covers priority, competition, geography, site overlap and reliability; does not establish live registry-selection equivalence. |
| Independent shared-trial fixture | `dim_trial`: 20 profile rows, 10 distinct NCT IDs and 10 registry keys; every row equals `md5(nct_id)` | The same ten bronze studies are transformed independently through ADRD and NSCLC taxonomies. Identity is computed by production dbt, never copied from an existing dimension. |
| Distinct snapshots in that fixture | `fct_trial_snapshot`: 30 rows on 3 dates; 10 registry identities. Reliability: 3 run rows, each with 10 trial rows and 10 distinct trials | ADRD has September 1 and 15 observations; NSCLC has September 8. A global latest-date filter would lose NSCLC's current rows. The extra history and reliability rows are deliberate input differences, not changes to production output for fixed input. |
| Shared direct/ancestor MeSH nodes | NCT00000001 has one `D000544` and one `D001523` bridge row per profile | Neither shared identity nor the ancestor edge may fan out a profile's bridge. |
| No raw condition and no derived MeSH | NCT00000010 remains as 2 profile dimension rows with `has_derived_mesh=false` and zero condition bridge rows | Current unmapped behavior is retained. This fixture directly transforms bronze; it does not pass through registry MeSH preset selection. |

NCT00000001 deliberately changes fields between source observations:

| Source run | Profile | Date (UTC) | Status | Enrollment | Lead sponsor | Current for profile |
|---|---|---|---|---:|---|---|
| `20260901T100000Z_shared0` | ADRD | 2026-09-01 | RECRUITING | 500 | NeuroPharm Inc | false |
| `20260908T100000Z_shared1` | NSCLC | 2026-09-08 | ACTIVE_NOT_RECRUITING | 600 | Variant Sponsor | true |
| `20260915T100000Z_shared2` | ADRD | 2026-09-15 | COMPLETED | 700 | Latest Sponsor | true |

All three source payload hashes differ. The dimension exposes the last two rows as separate
profile versions, including the older NSCLC fields; it must not borrow ADRD's newer values.
No canonical conflict resolution is implemented here. The proposed version/field policy and
exact phase 2–5 file inventory are in `docs/registry-grain-inventory.md`; unresolved authority,
no-MeSH membership, report-denominator, API and configuration decisions remain gates.

### Reproduce without changing a baseline or user data

Run from the repository root. Both commands create fresh scratch fixture roots and use the
actual transform/dbt path. The Python block compares every baseline row, then prints the
shared-trial measurements above. It never invokes the baseline writer.

```bash
uv run pytest tests/test_presets.py tests/test_mart_baseline.py
TASK_TMP=$(mktemp -d)
uv run python - "$TASK_TMP" <<'PY'
import json
import sys
from pathlib import Path

import duckdb

from tests.fixture_warehouse import build_fixture_warehouse
from tests.mart_baseline import cells_match, dump_marts, load_baseline
from tests.preset_helpers import shared_trial_runs

scratch = Path(sys.argv[1])
base = build_fixture_warehouse(scratch / "base")
baseline, actual = load_baseline(), dump_marts(base)
changed, compared = 0, 0
for name, expected in baseline.items():
    indexes = [actual[name]["columns"].index(c) for c in expected["columns"]]
    rows = sorted(
        ([row[i] for i in indexes] for row in actual[name]["rows"]),
        key=lambda row: json.dumps(row, sort_keys=True),
    )
    assert len(rows) == len(expected["rows"]), name
    compared += len(rows)
    changed += sum(
        not all(cells_match(w, h) for w, h in zip(want, have, strict=True))
        for want, have in zip(expected["rows"], rows, strict=True)
    )
print(f"baseline: {len(baseline)} marts, {compared} rows, {changed} changed")
assert changed == 0
shared = build_fixture_warehouse(scratch / "shared", runs=shared_trial_runs())
with duckdb.connect(str(shared / "data/warehouse/clinical_trials.duckdb"), read_only=True) as con:
    for label, sql in (
        ("dimension", "select count(*), count(distinct nct_id), "
         "count(distinct registry_trial_key) from main_marts.dim_trial"),
        ("history", "select count(*), count(distinct snapshot_date), "
         "count(distinct registry_trial_key) from main_marts.fct_trial_snapshot"),
        ("unmapped", "select count(*) from main_marts.dim_trial d where not exists "
         "(select 1 from main_marts.bridge_trial_condition b where b.trial_key=d.trial_key)"),
        ("reliability", "select indication_profile_id, snapshot_date, trial_row_count, "
         "distinct_trial_count from main_marts.mart_data_reliability order by snapshot_date"),
        ("versions", "select indication_profile_id, snapshot_date, ingestion_run_id, "
         "overall_status, enrollment_count, lead_sponsor_name, current_record_flag "
         "from main_marts.fct_trial_snapshot where nct_id='NCT00000001' order by snapshot_date"),
    ):
        print(label, con.execute(sql).fetchall())
PY
```

### Discrimination check: profile-dependent hash

A separate temporary copy of the dbt project was mutated so `dim_trial.registry_trial_key`
hashes `['c.nct_id', 'c.indication_profile_id']` instead of `['c.nct_id']`. Running only
`test_shared_trials_keep_one_registry_identity_across_presets` against that copy failed at the
per-row `[expected, expected]` assertion: **1 failed, 19 deselected**. The unchanged project
passes. The same assertion requires both rows to be non-NULL and NCT-derived; a single non-NULL
key plus a NULL no longer passes through `count(distinct)`. A separate private-warehouse
check set the NSCLC key for NCT00000001 to NULL and directly invoked the same test assertion;
it also raised `AssertionError`, without modifying a production model or the source fixture.

Reproduce the mutation without editing the production model or sharing generated keys:

```bash
TASK_TMP=$(mktemp -d)
uv run python - "$TASK_TMP" <<'PY'
import shutil
import subprocess
import sys
from pathlib import Path

repo, mutant = Path.cwd(), Path(sys.argv[1]) / "mutant"
mutant.mkdir()
for name in ("tests", "dbt_clinical_trials"):
    shutil.copytree(
        repo / name, mutant / name,
        ignore=shutil.ignore_patterns("__pycache__", "target", "dbt_target", "logs"),
    )
for name in ("src", "dashboard", "config"):
    (mutant / name).symlink_to(repo / name, target_is_directory=True)
model = mutant / "dbt_clinical_trials/models/marts/dim_trial.sql"
old = "generate_surrogate_key(['c.nct_id']) }} as registry_trial_key"
new = "generate_surrogate_key(['c.nct_id', 'c.indication_profile_id']) }} as registry_trial_key"
text = model.read_text()
assert text.count(old) == 1
model.write_text(text.replace(old, new))
result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/test_presets.py", "-k",
     "shared_trials_keep_one_registry_identity_across_presets"],
    cwd=mutant, capture_output=True, text=True,
)
print(result.stdout)
print(result.stderr)
assert result.returncode == 1
assert "assert [row[2] for row in shared] == [expected, expected]" in result.stdout
assert "1 failed, 19 deselected" in result.stdout
PY
```

Remaining evidence gap: no full registry pull, retained live warehouse rebuild or matched live
preset-set comparison was performed for #166. The earlier step 2 selection differences above
remain unmeasured; the historical “about 4%” statement is not a measurement made by this PR.
The synthetic results cannot approve replacing the baseline or dropping profile-grain reports.

## Phase 2 (#168): parallel canonical silver and staging

Phase 2 builds the canonical registry silver streams (nine entities) and staging models (`stg_registry_*`) alongside existing profile silver and staging models. Existing marts and profile models remain untouched, preserving the baseline.

| Step | Table / column | Expected difference | Why |
|------|----------------|---------------------|-----|
| Phase 2 | Profile silver and marts | none | Parallel additive architecture; existing models and baseline fixtures remain completely unchanged and pass. |
| Phase 2 | `registry_trials` and other `registry_*` silver tables | New parallel entity files under `data/silver/registry_*/run_id=<id>.parquet` | Canonical registry observations without indication profile scoping. |
| Phase 2 | `stg_registry_*` staging models | 10 new models in staging | Views over canonical registry silver and manifests providing `registry_trial_key`. |
| Phase 2 | `assert_registry_observation_integrity` | New singular test | Enforces canonical observation uniqueness and child entity referential integrity. |

