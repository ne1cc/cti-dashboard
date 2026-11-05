# Burden and population denominator sources (Issue #71)

Population-adjusted access needs a denominator. This page records which source
supplies it per condition family, what was confirmed (release, coverage,
licence) and what was not. The machine-readable form is the
`condition_burden_map` seed (`mesh_id` → source, `denominator_available`).

Rule enforced in the mart and the Access Rates page: where
`denominator_available` is false the page shows population-adjusted access
(per 100,000 residents) only and says **"no burden denominator available"**.
It never substitutes raw counts or a proxy for the missing burden.

## Population: Census ACS 5-year (`dim_population`)

| Item | Value |
|---|---|
| Release | ACS 2020-2024 5-year, table-based summary files |
| Tables | `B01001` (sex by age), `B03002` (Hispanic or Latino origin by race) |
| Geographies | state (all 50 + DC + PR); county for DE, MD, RI, VT; ZCTA for 21287, 19711, 02903, 05401 |
| Breakdowns | sex, age band (0-17, 18-44, 45-64, 65+), race/ethnicity (7 groups) |
| Source files | `https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/` (`acsdt5y2024-b01001.dat`, `acsdt5y2024-b03002.dat`, `Geos20245YR.txt`) |
| Licence | U.S. Government work, public domain; cite the U.S. Census Bureau |
| Margin of error | carried per row; derived sums use the root-sum-of-squares of the component MOEs |

Why files, not the API: the Census API now redirects keyless requests to a
key-signup page, so the ingest reads the keyless bulk files. Reproduce with
`src/ingest/acs_population.py` (a local-file parser, no network); its CLI
example is in the module docstring. Negative ACS sentinels become null. Only
the subset above is committed to keep the seed small (about 2,200 rows);
widening it is a rerun with more `--county-states` / `--zctas`.

The existing `state_population` seed (Vintage 2024 July 1 estimates) still
feeds `mart_condition_geography_trends`. The two sources differ by design
(ACS is a 5-year survey estimate; the Vintage series is a postcensal
estimate), so do not mix them in one chart.

## Burden by condition family

| Condition family | Best source | Geography | Demographic detail | Denominator in this PR | Limits |
|---|---|---|---|---|---|
| NSCLC | NCI/CDC State Cancer Profiles, lung and bronchus incidence, 2019-2023 (SEER + NPCR registries) | state | sex, race, age via the site's filters (not ingested) | **yes** (`scp_lung_state`) | Lung and bronchus, all histologies, not NSCLC-only (NSCLC is the large majority but the published state table does not split it). Average annual new cases, an incidence count. |
| Type 2 diabetes | CDC PLACES county estimates, diagnosed diabetes, crude prevalence | county, summed to state | none in the public table (adults 18+ only) | **yes** (`places_diabetes_county`) | All diagnosed diabetes, not type 2 only. Model-based small-area estimates from BRFSS 2023. Committed extract covers DE, MD, RI, VT; other states show "no burden denominator available". A prevalence count, not comparable with NSCLC incidence. |
| ADRD (Alzheimer disease, dementia) | **Fallback path taken**: PLACES cognitive disability | county | none | **no** (`places_cognition_county`) | Self-reported cognitive disability among adults. It is not diagnosed dementia and is labelled as such. Rows are kept for context only, with `denominator_available=false`. See "ADRD path" below. |
| Breast, colorectal, melanoma, ovarian, prostate cancer | State Cancer Profiles has state incidence for each site | state | sex, race, age filters | no (documented only) | Same export as lung; each needs its own `cancer=` site code and a seed. Not ingested here. |
| Heart failure | none identified | n/a | n/a | no | PLACES covers coronary heart disease and stroke, not heart failure. |
| Multiple sclerosis | none identified | n/a | n/a | no | No state or county prevalence source confirmed. |
| Parkinson disease | none identified | n/a | n/a | no | No state or county prevalence source confirmed. |
| Rheumatoid arthritis | none identified | n/a | n/a | no | PLACES has all-cause arthritis only. |

"None identified" means a search did not confirm a suitable open source, not
that none exists. Revisit when a family is prioritised.

Burden kinds differ: NSCLC is annual incident cases, diabetes is prevalent
adults. Compare per-case rates only within one condition. The mart is
built so a cross-condition comparison has to go through per-100,000 residents.

## Source details

### State Cancer Profiles (NSCLC)

- Export: Incidence Rate Report by State, "Lung & Bronchus (All Stages), 2019-2023", CSV output of the site query (`cancer=047`, all races, both sexes, all ages). The exact URL is the `SCP_URL` constant in `src/ingest/burden_extracts.py`.
- Vintage: the file's own notes cite the SEER November 2025 submission and the NPCR 2025 submission. Seed `period` is `2019-2023`; the map records "2025 data submission".
- Coverage: every state and PR (52 rows). `registry_program` records SEER, NPCR or SEER+NPCR per the file's footnotes. The US aggregate row is dropped, and the file states that the US figure excludes Puerto Rico.
- Estimated cases = the file's "average annual count". Suppressed cells would stay empty (none in this extract).
- Licence: the site is a NCI/CDC product. NCI content may be reused with credit to NCI; NPCR/CDC data are public domain. Attribution is shown on the page.

### CDC PLACES (type 2 diabetes, ADRD fallback)

- Dataset: data.cdc.gov `swc5-untb` (PLACES county data, 2025 release, BRFSS 2023). Fetched with a Socrata `$where` on `measureid in ('DIABETES','COGNITION') and datavaluetypeid='CrdPrv' and stateabbr in (...)`.
- Estimated cases per county = crude prevalence × `totalpop18plus` from the same record; the mart sums counties to state. The committed extract holds complete states only (a test checks that every ACS county of DE, MD, RI and VT is present), so a state sum is never partial.
- Licence: CDC open data, public domain; cite CDC PLACES.
- Limit: PLACES values are model-based and carry confidence intervals (stored in the seed); they are estimates, not counts.

### ADRD path

The decision was to use CMS Mapping Medicare Disparities (MMD) for ADRD, and
fall back to PLACES cognitive disability if MMD was unusable or its terms
unclear. **The fallback was taken.** MMD (`data.cms.gov/mapping-medicare-disparities`
and its tool at `data.cms.gov`) loads its data through a browser-only
download flow: the fetched page is a JavaScript shell with no static data file
or documented API endpoint, and no licence or reuse terms could be confirmed
from it. Nothing was scraped. The ADRD rows therefore use PLACES cognitive
disability, are labelled "self-reported cognitive disability, not diagnosed
dementia", and carry `denominator_available=false`, so ADRD shows
population-adjusted access only. If MMD later offers a stable, licensed
download, add it as a new `burden_source_id` and flip the flag.

## Regenerating the committed extracts

```
python -m src.ingest.acs_population --year 2024 --data-dir <dir with the 3 ACS files> \
    --county-states 10,24,44,50 --zctas 21287,19711,02903,05401 \
    --out dbt_clinical_trials/seeds/acs_population.csv
python -m src.ingest.burden_extracts places --states DE,MD,RI,VT \
    --out dbt_clinical_trials/seeds/burden_places_county.csv
python -m src.ingest.burden_extracts scp \
    --out dbt_clinical_trials/seeds/burden_scp_lung_state.csv
```

Tests never call these sources: they parse small committed excerpts of the
real files in `tests/fixtures/acs` and `tests/fixtures/burden`.
