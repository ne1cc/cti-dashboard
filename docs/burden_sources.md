# Burden and prevalence sources by condition family

Why this exists: a per-100,000-residents rate (`recruiting_trials_per_100k`) says how many
recruiting listings a state has for its size. It does not say whether that is enough for the
people who have the condition. A burden denominator (estimated cases) answers that, but only
where a credible state- or county-level source exists. This page records which source backs
which condition, what was confirmed, and what was not.

**Rule used everywhere** (`mart_condition_state_rates`, `dashboard/components/denominators.py`):
where a condition has no burden denominator, show the population-adjusted rate only and say
"no burden denominator available". A raw trial count is never presented as a stand-in for need.

## Denominators in the warehouse

| Denominator | Seed or model | Source | Vintage |
|---|---|---|---|
| Residents (state, county, ZCTA; age band, sex, race/ethnicity) | `dim_population` ← seed `acs_population_extract` | U.S. Census Bureau ACS tables B01001, B03002 | ACS 2020-2024 5-year |
| Estimated cases | `mart_condition_state_rates` ← seed `condition_burden_map` | per the table below | per the table below |

`dim_population` rows carry `table_id`, `vintage`, `source_name` and `source_url`. Within one
geography each dimension (`total`, `age_sex`, `race_ethnicity`) sums to the total; do not add
across dimensions. The extract holds every state, DC and Puerto Rico, all counties of DE, HI,
MA, NH, RI and VT, and six ZCTAs. Absence of any other county or ZCTA means "not loaded", not zero.
`state_population` (Vintage 2024 estimates) still backs the older geography mart and is unchanged.

## Condition families

`denominator_available` below is the flag in `condition_burden_map`. Only the first three
families have committed extracts. The others are documented, not implemented.

| Family | Best source known | Granularity | `denominator_available` | Status |
|---|---|---|---|---|
| Lung cancer (NSCLC, MeSH D002289) | SEER + CDC NPCR State Cancer Profiles, lung and bronchus incidence | state | true (proxy) | extract committed |
| Type 2 diabetes (D003924) | CDC PLACES county estimates, DIABETES | county | true (proxy) | extract committed (7 jurisdictions) |
| Alzheimer disease and dementia (D000544, D003704) | CMS Mapping Medicare Disparities (preferred), else PLACES COGNITION | county | false | PLACES fallback committed; labelled as not a dementia count |
| Breast, colorectal, melanoma, ovarian, prostate cancer | State Cancer Profiles, same product and extract method as lung with another `cancer=` code | state | false until extracted | not extracted; site codes not verified in this change |
| Heart failure | no state-level prevalence source confirmed (CDC PLACES has coronary heart disease, not heart failure) | n/a | false | no source confirmed |
| Multiple sclerosis | no public state-level prevalence source confirmed | n/a | false | no source confirmed |
| Parkinson disease | no public state-level prevalence source confirmed | n/a | false | no source confirmed |
| Rheumatoid arthritis | PLACES ARTHRITIS covers all arthritis, not rheumatoid | county | false | proxy too broad to use |

"Not confirmed" means no source was checked for release, coverage and licence in this change.
It does not mean none exists.

## Confirmed sources

### SEER + NPCR State Cancer Profiles (lung and bronchus incidence)

- Measure: average annual incident cases and age-adjusted rate per 100,000, all stages, all
  races, both sexes, all ages, 2019-2023 (SEER November 2025 submission, NPCR 2025 submission).
- Coverage: 50 states, DC and Puerto Rico. The page's US total excludes Puerto Rico; the extract
  keeps Puerto Rico as its own row and drops the US row.
- Acquisition: `uv run python -m src.ingest.burden_extracts seer --out dbt_clinical_trials/seeds/burden_seer_lung_state.csv`
  (fetches the State Cancer Profiles CSV export, `cancer=047`).
- Parser fails loudly if the page layout changes (`ValueError`), so a refresh cannot silently
  produce an empty seed.
- Limit: no NSCLC-only cut exists by state. Counts include small-cell lung cancer, so the NSCLC rate per case is understated. The caveat is stored in
  `condition_burden_map.caveat` and shown in `denominator_note`.
- Footnote markers on state names are stripped by the parser; read the source page's notes
  before quoting a single state's figure.
- Licence: not separately confirmed. The data are US government publications; the site asks for
  citation. Confirm reuse terms before redistributing outside this repository.

### CDC PLACES, county, 2025 release

- Measures: DIABETES (diagnosed diabetes among adults) and COGNITION (cognitive disability
  among adults), crude prevalence, BRFSS 2023 base year.
- Dataset: Socrata `swc5-untb` on data.cdc.gov. Licence field reads Public Domain; checked
  while preparing this change.
- Acquisition: `uv run python -m src.ingest.burden_extracts places --states MA RI NH VT DE HI DC --out dbt_clinical_trials/seeds/burden_places_county.csv`.
- Estimated cases per state = sum over counties of `prevalence_pct / 100 × population_18plus`.
  `burden_county_count` and `burden_county_estimate_count` in the mart show how many counties
  were listed and how many had an estimate.
- Limits: model-based small-area estimates, not counts; CDC suppresses some tiny counties and
  not every state has every county; the committed seed is a seven-jurisdiction subset to stay
  small, so other states have no PLACES denominator and the mart says so.

### Alzheimer disease and dementia: what was tried

1. Preferred source: CMS Mapping Medicare Disparities (share of Medicare fee-for-service
   enrollees with Alzheimer's disease or related dementia, by county). It publishes through an
   interactive tool with a download button and offers no documented API or stable file URL, so
   a reproducible, offline-checkable extract could not be built from it.
2. Fallback taken, as the issue allows: PLACES COGNITION. It is self-reported cognitive
   disability, not diagnosed dementia. It is therefore mapped with `denominator_available =
   false`; the mart shows the per-100k rate and "no burden denominator available". The same
   limits apply to MMD if adopted later: it covers Medicare fee-for-service enrollees only
   (not Medicare Advantage or uninsured people) and counts diagnosed cases only.

## How to add a condition

1. Pick the source, confirm release, coverage and licence, and record them in this page.
2. Add the small extract as a seed with a parser under `src/ingest/` that has an offline test.
3. Add a row to `condition_burden_map` (MeSH id, source, measure, unit, vintage, caveat). Set
   `denominator_available` true only if the measure counts the condition itself.
4. Add a CTE branch for the source in `mart_condition_state_rates`.
