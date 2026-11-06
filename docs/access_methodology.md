# Trial access by geography: methodology and limits (Issue #72)

`mart_trial_access_by_geo` answers one question per MeSH condition node and county:
**is the county's centroid within 25, 50 or 100 straight-line miles of at least one open
site, and how many residents live in that county?** `mart_trial_access_by_stratum` sums
the same rows by rural-urban group and social-vulnerability quartile. Both are
registry-based signals, not a forecast of whether anyone can or will enrol.

## What "open site" means

A site counts only when **both** of these hold:

- the **site-level** `location_status` is `RECRUITING` or `NOT_YET_RECRUITING`, and
- the **trial-level** `overall_status` is the same pair and the trial is in the latest
  snapshot (`active_in_latest_snapshot_flag`).

This is the rule the distance search uses (`docs/metric_definitions.md`). A trial that is
recruiting overall can list a closed site, and a suspended trial can still carry a site
that says recruiting; both are excluded. A node includes its MeSH descendants through
`bridge_trial_condition`, so a parent node counts the sites of every child trial.
A "site" is one `facility + city + state` (normalized) per profile: the registry has no
site identifier, so two spellings of one hospital count twice and a hospital in two
trials counts once. Treat site counts as approximate.

## Where site coordinates come from

Precedence, per listing, then one coordinate per site:

1. registry `geoPoint` (`exact`);
2. Census Gazetteer ZCTA centroid for the 5-digit ZIP (`zip`, approximate);
3. Gazetteer place centroid for city + state (`city`, approximate);
4. none: the site is **excluded** from distances and counted in `sites_excluded`.

The Gazetteer files are the ones `make gazetteer` writes for the read API
(`CTI_GAZETTEER_DIR`, default `data/reference`). They are not committed. When absent the
dbt build still succeeds and every ZIP/city-only site is reported as excluded, so a
warehouse built without them understates coverage rather than failing.

## Coverage and confidence fields

Every county row of a node repeats: `sites_total`, `sites_exact`, `sites_zip_approx`,
`sites_city_approx`, `sites_excluded`, `exact_coordinate_share` (exact ÷ usable sites)
and `coverage_confidence`:

| Tier | Rule |
|---|---|
| `none` | no site has usable coordinates |
| `high` | exact share ≥ 0.90 and excluded share ≤ 0.05 |
| `medium` | exact share ≥ 0.70 and excluded share ≤ 0.15 |
| `low` | anything else |

`shortcut:` the thresholds are a judgement call, not calibrated against ground truth.
Revisit them once real nodes show where the distance error actually grows. The tier
describes site-location quality only; it says nothing about the population side.

## Distance: what it is and is not

- **Straight-line (great-circle) miles**, haversine with earth radius 3,958.8 mi, the
  same constant the API uses. It is **not** travel time. Water, mountains, missing roads
  and transit make real travel longer, most of all in rural counties and across
  state lines; 50 straight-line miles is not 50 minutes. The default path uses no routing
  engine. Travel-time isochrones (the issue's stretch goal) are **not implemented**.
- **Centroid error.** Each county is represented by its Census internal point, not by
  where residents live. A large or lopsided county can have most residents far from the
  centroid, so a county flagged "within 25 miles" may contain residents well beyond it
  and the reverse. County-level shares are population-weighted by whole counties; they
  are not person-level distances.
- `nearest_site_miles` is reported only up to 100 miles; beyond that it is null and all
  three `within_*` flags are false. A null therefore means "no open site within 100
  miles", not "unknown".
- ZIP and city fallbacks move a site by up to several miles (ZCTA) or tens of miles
  (large-city centroid). The exact/approximate mix is reported above so users can judge
  that.

## Stratification

| Stratum | Source | Rule |
|---|---|---|
| `ruca_group` | USDA ERS RUCA 2020 (census-tract primary codes, ERS update 2025-12-09) | tract codes 1-3 → metropolitan, 4-6 → micropolitan, 7-9 → small_town, 10 → rural, 99 → not_coded; a county takes the group holding the most residents (ties go to the more urban group). `ruca_group_pop_share` gives how dominant that group is, so a mixed county is visible |
| `svi_quartile` | CDC/ATSDR SVI 2022, overall percentile rank `RPL_THEMES`, county file | Q1 least to Q4 most vulnerable (`rpl` < 0.25, < 0.50, < 0.75, ≥ 0.75). Ranks are national among counties; a county with no valid rank (negative sentinel) falls in `unclassified` |
| population | Census ACS 2020-2024 5-year (`dim_population`) | total residents per county; see `docs/burden_sources.md` |
| centroids | Census Gazetteer 2024 counties | public domain |

Licensing: the Census files are U.S. Government works in the public domain. We found **no
explicit licence statement** on the CDC/ATSDR SVI or USDA ERS RUCA download pages. Both
are U.S. federal agency products published for public use, and we cite them as the
source, but we have not confirmed a formal licence; confirm before redistributing the
seed outside this repository. The seed (`county_access_context`, rebuilt by
`src/ingest/access_context.py`, offline against downloaded files) records the release of
each source per row. RUCA is the dominant-group county rollup described above; the
ZIP-level RUCA file was not used.

## Scope and grain

- **County grain only.** The issue also allows ZCTA centroids; that is deferred because
  the committed ACS population covers counties for DE, MD, RI and VT only, so the seed
  has 46 counties. Widening is a rerun of `src/ingest/acs_population.py` and
  `src/ingest/access_context.py` with more `--states`, with no model change.
  Connecticut would need care: the ACS 2020-2024 files use planning regions while the
  2024 Gazetteer counties are the older counties.
- `/v1/geo/access` in `docs/api/openapi.yaml` is specified but not implemented in
  `src/api/app.py`; this change adds the marts only, so the contract is untouched.
- Every consumer of these marts must link to this page: the model descriptions in
  `dbt_clinical_trials/models/marts/_marts.yml` do, and each stratum row carries it in
  `access_basis`.

## Performance

Distances are computed once per distinct site against every county (a latitude and
longitude bounding box discards far pairs before the haversine), then joined to nodes, so
cost scales with sites × nearby counties, not nodes × counties × sites. A DuckDB
benchmark of that step used all 3,109 conterminous-US county centroids from the 2024
Gazetteer and synthetic sites sampled from real ZCTA centroids:

| Distinct sites | Nodes | Pairs ≤ 100 mi | Distances | Node rollup |
|---|---|---|---|---|
| 2,000 | 300 | 84,275 | 0.30 s | 0.08 s |
| 10,000 | 600 | 427,671 | 1.29 s | 0.11 s |

That is the distance and rollup step on one laptop, not a full `dbt build`; it is well
inside the "few minutes for one condition" target. The committed seed holds 46 counties,
so the real build is smaller still until the seed is widened.

## Validation

`tests/test_trial_access_geo.py` builds a warehouse over a synthetic fixture and checks
the hand-picked Baltimore metro: Johns Hopkins (exact coordinates) lies about a mile from
the Baltimore city centroid and every ring flag is true; the Delaware counties sit inside
100 but outside 50 miles of Baltimore, Allegany County has no site within 100 miles,
a ZIP-resolved Vermont site and a city-resolved Rhode Island site reach their counties, and
a completed site and a suspended trial's site are ignored. Ring counts and nearest
distances for every county are cross-checked against an independent Python haversine.
