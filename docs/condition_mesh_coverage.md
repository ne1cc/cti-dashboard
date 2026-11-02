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
  - ADRD: local dev bronze snapshot `data/bronze/api_responses/run_id=20260905T012941Z_fe9f104f`
    (2,618 studies, 2,523 with `conditionBrowseModule`) — captured 2026-09-05, not
    committed to this repository; the reproduce block below accepts the path to
    wherever a copy of that snapshot lives
  - NSCLC: live ClinicalTrials.gov API v2 query `query.cond=Non-small+cell+lung+cancer`
    (sample of 50 consecutively retrieved studies: 8,646 registry total matches,
    43 of 50 with `conditionBrowseModule`, 42 of 50 overlay mapped, 21 of 50 with
    any fallback row, 8 of 50 fallback-only; full registry-wide NSCLC coverage
    is explicitly unmeasured without a complete NSCLC bronze snapshot)
  - Committed fixture provenance: `GET /api/v2/studies/NCT02253732`, 2026-10-30
- Report date: 2026-11-02 (updated review round 3)

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
   MeSH rows and are flagged explicitly with `has_derived_mesh = false` on `dim_trial`
   (distinguished from `null` / unknown for retained historical legacy runs where MeSH
   extraction was never performed).

## Coverage (fixture denominators)

| warehouse | trials | with `conditionBrowseModule` | direct MeSH rows (distinct terms) | distinct ancestor nodes | overlay (yaml) mapped trials | trials with any fallback row | fallback-only trials |
|---|---|---|---|---|---|---|---|
| ADRD × mesh fixture (`tests/fixtures/condition_mesh/`) | 3 | 2 (67%) | 6 (5) | 15 | 2 of 3 (NCT99000001, NCT99000003) | 1 of 3 (NCT99000002) | 1 of 3 (NCT99000002) |
| NSCLC × mesh fixture (same page, NSCLC taxonomy) | 3 | 2 (67%) | 6 (5) | 15 | 0 of 3 | 3 of 3 (all catch-all) | 3 of 3 |
| ADRD base fixture (`bronze_snapshot`) | 10 | 0 (0%) | — | — | 7 of 10 | 4 of 10 | 3 of 10 |
| NSCLC base fixture (`bronze_snapshot_nsclc`) | 10 | 0 (0%) | — | — | 7 of 10 | 6 of 10 | 3 of 10 |

**Missing-derived path:**
Studies lacking a derived section in a new-schema run contribute no MeSH rows (`has_derived_mesh = false`).
Retained historical runs predating Issue #52 where MeSH was never extracted preserve `has_derived_mesh = null` (unknown).
Their condition grouping is completely independent of derived section presence:
- When a taxonomy rule matches, they carry `mapping_source = 'yaml_overlay'`.
  `NCT99000003` is the in-fixture example: condition "Frontotemporal Dementia"
  matches the taxonomy substring rule for `frontotemporal_dementia` (`yaml_overlay`),
  while carrying zero MeSH rows and `has_derived_mesh = false`.
- When no taxonomy rule matches (or when a profile has no taxonomy), condition text
  falls back to the catch-all group or `unmapped` with `mapping_source = 'text_fallback'`.

## Disagreement review: MeSH-derived vs YAML overlay groupings

### Real ADRD records (dev bronze run `20260905T012941Z_fe9f104f`)

In a review of the first 500 studies of the dev bronze (where 2,523 of 2,618 studies have `conditionBrowseModule`),
171 studies mapped at least one condition text to the catch-all without a taxonomy rule above low confidence
(`text_fallback` into `non_dementia_other`, or `unmapped`).
Key patterns of disagreement:

1. **Healthy volunteer / control cohorts (`NCT05532358`):**
   - Condition text: `['Healthy Volunteers']`.
   - YAML overlay: maps to `non_dementia_other` with low confidence.
   - MeSH derived (direct terms): `['Parkinson Disease 4, Autosomal Dominant Lewy Body', 'Multiple System Atrophy', 'Parkinson Disease', 'Alzheimer Disease', 'Neurodegenerative Diseases', 'Tauopathies']`.
   - Review: Because the trial evaluates healthy volunteers as a control group for neurodegenerative disease cohorts, the registry derives the underlying disease context from the full study record. MeSH restores discoverability that the text overlay lost.

2. **Underlying syndrome and biomarker terms (`NCT05462106`):**
   - Condition text: `['Amyloid Plaque', 'Beta-Amyloid', 'DSAD', "Prodromal Alzheimer's Disease", "Alzheimer's Disease"]`.
   - YAML overlay: maps both Alzheimer's spellings to `alzheimers_disease`, but `Amyloid Plaque`, `Beta-Amyloid` and `DSAD` fall back to `non_dementia_other`.
   - MeSH derived (direct terms): `['Plaque, Amyloid', 'Alzheimer Disease', 'Dementia', 'Brain Diseases', 'Central Nervous System Diseases', 'Down Syndrome']`.
   - Review: MeSH retains the Down Syndrome (DSAD) and amyloid pathology links, enabling cross-disease research across the MeSH hierarchy.

3. **Multi-condition / brain injury studies (`NCT05446584`):**
   - Condition text: `['Mild Cognitive Impairment', 'Amnestic Mild Cognitive Disorder', 'Amnestic Mild Cognitive Impairment', 'Mild Traumatic Brain Injury', 'Concussion, Brain']`.
   - YAML overlay: maps all three MCI spellings to `mild_cognitive_impairment` and sends the brain-injury terms to `non_dementia_other`.
   - MeSH derived (direct terms): `['Cognitive Dysfunction', 'Brain Concussion']`.
   - Review: MeSH accurately indexes both the cognitive and traumatic brain injury facets without bespoke taxonomy rules.

### Real NSCLC records (live ClinicalTrials.gov API v2)

Evaluated against a reproducible sample of 50 consecutive studies from the live
ClinicalTrials.gov API v2 query (`query.cond=Non-small+cell+lung+cancer&pageSize=50&countTotal=true`).

- **Sample denominator and measurement**:
  - Total studies matching query in registry: **8,646** (as of 2026-11-02).
  - Sample size: **50** consecutively retrieved studies.
  - Studies with `conditionBrowseModule`: **43 of 50 (86.0%)**.
  - Overlay mapped studies (≥1 condition matched YAML overlay): **42 of 50 (84.0%)**.
  - Studies with any fallback row (`text_fallback`): **21 of 50 (42.0%)**.
  - Fallback-only studies (zero YAML overlay matches): **8 of 50 (16.0%)**.
  - **Scope limitation**: Full registry-wide NSCLC coverage across all 8,600+ records remains
    **unmeasured** without a complete NSCLC bronze ingestion snapshot (unlike ADRD which has
    the local dev bronze snapshot `20260905T012941Z_fe9f104f` with 2,618 records — also
    not committed; see "Real-data sources" above).

Key patterns of disagreement from the reviewed clinical scenarios. The three
studies below were **hand-picked** (not drawn from the 50-study sample and not
selected by an automated rule) to illustrate three distinct disagreement
patterns — symptom/complication indexing, basket-trial multi-indication
coverage, and broad-text ancestor closure — so they are illustrative examples,
not a ranked or representative selection:

1. **Complication & symptom studies (`NCT03803787`):**
   - Condition text: `['Radiation Pneumonitis']`.
   - YAML overlay: maps to catch-all `other_thoracic_oncology` (low confidence).
   - MeSH derived (direct terms): `['Radiation Pneumonitis', 'Carcinoma, Non-Small-Cell Lung', 'Respiratory Tract Neoplasms', 'Radiation Fibrosis Syndrome', 'Thoracic Neoplasms', 'Lung Diseases', 'Lung Injury']`.
   - Review: A trial investigating radiation pneumonitis in lung cancer patients is linked directly to `Carcinoma, Non-Small-Cell Lung` in MeSH, making it discoverable under both lung cancer and radiation injury trees.

2. **Basket and multi-indication trials (`NCT03836352`):**
   - Condition text: `['Ovarian Cancer', 'Hepatocellular Carcinoma', 'Non-small Cell Lung Cancer', 'Bladder Cancer', 'Microsatellite Instability-High']`.
   - YAML overlay: maps `Non-small Cell Lung Cancer` to `nsclc_unspecified` and sends the other tumor types to `other_thoracic_oncology`.
   - MeSH derived (direct terms): `['Ovarian Neoplasms', 'Carcinoma, Hepatocellular', 'Carcinoma, Non-Small-Cell Lung', 'Urinary Bladder Neoplasms']`.
   - Review: The YAML overlay is blind to non-NSCLC malignancies; MeSH indexes the four distinct tumor types faithfully.

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

Run from the repository root. Steps 1–2 rebuild the fixture table rows with
`dbt build` (they are not prepare-only), and step 3 re-derives the real-record
counts and the disagreement selection. The fixture warehouses reproduce only
the fixture numbers: the real-record evidence needs the local ADRD bronze
snapshot listed under "Real-data sources" (not committed to this repository)
and network access for the live NSCLC records.

```bash
TMP_DIR=$(mktemp -d)

# 1. Base fixture warehouse, both profiles, built end to end
#    (table rows "ADRD base fixture" and "NSCLC base fixture").
uv run python -m tests.fixture_warehouse --root "${TMP_DIR}/base_db"

# 2. MeSH fixture warehouses for both profiles, built end to end
#    (table rows "ADRD x mesh fixture" and "NSCLC x mesh fixture").
uv run python - "${TMP_DIR}" <<'PY'
import sys
from pathlib import Path

from tests.fixture_warehouse import build_fixture_warehouse
from tests.test_condition_mesh import mesh_run, nsclc_mesh_run

root = Path(sys.argv[1])
build_fixture_warehouse(root / "mesh_db_adrd", runs=[mesh_run()])
build_fixture_warehouse(root / "mesh_db_nsclc", runs=[nsclc_mesh_run()])
PY

# 3a. Real ADRD records. Reads the local dev bronze snapshot — provenance
#     under "Real-data sources"; pass the path to your copy as the argument.
#     Prints the totals quoted above, one line per text_fallback study in the
#     first 500 records (the disagreement selection pool), and the three
#     reviewed studies with their overlay/MeSH split.
uv run python - data/bronze/api_responses/run_id=20260905T012941Z_fe9f104f <<'PY'
import json
import sys
from pathlib import Path

from src.transform.normalize_conditions import get_taxonomy

run_dir = Path(sys.argv[1])
pages = sorted(run_dir.glob("page=*.json"))
if not pages:
    raise SystemExit(
        f"no bronze pages under {run_dir} — pass the path to your copy of the "
        "run_id=20260905T012941Z_fe9f104f snapshot (see Real-data sources)"
    )

tax = get_taxonomy()


def split_conditions(texts):
    # Mirrors the silver rule (flatten_studies): a taxonomy match with
    # confidence above low is yaml_overlay, everything else text_fallback.
    return [
        (
            text,
            tax.map_condition(text).condition_group,
            "yaml_overlay" if tax.map_condition(text).mapping_confidence != "low"
            else "text_fallback",
            tax.map_condition(text).condition_normalized is not None,
        )
        for text in texts
    ]


records = modules = fallback_studies = 0
seen = 0
for page in pages:
    for study in json.loads(page.read_text(encoding="utf-8"))["studies"]:
        records += 1
        if "conditionBrowseModule" in study.get("derivedSection", {}):
            modules += 1
        nct = study["protocolSection"]["identificationModule"]["nctId"]
        rows = split_conditions(
            study["protocolSection"].get("conditionsModule", {}).get("conditions", [])
        )
        fallback = [t for t, _, s, known in rows if s == "text_fallback" and known]
        if seen < 500 and fallback:
            fallback_studies += 1
            print("FALLBACK", nct, fallback)
        if seen < 500 and nct in {"NCT05532358", "NCT05462106", "NCT05446584"}:
            mesh = [
                m["term"]
                for m in study.get("derivedSection", {})
                .get("conditionBrowseModule", {})
                .get("meshes", [])
            ]
            print("REVIEW", nct, [(t, g, s) for t, g, s, _ in rows], mesh)
        seen += 1
print(
    f"records={records} with_conditionBrowseModule={modules} "
    f"text_fallback_studies_in_first_500={fallback_studies}"
)
PY

# 3b. Real NSCLC records (live ClinicalTrials.gov API v2, needs network).
#     Retrieves a 50-study sample from query.cond=Non-small+cell+lung+cancer,
#     measures the sample denominator and coverage metrics, and prints the three
#     reviewed studies with their overlay mapping and direct MeSH terms.
uv run python - <<'PY'
import json
import urllib.request

from src.transform.normalize_conditions import load_taxonomy

nsclc = load_taxonomy("config/condition_taxonomy_nsclc.yml")
url = "https://clinicaltrials.gov/api/v2/studies?query.cond=Non-small+cell+lung+cancer&pageSize=50&countTotal=true"
with urllib.request.urlopen(url, timeout=15) as r:
    data = json.load(r)

studies = data.get("studies", [])
total_count = data.get("totalCount")
with_module = 0
overlay_studies = 0
any_fallback_studies = 0
fallback_only_studies = 0

for study in studies:
    has_mod = "conditionBrowseModule" in study.get("derivedSection", {})
    if has_mod:
        with_module += 1
    conditions = study.get("protocolSection", {}).get("conditionsModule", {}).get("conditions", [])
    rows = [
        (c, nsclc.map_condition(c).condition_group, "yaml_overlay" if nsclc.map_condition(c).mapping_confidence != "low" else "text_fallback")
        for c in conditions
    ]
    has_ov = any(s == "yaml_overlay" for _, _, s in rows)
    has_fb = any(s == "text_fallback" for _, _, s in rows)
    if has_ov:
        overlay_studies += 1
    if has_fb:
        any_fallback_studies += 1
    if has_fb and not has_ov:
        fallback_only_studies += 1

print(
    f"nsclc_total_in_registry={total_count} sample_size={len(studies)} "
    f"with_conditionBrowseModule={with_module} overlay_mapped={overlay_studies} "
    f"any_fallback={any_fallback_studies} fallback_only={fallback_only_studies}"
)

for nct in ("NCT03803787", "NCT03836352", "NCT00080509"):
    with urllib.request.urlopen(f"https://clinicaltrials.gov/api/v2/studies/{nct}") as r:
        study = json.load(r)
    rows = [
        (t, nsclc.map_condition(t).condition_group, nsclc.map_condition(t).mapping_confidence)
        for t in study["protocolSection"].get("conditionsModule", {}).get("conditions", [])
    ]
    module = study.get("derivedSection", {}).get("conditionBrowseModule", {})
    print("REVIEW", nct, rows, [m["term"] for m in module.get("meshes", [])])
PY

# 4. Inspect any of the three warehouses.
cd "${TMP_DIR}/base_db" && uv run duckdb data/warehouse/clinical_trials.duckdb
cd "${TMP_DIR}/mesh_db_adrd" && uv run duckdb data/warehouse/clinical_trials.duckdb
cd "${TMP_DIR}/mesh_db_nsclc" && uv run duckdb data/warehouse/clinical_trials.duckdb
```

Expected outputs:

- Step 1 `base_db`: the two base-fixture table rows — 10 trials per profile,
  0 with `conditionBrowseModule`, 7 overlay-mapped trials per profile,
  4 (ADRD) / 6 (NSCLC) trials with any fallback row, and 3 (ADRD) / 3 (NSCLC)
  fallback-only trials. One ADRD trial and three NSCLC trials carry both an
  overlay row and a fallback row, which is why overlay + fallback-only equals
  total trials (7 + 3 = 10) while any fallback (4 and 6) reflects multi-condition trials.
- Step 2 `mesh_db_adrd`: 3 trials, 2 with `conditionBrowseModule`, 6 direct
  MeSH rows (5 distinct terms), 15 distinct ancestor nodes, 2 overlay-mapped,
  1 with any fallback, and 1 fallback-only.
- Step 2 `mesh_db_nsclc`: same MeSH numbers under the NSCLC taxonomy;
  0 of 3 trials overlay-mapped, 3 of 3 with any fallback, and 3 of 3 fallback-only.
- Step 3a: `records=2618 with_conditionBrowseModule=2523
  text_fallback_studies_in_first_500=171`, the 171-study fallback pool, and
  the three reviewed studies whose classifications are quoted above.
- Step 3b: `nsclc_total_in_registry=8646 sample_size=50 with_conditionBrowseModule=43 overlay_mapped=42 any_fallback=21 fallback_only=8`
  and the three reviewed studies whose classifications and MeSH terms are quoted above.

```sql
-- 0. Coverage table reproduction (measures every column of the table above)
-- Multi-run limitation: dim_trial holds current records only, while
-- stg_trial_conditions / stg_trial_condition_mesh read every retained run.
-- On a warehouse with multiple runs per profile, the overlay/fallback and
-- mesh-count columns below would aggregate superseded runs alongside the
-- current one (total_trials stays current-record). Every published row above
-- comes from a single-run fixture warehouse, so the table is unaffected;
-- on a multi-run tree, filter the staging models to the latest run first.
with trial_mapping as (
    select
        indication_profile_id,
        nct_id,
        bool_or(mapping_source = 'yaml_overlay') as has_overlay,
        bool_or(mapping_source = 'text_fallback') as has_fallback
    from main_staging.stg_trial_conditions
    group by indication_profile_id, nct_id
),
mesh_counts as (
    select
        indication_profile_id,
        count(*) filter (where not is_ancestor) as direct_mesh_rows,
        count(distinct mesh_id) filter (where not is_ancestor) as distinct_direct_terms,
        count(distinct mesh_id) filter (where is_ancestor) as distinct_ancestor_nodes
    from main_staging.stg_trial_condition_mesh
    group by indication_profile_id
)
select
    t.indication_profile_id,
    count(distinct t.nct_id) as total_trials,
    count(distinct t.nct_id) filter (where t.has_derived_mesh is true) as with_condition_browse_module,
    coalesce(max(m.direct_mesh_rows), 0) as direct_mesh_rows,
    coalesce(max(m.distinct_direct_terms), 0) as distinct_direct_terms,
    coalesce(max(m.distinct_ancestor_nodes), 0) as distinct_ancestor_nodes,
    count(distinct t.nct_id) filter (where tm.has_overlay) as overlay_mapped_trials,
    count(distinct t.nct_id) filter (where tm.has_fallback) as trials_with_any_fallback,
    count(distinct t.nct_id) filter (where tm.has_fallback and not coalesce(tm.has_overlay, false)) as fallback_only_trials
from main_marts.dim_trial t
left join trial_mapping tm
    on t.indication_profile_id = tm.indication_profile_id
    and t.nct_id = tm.nct_id
left join mesh_counts m
    on t.indication_profile_id = m.indication_profile_id
group by t.indication_profile_id
order by t.indication_profile_id;

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
