# MeSH condition coverage and disagreement report (Issue #52)

**Status: fixture-limited.** Every number below is measured against the
committed test fixtures (`tests/fixtures/condition_mesh/` plus the two bronze
snapshot fixtures), not against a production refresh. The denominators are
fixture trials, not registry population. No production data was rebuilt for
this report.

- Source: ClinicalTrials.gov API v2, `derivedSection.conditionBrowseModule`
- Spec: <https://clinicaltrials.gov/api/oas/v2> (OpenAPI 3.0.3, `info.version` 2.0.5),
  schemas `DerivedSection` → `BrowseModule` → `Mesh` / `BrowseLeaf` / `BrowseBranch`
- Live capture used for the fixture: `GET /api/v2/studies/NCT02253732`, 2026-10-30
- Report date: 2026-10-30

## Verified wire shape

`conditionBrowseModule` and `interventionBrowseModule` are the same
`BrowseModule` type:

| field | type | semantics |
|---|---|---|
| `meshes` | `[{id, term}]` | MeSH descriptors applied directly to the record |
| `ancestors` | `[{id, term}]` | ancestor closure of those descriptors (flat, no depth) |
| `browseLeaves` | `[{id, name, asFound, relevance}]` | leaves with LOW/HIGH relevance |
| `browseBranches` | `[{abbrev, name}]` | branch labels with **no edge to terms** |

Verified against the spec and a live response. Two consequences drive the
implementation:

1. The module is **study-level**: the registry derives terms for the whole
   record, so there is no per-condition-text edge. `silver_trial_condition_mesh`
   rows are therefore per trial, and a condition's MeSH identity joins at
   trial grain (`bridge_trial_condition`), not per condition string.
2. `browseBranches` carries no term linkage, so the `branch` column is NULL
   everywhere; no branch attribution is inferred. The existing fixtures
   (`bronze_snapshot*`) predate `derivedSection` and cannot demonstrate the
   shape — `tests/fixtures/condition_mesh/page=00001.json` was added for that,
   with per-study provenance in its `_provenance` block.

## Coverage (fixture denominator)

| warehouse | trials | with `conditionBrowseModule` | direct MeSH rows (distinct terms) | distinct ancestor nodes | overlay (yaml) mapped trials |
|---|---|---|---|---|---|
| ADRD × mesh fixture (`tests/fixtures/condition_mesh/`) | 3 | 2 (67%) | 6 (5) | 15 | 2 of 3 |
| NSCLC × mesh fixture (same page, NSCLC taxonomy) | 3 | 2 (67%) | 6 (5) | 15 | 0 of 3 |
| ADRD base fixture (`bronze_snapshot`) | 10 | 0 | — | — | 10 of 10 |
| NSCLC base fixture (`bronze_snapshot_nsclc`) | 10 | 0 | — | — | 10 of 10 |

The 0/10 rows are the flagging path the issue requires: records without a
derived section (new or unprocessed) contribute no MeSH rows, keep their
overlay groups when a taxonomy matches, and otherwise carry
`mapping_source = text_fallback`. `NCT99000003` in the mesh fixture is the
in-fixture example: one `text_fallback` group row, zero mesh rows.

## Where MeSH-derived and YAML groupings disagree

Per-trial comparison on the mesh fixture (ADRD taxonomy):

| trial | YAML overlay says | MeSH says (direct → closure) | reviewed |
|---|---|---|---|
| NCT99000001 | `alzheimers_disease` | `D000544 Alzheimer Disease` + `D060825`, `D010300`, `D009043` → 14 ancestors incl. `Dementia`, `Parkinsonian Disorders` | MeSH is broader than the listed condition text because the registry derives terms from the whole record (population and interventions included). Expected registry behavior; the overlay group stays the segment label and MeSH adds browsing. No action. |
| NCT99000002 | `non_dementia_other` (text_fallback) | `D005662 Friedreich Ataxia` → `Spinocerebellar Degenerations`, `Neurodegenerative Diseases`, `Nervous System Diseases` | The overlay loses the disease entirely (catch-all group); MeSH preserves it and makes the trial reachable from every tree node above it. This is the case the issue fixes: a condition is browsable with no new config file. |
| NCT99000003 | `frontotemporal_dementia` via the `frontotemporal` substring | none (no derived section) | Missing-derived path; zero mesh rows. The overlay keeps working — acceptance criterion for existing views. |

Same page under the NSCLC taxonomy: all three trials map to
`other_thoracic_oncology` (the NSCLC catch-all) while MeSH identity is
identical to the ADRD run. This is the per-profile disagreement by design:
group labels are profile-scoped, MeSH identity is registry-scoped, and
`dim_condition` / `bridge_trial_condition` count per profile so neither
taxonomy's labels leak into the other's counts.

Ancestor-closure risk reviewed: the shared ancestors (`D009422 Nervous System
Diseases`, `D019636 Neurodegenerative Diseases`) make both derived-section
trials selectable from those nodes. Count metrics and similarity exclude
`is_ancestor` edges, so closure adds reach without multiplying any existing
count (`fct_trial_snapshot.condition_group_count` stays 1 per trial on the
fixture; `mart_trial_similarity` is empty — the two trials share `D010300`
directly but sit in different phases, so no candidate pair forms).

## How to reproduce

```bash
TMP=$(mktemp -d)
uv run python -m tests.fixture_warehouse --root "$TMP/mesh_db" --prepare-only   # base fixtures
uv run python - <<'PY'
from pathlib import Path
from tests.fixture_warehouse import build_fixture_warehouse
from tests.test_condition_mesh import mesh_run
build_fixture_warehouse(Path("$TMP/mesh_db2"), runs=[mesh_run()])  # ADRD x mesh page
PY
cd "$TMP/mesh_db2" && uv run duckdb data/warehouse/clinical_trials.duckdb
```

```sql
-- coverage: derived-section presence per trial
select count(*) as trials,
       count(*) filter (where nct_id in (select distinct nct_id from main_staging.stg_trial_condition_mesh)) as with_derived
from main_marts.dim_trial;
-- per-node browsing (any tree node selectable)
select node_id, condition_normalized, mapping_source, is_ancestor, trial_count
from main_marts.dim_condition order by mapping_source, node_id;
-- disagreement review: overlay vs MeSH per trial
select nct_id,
       list(case when mapping_source != 'mesh' then condition_group end) as overlay_nodes,
       list(case when mapping_source = 'mesh' and not is_ancestor then condition_group end)
           as direct_terms
from main_marts.bridge_trial_condition group by 1 order by 1;
```

Populate: the committed end-to-end assertions in
`tests/test_condition_mesh.py` pin the numbers above.
