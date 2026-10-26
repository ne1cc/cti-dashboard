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
comes from the reconciled CSV. The captured warehouse passed its dbt data checks
(measured 2026-10-03: 157 dbt tests passed).

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

From the repository root, with the approved auditability models available:

```bash
uv sync --all-groups --frozen
uv run python -m src.cli init-data-dirs
uv run python -m src.cli ingest --profile adrd
uv run python -m src.cli transform --profile adrd
make dbt-deps
make dbt-run
make dbt-test
uv run python docs/evidence/build_feasibility_case_study.py
```

A new registry pull produces a new captured dataset and may change results. To
reproduce this case from its retained source run, retain the run and corresponding
rule files and use the recorded time/date:

```bash
uv run python docs/evidence/build_feasibility_case_study.py \
  --snapshot-date 2026-10-03 \
  --evaluation-time 2026-10-03T08:05:25.755288+00:00
```

To inspect a state audit without loading the whole expanded package:

```python
import json
import lzma

with lzma.open("docs/evidence/feasibility_case_study_audit.jsonl.xz", "rt") as evidence:
    for line in evidence:
        record = json.loads(line)
        if record.get("jurisdiction") == "CA":
            print(record["audit"]["summary"])
            print(record["audit"]["contributors"])
            break
```

Raw pages and the warehouse remain local and are not committed. After configured
bronze pruning, raw references may stop resolving. Rule hashes alone do not archive
historical configuration bytes; retained source plus the corresponding code/config
is required for source-level reproduction. The package preserves captured audit
evidence for inspection, with its source-retention limits explicitly recorded.

See the [project brief](project_brief.md), [metric definitions](metric_definitions.md)
and [interpretation guardrails](clinical_interpretation_guardrails.md).

Registry-derived signals support preliminary feasibility review. They do not measure
site-level recruitment performance or establish scientific validity. Counts reflect
captured public records and the displayed inclusion rules.
