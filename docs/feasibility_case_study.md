# National descriptive case study — Phase 3 Alzheimer's disease listings

**Proposed stakeholder:** Clinical-operations analyst conducting an initial landscape review.\
**Captured UTC date:** October 3, 2026.\
**Run:** `20261003T080013Z_c1b234b8`.\
**Evaluation UTC:** `2026-10-03T08:05:25.755288+00:00`.\
**Scope:** ADRD profile; existing `alzheimers_disease` condition group;
exactly `PHASE3`; overall `RECRUITING`; all 50 states.\
**Rules:** `competition-audit-v2`; active rule hashes and source-code commit
`aff62f7` are preserved in the evidence package.

## Stakeholder memo

**Question.** Describe the registry-reported recruiting-study landscape across
all 50 U.S. states. Identify patterns and data gaps that warrant further
investigation without inferring recruitment prospects or recommending states/sites.

**Observed landscape.** Of 35 captured studies matching the condition/phase rules
and reported overall status `RECRUITING`, 18 list at least one usable location
inside the 50-state scope. That is 18/35 (51.43%) national geographic coverage
under these rules. The remaining 17 report locations only under non-U.S. country
labels and are excluded by the existing U.S.-geography rules. This proportion
therefore reflects geographic scope as well as data usability; it is not an
estimate of missing U.S. studies or sites.

Thirty-nine states have at least one qualifying study. Eleven have an observed
zero. The largest observed counts are California (15), Florida (14), Texas (13)
and New York (12), within this captured dataset. State rows sum to 221 study–state
memberships, while the national distinct-study union is 18. Shared multi-state
studies account for that overlap; summing state rows would inflate the national total.

**Data caveats.** No recruiting study has undetermined state membership under the
existing rules in this cohort. Two of the 18 nationally contributing studies have
public posted-update ages greater than 180 days. They remain included: this is a
project-defined review warning, not a compliance rule or proof of inaccurate data.
An overall recruiting status does not verify recruiting activity at each listed
location. Facility names and location rows do not resolve unique real-world sites.

**Follow-up questions.** Which listed studies have sufficiently comparable
protocols and participant populations to merit closer review? Are location/status
listings current enough for the proposed use? What separate evidence is available
about patient availability and relevant site capabilities? These questions require
further evidence; the state counts do not answer them.

**Validation and benefit status.** The calculations below reconcile numerically
to every audit and an independent staging SQL calculation. Raw-record inspection
is a separate documented sample. The proposed stakeholder workflow and any
operational or financial benefits have not been evaluated.

## Complete 50-state results

Rows are ordered by state code, not by count or an asserted feasibility ranking.
Every share below is the state's distinct qualifying study count divided by the
same 35-study captured recruiting cohort. It describes cohort representation in
that state's recorded locations, not completeness of all real-world state activity.
The [CSV](evidence/feasibility_case_study_results.csv) includes the denominator in
every row and contains the separate national and additional-jurisdiction rows.

| Jurisdiction | Distinct studies | Cohort share (studies / 35) | Undetermined recruiting studies | Older-update contributors |
|---|---:|---:|---:|---:|
| Alaska (AK) | 0 | 0.00% | 0 | 0 |
| Alabama (AL) | 3 | 8.57% | 0 | 0 |
| Arkansas (AR) | 0 | 0.00% | 0 | 0 |
| Arizona (AZ) | 11 | 31.43% | 0 | 0 |
| California (CA) | 15 | 42.86% | 0 | 1 |
| Colorado (CO) | 6 | 17.14% | 0 | 0 |
| Connecticut (CT) | 5 | 14.29% | 0 | 0 |
| Delaware (DE) | 0 | 0.00% | 0 | 0 |
| Florida (FL) | 14 | 40.00% | 0 | 0 |
| Georgia (GA) | 8 | 22.86% | 0 | 0 |
| Hawaii (HI) | 1 | 2.86% | 0 | 0 |
| Iowa (IA) | 1 | 2.86% | 0 | 0 |
| Idaho (ID) | 1 | 2.86% | 0 | 0 |
| Illinois (IL) | 8 | 22.86% | 0 | 1 |
| Indiana (IN) | 2 | 5.71% | 0 | 0 |
| Kansas (KS) | 0 | 0.00% | 0 | 0 |
| Kentucky (KY) | 0 | 0.00% | 0 | 0 |
| Louisiana (LA) | 5 | 14.29% | 0 | 0 |
| Massachusetts (MA) | 10 | 28.57% | 0 | 0 |
| Maryland (MD) | 2 | 5.71% | 0 | 0 |
| Maine (ME) | 0 | 0.00% | 0 | 0 |
| Michigan (MI) | 9 | 25.71% | 0 | 0 |
| Minnesota (MN) | 3 | 8.57% | 0 | 0 |
| Missouri (MO) | 6 | 17.14% | 0 | 0 |
| Mississippi (MS) | 6 | 17.14% | 0 | 0 |
| Montana (MT) | 0 | 0.00% | 0 | 0 |
| North Carolina (NC) | 8 | 22.86% | 0 | 0 |
| North Dakota (ND) | 0 | 0.00% | 0 | 0 |
| Nebraska (NE) | 1 | 2.86% | 0 | 0 |
| New Hampshire (NH) | 1 | 2.86% | 0 | 0 |
| New Jersey (NJ) | 9 | 25.71% | 0 | 0 |
| New Mexico (NM) | 1 | 2.86% | 0 | 0 |
| Nevada (NV) | 5 | 14.29% | 0 | 0 |
| New York (NY) | 12 | 34.29% | 0 | 0 |
| Ohio (OH) | 11 | 31.43% | 0 | 0 |
| Oklahoma (OK) | 5 | 14.29% | 0 | 0 |
| Oregon (OR) | 2 | 5.71% | 0 | 0 |
| Pennsylvania (PA) | 6 | 17.14% | 0 | 0 |
| Rhode Island (RI) | 4 | 11.43% | 0 | 0 |
| South Carolina (SC) | 5 | 14.29% | 0 | 0 |
| South Dakota (SD) | 0 | 0.00% | 0 | 0 |
| Tennessee (TN) | 5 | 14.29% | 0 | 0 |
| Texas (TX) | 13 | 37.14% | 0 | 0 |
| Utah (UT) | 3 | 8.57% | 0 | 0 |
| Virginia (VA) | 3 | 8.57% | 0 | 0 |
| Vermont (VT) | 2 | 5.71% | 0 | 0 |
| Washington (WA) | 7 | 20.00% | 0 | 0 |
| Wisconsin (WI) | 2 | 5.71% | 0 | 0 |
| West Virginia (WV) | 0 | 0.00% | 0 | 0 |
| Wyoming (WY) | 0 | 0.00% | 0 | 0 |

An observed zero means no qualifying recorded match in this captured dataset;
it does not establish absence of studies, sites or recruitment operations.
Undetermined counts remain visible even when zero. If the captured dataset or
required audit rows are unavailable, the builder fails rather than publishing
blank cells or zero counts. With an empty recruiting cohort, shares are undefined.

## Additional jurisdictions — excluded from the 50-state national total

D.C. and the five configured territories are shown separately. The existing rule
requires a reported U.S.-country alias plus a usable state code. Territory locations
reported with a different country label may be excluded; these rows are not a
comprehensive territorial screen. Territory country/geography matching has not
been broadened for this case study.

| Jurisdiction | Distinct studies | Cohort share (studies / 35) | Undetermined recruiting studies | Older-update contributors |
|---|---:|---:|---:|---:|
| District of Columbia (DC) | 1 | 2.86% | 0 | 0 |
| Puerto Rico (PR) | 0 | 0.00% | 0 | 0 |
| Guam (GU) | 0 | 0.00% | 0 | 0 |
| U.S. Virgin Islands (VI) | 0 | 0.00% | 0 | 0 |
| American Samoa (AS) | 0 | 0.00% | 0 | 0 |
| Northern Mariana Islands (MP) | 0 | 0.00% | 0 | 0 |

## Calculation and evidence appendix

**Dataset selection.** The existing competition loader chooses daily study
observations at the latest competition-mart date for `adrd`. The builder freezes
that selection once for all 57 audits. For this case there is one complete local
run; all selected observations belong to that run. The API capture spans successive
pages, rather than a guaranteed single instant of registry state.

The source run retrieved 3,056 records across 31 pages, matching the API-reported
query total, with zero quarantined records. It started at
`2026-10-03T08:00:13.767310Z` and ended at `2026-10-03T08:00:28.791842Z`.
Page receipt times range from `2026-10-03T08:00:14.579597Z` to
`2026-10-03T08:00:28.787925Z`. These are pipeline clocks, separate from the
public source-update and verification dates retained in every study audit.

The existing profile uses the `Alzheimer Disease` query, interventional restriction
and its configured status list. Acquisition filters are unchanged. Downstream
condition mapping uses `config/condition_taxonomy.yml`. The exact `PHASE3` filter
excludes combined normalized phase strings such as `PHASE2/PHASE3`. The capture
is neither a census of every registry nor a measure of all real-world studies.

**Grain, status and overlap.** A qualifying NCT ID counts once per state if at
least one original location meets the existing reported U.S.-country and normalized
state rules. Missing facility names do not invalidate usable state geography.
Overall status must be exactly `RECRUITING`; location status is visible but does
not gate these counts. One study may count in several state rows. The national
count independently selects the explicit 50-state list and deduplicates NCT IDs.

**Denominators.** The helper retains a broader condition/phase cohort of 274 studies
before status/geography exclusions. Its national audit reconciles 18 contributors
+ 239 not-confirmed-recruiting exclusions + 17 unsupported-geography exclusions
= 274. The headline recruiting-cohort denominator is 35, not 274. National
geographic coverage is the 50-state contributor union / 35. Each state share is
its contributor set / 35; it is not an estimate of local capture completeness.
The package preserves the helper's original `coverage` and `recruiting_coverage`
fields without altering their definitions.

**Unknown geography.** A recorded state match takes precedence. Otherwise, absent
locations, absent country or unresolved U.S. geography produce undetermined
membership; known nonmatching locations are outside the selected region. The
headline unknown count considers recruiting studies only. The broader audits
also retain missing geography for excluded non-recruiting records.

**Enrollment.** Trial-wide estimated targets, reported actual enrollment, missing
counts and unknown types remain separate in each original audit. They are omitted
from the state table. Repeated study detail across state audits must not be summed
as state enrollment or apportioned to sites.

**Evidence format.** The [XZ-compressed JSON Lines package](evidence/feasibility_case_study_audit.jsonl.xz)
contains a manifest line and 57 audit lines: a national audit, 50 states and six
additional jurisdictions. Each audit line has `jurisdiction` and an `audit` object
with the unchanged existing export structure. All audits share the evaluation time,
observation selection and rule configuration. Compression removes repeated detail;
read the file line by line to avoid loading all repeated arrays at once.
The expanded repeated detail is approximately 1.1 GB; the CSV and memo provide
the compact results, while individual audit lines support drill-through.

**Numerical verification.** All 57 contributor sets, counts, denominators, shares,
exclusion reason counts, unresolved counts and older-update contributor counts were
compared against independent SQL starting at staging tables. Contributor union
matches the separately calculated national total. Every displayed table value
comes from the reconciled CSV. The captured warehouse passed its dbt data checks on 2026-10-03.

**Raw-record inspection.** Select the alphabetically first NCT ID in each of nine
available categories: national contributor, multi-state contributor, recruiting
without a 50-state match, non-recruiting exclusion, missing geography, unsupported
geography, missing facility, older posted update, and month-precision verification.
Categories can reuse the same study. Checks resolve page/ordinal lineage and verify
identity, canonical content hash, overall status, exact phase, condition mapping,
source dates, location count and original facility/city/state/country/status fields.
The manifest records selected IDs, categories and outcomes. This sample is not
full source validation and does not establish scientific reliability.

Nine category checks cover seven distinct studies; all passed:

| Sample category | Captured NCT ID |
|---|---|
| national contributor | [NCT03860857](https://clinicaltrials.gov/study/NCT03860857) |
| multi state contributor | [NCT05511363](https://clinicaltrials.gov/study/NCT05511363) |
| recruiting without 50 state match | [NCT04516057](https://clinicaltrials.gov/study/NCT04516057) |
| non recruiting exclusion | [NCT00000171](https://clinicaltrials.gov/study/NCT00000171) |
| missing geography | [NCT00034762](https://clinicaltrials.gov/study/NCT00034762) |
| unsupported geography | [NCT00423085](https://clinicaltrials.gov/study/NCT00423085) |
| missing facility | [NCT00056524](https://clinicaltrials.gov/study/NCT00056524) |
| older posted update | [NCT00000171](https://clinicaltrials.gov/study/NCT00000171) |
| month precision verification | [NCT00000171](https://clinicaltrials.gov/study/NCT00000171) |

## Reproduce and inspect

To avoid conflating read-only evidence inspection with pipeline execution, three
distinct tasks are separated below:
1. **Inspect committed evidence** (offline, read-only, Python standard library).
2. **Recalculate from an authorized retained run** (requires retained bronze/source data, warehouse build, and matching historical code/config).
3. **Acquire a new capture** (explicitly new work against the current registry; not an October 3 reproduction).

### Reviewer handoff checklist

Before conducting or reviewing an inspection session, verify each element of the frozen package:

- [ ] **Stakeholder question & memo:** Bounded descriptive review of Phase 3 Alzheimer's disease recruiting listings across 50 U.S. states; gaps identified without inferring recruitment prospects or recommending sites.
- [ ] **Captured run & snapshot:** `snapshot_date=2026-10-03`, `run_id=20261003T080013Z_c1b234b8`, evaluation UTC `2026-10-03T08:05:25.755288+00:00`.
- [ ] **Source commit & rule hashes:** Original commit `aff62f7caea2bd08c11a28ec90085e2abe7444f5`, rule version `competition-audit-v2`. Rule SHA-256 digests are recorded in audit metadata (`config/geography_rules.yml`, `config/condition_taxonomy.yml`, historical score weights path config/score_weights.yml, `dashboard/components/audit.py`, and dbt models; rule scope: *"Active configuration; historical configuration identity is not recorded."*). Current rule hashes are not historical configuration archives (checkout `42da3c5` contains matching rule bytes and the builder script).
- [ ] **Exact cohort & denominators:** ADRD profile, `alzheimers_disease` condition group, exactly `PHASE3` (excludes combined strings like `PHASE2/PHASE3`), overall `RECRUITING`.
  - **Recruiting denominator:** Exactly 35 captured recruiting studies.
  - **Broader eligible cohort:** 274 total condition/phase studies in the retained audit cohort (18 national contributors + 239 not-confirmed-recruiting exclusions + 17 unsupported-geography exclusions = 274).
  - **Study–jurisdiction memberships:** 50 states sum to 221 study–state memberships.
  - **National distinct union:** 18 unique studies across the 50 states (18/35 = 51.43% recruiting coverage).
- [ ] **Committed CSV results:** `docs/evidence/feasibility_case_study_results.csv` has 57 rows (50 states, 1 national union, and 6 supplementary jurisdictions: DC, PR, GU, VI, AS, MP). Every row records `eligible_recruiting_cohort = 35`.
- [ ] **Audit package streaming:** `docs/evidence/feasibility_case_study_audit.jsonl.xz` contains 1 manifest line and 57 audit lines. Expanded repeated arrays total ~1.1 GB; stream line by line using standard `lzma` without full extraction.
- [ ] **Boundary & non-equivalence notes:**
  - *Recruiting unknowns vs. wider-status unknowns:* Recruiting studies have 0 unresolved state memberships in this cohort. Broader audits retain missing geography for excluded non-recruiting studies (36 in the national cohort).
  - *D.C. and territories:* DC and the five territories are tracked separately and excluded from the 50-state national union.
  - *Generic view differences:* The generic monthly Geography Trends view (which sums monthly jurisdiction memberships without an exact phase filter, across all reporting jurisdictions) and token-inclusive API phase filters do *not* reproduce this exact cohort.
  - *Empty denominator rule:* If an evaluated cohort has 0 recruiting studies, the cohort share is an **undefined share** (division by zero), not 0% completeness.
- [ ] **Human validation status:** A second reviewer can stream the audit, select a state audit (e.g. California `CA`), identify its contributing NCT IDs and source registry links (reading saved `registry_url` from each study record, e.g. `https://clinicaltrials.gov/study/<NCT_ID>`), note at least one data caveat (e.g. overall status does not verify site-level recruitment, or facility names do not resolve unique entities), and formulate a qualified next investigation (e.g. protocol comparability, local site capability). Human evaluation and operational benefits remain **NOT VERIFIED** until an actual live session.

---

### Task 1: Inspect committed evidence (offline, standard library only)

This task requires only Python's standard library (`csv`, `json`, `lzma`, `pathlib`) and the committed repository files. It does **not** perform network requests, install dependencies, import application packages, create a warehouse, extract the full 1.1 GB uncompressed JSONL, or write any files.

From the repository root:

```bash
python3 -I - <<'PY'
import csv
import json
import lzma
from pathlib import Path

root = Path("docs/evidence")
csv_path = root / "feasibility_case_study_results.csv"
xz_path = root / "feasibility_case_study_audit.jsonl.xz"

# Missing or corrupt evidence raises an immediate exception; no zeros or blanks are fabricated.
if not csv_path.exists() or not xz_path.exists():
    raise FileNotFoundError("Committed evidence files are unavailable.")

# 1. Inspect CSV summary results
with csv_path.open() as f:
    rows = list(csv.DictReader(f))

states = [r for r in rows if r["jurisdiction_type"] == "state"]
national = next(r for r in rows if r["jurisdiction"] == "national_50_states")
memberships = sum(int(r["contributing_studies"]) for r in states)
national_contributors = int(national["contributing_studies"])
recruiting_denominator = int(national["eligible_recruiting_cohort"])

assert len(rows) == 57
assert len(states) == 50
assert memberships == 221
assert national_contributors == 18
assert recruiting_denominator == 35

# 2. Stream audit package line-by-line (manifest, national audit, and selected state)
with lzma.open(xz_path, "rt") as f:
    manifest = json.loads(next(f))
    national_audit = json.loads(next(f))

    ca_audit = None
    for line in f:
        record = json.loads(line)
        if record.get("jurisdiction") == "CA":
            ca_audit = record
            break

if ca_audit is None:
    raise LookupError("CA audit record unavailable in evidence package")

audit_summary = national_audit["audit"]["summary"]
audit_meta = national_audit["audit"]["metadata"]

print(f"Snapshot Date: {manifest['snapshot_date']}")
print(f"Run ID: {audit_meta['snapshot_ids'][0]}")
print(f"Source Commit: {manifest['source_code_commit']}")
print(f"Cohort: {audit_meta['profile_id']} / {audit_meta['filters']['conditions'][0]} / {audit_meta['filters']['phases'][0]}")
print(f"Rows: {len(rows)} (50 states, 1 national union, 6 additional jurisdictions)")
print(f"Study-State Memberships: {memberships}")
print(f"National Contributors: {national_contributors}")
print(f"Recruiting Denominator: {recruiting_denominator}")
print(f"Broader Eligible Studies: {audit_summary['eligible_studies']}")
print(f"National Exclusions: {audit_summary['exclusions']}")

ca_contributors = ca_audit["audit"]["contributors"]
sample_study = next((s for s in ca_audit["audit"]["studies"] if s.get("nct_id") == ca_contributors[0]), {})
sample_url = sample_study.get("registry_url") or f"https://clinicaltrials.gov/study/{ca_contributors[0]}"
print(f"CA Contributing Studies ({len(ca_contributors)}): {ca_contributors}")
print(f"Sample Registry Link: {sample_url}")
PY
```

**Expected output:**
```text
Snapshot Date: 2026-10-03
Run ID: 20261003T080013Z_c1b234b8
Source Commit: aff62f7caea2bd08c11a28ec90085e2abe7444f5
Cohort: adrd / alzheimers_disease / PHASE3
Rows: 57 (50 states, 1 national union, 6 additional jurisdictions)
Study-State Memberships: 221
National Contributors: 18
Recruiting Denominator: 35
Broader Eligible Studies: 274
National Exclusions: {'not_confirmed_recruiting': 239, 'unsupported_geography': 17}
CA Contributing Studies (15): ['NCT03860857', 'NCT05511363', 'NCT05641688', 'NCT05980949', 'NCT06126224', 'NCT06585787', 'NCT06937229', 'NCT06947941', 'NCT06976203', 'NCT06976216', 'NCT07011732', 'NCT07011745', 'NCT07177352', 'NCT07602582', 'NCT07717411']
Sample Registry Link: https://clinicaltrials.gov/study/NCT03860857
```

**Failure handling:**
- Any uncaught exception or non-zero exit means the evidence is unavailable; no synthetic or zeroed outputs are fabricated. Standard library readers, consistency assertions, and lookups raise `FileNotFoundError`, `lzma.LZMAError`, `json.JSONDecodeError`, `EOFError` (e.g. truncated archive), `StopIteration` (e.g. empty file or stream), `AssertionError` (e.g. summary count mismatch), or `LookupError` (e.g. missing jurisdiction record). Missing raw bronze pages or historical rule bytes prevents a source-level recalculation claim, but committed evidence files remain fully inspectable offline.
- When exercising missing-file or corruption handling, test only against a nonexistent path or a temporary synthetic file; **never delete or rename committed evidence files**.

---

### Task 2: Recalculate from an authorized retained run

Source-level recalculation is **not** achieved by reading saved artifacts. It requires separate authorization, access to the retained bronze snapshot, the exact matching source code and configuration, and a built local DuckDB warehouse.

**Permission & data boundaries:**
- `docs/evidence/build_feasibility_case_study.py` is **not** a generally safe disposable-data command: its warehouse path is resolved from the repository root (`ROOT / "data/warehouse/clinical_trials.duckdb"`) and execution overwrites both `docs/evidence/` files (`feasibility_case_study_audit.jsonl.xz` and `feasibility_case_study_results.csv`) in place. Do not run it in the review checkout; require a separate disposable clone or worktree. Compare regenerated files against the committed SHA-256 digests rather than committing them.
- Active rule hashes in current code are not historical configuration storage. If taxonomy, historical score weights (path config/score_weights.yml in metadata), geography mappings, or dbt SQL models have changed since commit `aff62f7caea2bd08c11a28ec90085e2abe7444f5`, re-running the builder may drift from the frozen evidence. Commit `aff62f7caea2bd08c11a28ec90085e2abe7444f5` predates the builder itself; checkout `42da3c5` contains matching rule/config/model bytes and adds the builder script.
- If raw bronze pages have been pruned under local retention policies, raw-record lineage references will no longer resolve to original payloads.

When authorized, in a separate disposable clone or worktree with the retained run in the local DuckDB warehouse and checkout `42da3c5`:

```bash
uv run python docs/evidence/build_feasibility_case_study.py \
  --snapshot-date 2026-10-03 \
  --evaluation-time 2026-10-03T08:05:25.755288+00:00
```

---

### Task 3: Acquire a new capture (new work)

Pulling current data from ClinicalTrials.gov is **explicitly new work**, not a reproduction of the October 3, 2026 values. Clinical trials advance, locations change, and registries update continuously.

**Permission & operational boundaries:**
- Ingestion requires outbound network access to the ClinicalTrials.gov API, local storage writes, and pipeline dependency installation. Ingestion must **never** be placed in the read-only evidence inspection path.
- A new pipeline run must **not** overwrite the frozen historical artifacts in `docs/evidence/`.
- If a new capture or filtered cohort contains zero recruiting studies, the recruiting denominator is 0; jurisdiction cohort shares must be reported as **undefined shares** (division by zero), not 0% completeness.

To run an authorized new capture and transform into a local warehouse (new run added to repository-root `data/`):

```bash
uv sync --all-groups --frozen
uv run python -m src.cli init-data-dirs
uv run python -m src.cli ingest --profile adrd
uv run python -m src.cli transform --profile adrd
make dbt-deps
make dbt-run
make dbt-test
```

See the [project brief](project_brief.md), [metric definitions](metric_definitions.md)
and [interpretation guardrails](clinical_interpretation_guardrails.md).

Registry-derived signals support preliminary feasibility review. They do not measure
site-level recruitment performance or establish scientific validity. Counts reflect
captured public records and the displayed inclusion rules.
