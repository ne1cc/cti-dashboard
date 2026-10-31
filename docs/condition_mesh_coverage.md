# MeSH condition coverage and disagreement report (Issue #52)

**Status: fixture-limited with real-record review.** The fixture table numbers
below are measured against committed test fixtures (`tests/fixtures/condition_mesh/`
plus the two base bronze snapshot fixtures), not against a full production refresh.
To ensure representative coverage and honest disagreement analysis, real-world
studies from both the local dev bronze (ADRD) and the live ClinicalTrials.gov
v2 API (NSCLC) were reviewed and are documented below.

- Source: ClinicalTrials.gov API v2, `derivedSection.conditionBrowseModule`
- Spec: <https://clinicaltrials.gov/api/oas/v2> (OpenAPI 3.0.3, `info.version` 2.0.5),
  schemas `DerivedSection` → `BrowseModule` → `Mesh` / `BrowseLeaf` / `BrowseBranch`
- Real-data sources:
  - ADRD: local dev bronze snapshot `run_id=20260905T012941Z_fe9f104f` (2,618 studies, 2,523 with `conditionBrowseModule`)
  - NSCLC: live ClinicalTrials.gov API v2 query `query.cond=Non-small+cell+lung+cancer`
  - Committed fixture provenance: `GET /api/v2/studies/NCT02253732`, 2026-10-30
- Report date: 2026-10-30 (updated Round 1 review)

## Verified wire shape

`conditionBrowseModule` and `interventionBrowseModule` are the same
`BrowseModule` type:

| field | type | semantics |
|---|---|---|
| `meshes` | `[{id, term}]` | MeSH descriptors applied directly to the record |
| `ancestors` | `[{id, term}]` | ancestor closure of those descriptors (flat, no depth) |
| `browseLeaves` | `[{id, name, asFound, relevance}]` | leaves with LOW/HIGH relevance |
| `browseBranches` | `[{abbrev, name}]` | branch labels with **no edge to terms** |

Verified against the spec and live API responses:
1. The module is **study-level**: the registry derives terms for the whole
   record, so there is no per-condition-text edge. `silver_trial_condition_mesh`
   rows are therefore per trial, and a condition's MeSH identity joins at
   trial grain (`bridge_trial_condition`), not per condition string.
2. `browseBranches` carries no term linkage, so the `branch` column is NULL
   everywhere; no branch attribution is inferred.
3. Studies without a derived section (new or unprocessed records) contribute zero
   MeSH rows and are flagged explicitly with `has_derived_mesh = false` on `dim_trial`.

## Coverage (fixture denominators)

| warehouse | trials | with `conditionBrowseModule` | direct MeSH rows (distinct terms) | distinct ancestor nodes | overlay (yaml) mapped trials | fallback trials |
|---|---|---|---|---|---|---|
| ADRD × mesh fixture (`tests/fixtures/condition_mesh/`) | 3 | 2 (67%) | 6 (5) | 15 | 2 of 3 (NCT99000001, NCT99000003) | 1 of 3 (NCT99000002) |
| NSCLC × mesh fixture (same page, NSCLC taxonomy) | 3 | 2 (67%) | 6 (5) | 15 | 0 of 3 | 3 of 3 (all catch-all) |
| ADRD base fixture (`bronze_snapshot`) | 10 | 0 (0%) | — | — | 7 of 10 | 3 of 10 |
| NSCLC base fixture (`bronze_snapshot_nsclc`) | 10 | 0 (0%) | — | — | 7 of 10 | 3 of 10 |

**Missing-derived path:**
Studies lacking a derived section contribute no MeSH rows (`has_derived_mesh = false`).
Their condition grouping is completely independent of derived section presence:
- When a taxonomy rule matches, they carry `mapping_source = 'yaml_overlay'`.
  `NCT99000003` is the in-fixture example: condition "Frontotemporal Dementia"
  matches the taxonomy substring rule for `frontotemporal_dementia` (`yaml_overlay`),
  while carrying zero MeSH rows and `has_derived_mesh = false`.
- When no taxonomy rule matches (or when a profile has no taxonomy), condition text
  falls back to the catch-all group or `unmapped` with `mapping_source = 'text_fallback'`.

## Disagreement review: MeSH-derived vs YAML overlay groupings

### Real ADRD records (dev bronze run `20260905T012941Z_fe9f104f`)

In a review of 500 studies from the dev bronze (where 2,523 of 2,618 studies have `conditionBrowseModule`),
171 studies mapped at least one condition text to catch-all (`non_dementia_other`) or `unmapped`.
Key patterns of disagreement:

1. **Healthy volunteer / control cohorts (`NCT05532358`):**
   - Condition text: `['Healthy Volunteers']`.
   - YAML overlay: maps to `non_dementia_other` with low confidence.
   - MeSH derived: `['Parkinson Disease', 'Alzheimer Disease', 'Multiple System Atrophy', 'Neurodegenerative Diseases', 'Tauopathies']`.
   - Review: Because the trial evaluates healthy volunteers as a control group for neurodegenerative disease cohorts, the registry derives the underlying disease context from the full study record. MeSH restores discoverability that the text overlay lost.

2. **Underlying syndrome and biomarker terms (`NCT05462106`):**
   - Condition text: `['Amyloid Plaque', 'Beta-Amyloid', 'DSAD', "Prodromal Alzheimer's Disease", "Alzheimer's Disease"]`.
   - YAML overlay: maps `Alzheimer's Disease` to `alzheimers_disease`, but `Amyloid Plaque` and `DSAD` fall back to `non_dementia_other`.
   - MeSH derived: `['Plaque, Amyloid', 'Alzheimer Disease', 'Dementia', 'Brain Diseases', 'Down Syndrome']`.
   - Review: MeSH retains the Down Syndrome (DSAD) and amyloid pathology links, enabling cross-disease research across the MeSH hierarchy.

3. **Multi-condition / brain injury studies (`NCT05446584`):**
   - Condition text: `['Mild Cognitive Impairment', 'Amnestic Mild Cognitive Disorder', 'Mild Traumatic Brain Injury', 'Concussion, Brain']`.
   - YAML overlay: maps MCI to `mild_cognitive_impairment` and sends concussion terms to `non_dementia_other`.
   - MeSH derived: `['Cognitive Dysfunction', 'Brain Concussion']`.
   - Review: MeSH accurately indexes both the cognitive and traumatic brain injury facets without bespoke taxonomy rules.

### Real NSCLC records (live ClinicalTrials.gov API v2)

1. **Complication & symptom studies (`NCT03803787`):**
   - Condition text: `['Radiation Pneumonitis']`.
   - YAML overlay: maps to catch-all `other_thoracic_oncology` (low confidence).
   - MeSH derived: `['Radiation Pneumonitis', 'Carcinoma, Non-Small-Cell Lung', 'Thoracic Neoplasms', 'Lung Diseases']`.
   - Review: A trial investigating radiation pneumonitis in lung cancer patients is linked directly to `Carcinoma, Non-Small-Cell Lung` in MeSH, making it discoverable under both lung cancer and radiation injury trees.

2. **Basket and multi-indication trials (`NCT03836352`):**
   - Condition text: `['Ovarian Cancer', 'Hepatocellular Carcinoma', 'Non-small Cell Lung Cancer', 'Bladder Cancer']`.
   - YAML overlay: maps `Non-small Cell Lung Cancer` to `nsclc_unspecified` and sends the other three tumor types to `other_thoracic_oncology`.
   - MeSH derived: `['Carcinoma, Non-Small-Cell Lung', 'Ovarian Neoplasms', 'Carcinoma, Hepatocellular', 'Urinary Bladder Neoplasms']`.
   - Review: The YAML overlay is blind to non-NSCLC malignancies; MeSH indexes all four distinct tumor types faithfully.

3. **Broad terminology (`NCT00080509`):**
   - Condition text: `['Lung Cancer']`.
   - YAML overlay: maps to `other_thoracic_oncology` because the rule expects the substring "non-small cell".
   - MeSH derived: `['Lung Neoplasms']` (ancestor of Non-Small Cell Lung Carcinoma).
   - Review: MeSH ancestor closure allows broader disease queries to locate studies where the sponsor specified only a higher-level term.

### Summary of taxonomy overlay compatibility

Existing ADRD and NSCLC views continue rendering through the YAML overlays:
- Segment marts (`fct_trial_site`, `mart_recruiting_competition`, `mart_feasibility_priority_queue`) and dashboard filters rely on taxonomy `condition_group` slugs (`mesh_id is null`).
- Count metrics read `int_trial_condition_mapping` (which is group-only), so ancestor closure does not inflate counts.
- `mart_trial_similarity` candidate discovery and scoring strictly require `mesh_id is null`, ensuring similarity scores and rankings remain unchanged.

## How to reproduce

```bash
TMP_DIR=$(mktemp -d)

# 1. Base fixtures (ADRD and NSCLC base snapshot runs)
uv run python -m tests.fixture_warehouse --root "${TMP_DIR}/base_db" --prepare-only

# 2. Build fixture warehouse with MeSH fixture for ADRD and NSCLC
python -c "
import os
from pathlib import Path
from tests.fixture_warehouse import build_fixture_warehouse, FixtureRun
from tests.test_condition_mesh import mesh_run

tmp = Path(os.environ['TMP_DIR'])
build_fixture_warehouse(tmp / 'mesh_db', runs=[mesh_run()])
"

# 3. Inspect warehouse in DuckDB
cd "${TMP_DIR}/mesh_db" && uv run duckdb data/warehouse/clinical_trials.duckdb
```

```sql
-- 1. Coverage: derived-section presence and explicit has_derived_mesh flag
select nct_id, has_derived_mesh, current_overall_status
from main_marts.dim_trial
order by nct_id;

-- 2. Per-node browsing in dim_condition (any tree node selectable)
select node_id, condition_normalized, mapping_source, is_ancestor, trial_count
from main_marts.dim_condition
order by mapping_source, node_id;

-- 3. Disagreement review: overlay nodes vs direct MeSH terms per trial
select
    nct_id,
    list(case when mapping_source != 'mesh' then condition_group end) as overlay_nodes,
    list(case when mapping_source = 'mesh' and not is_ancestor then condition_group end) as direct_mesh_terms
from main_marts.bridge_trial_condition
group by 1
order by 1;
```
