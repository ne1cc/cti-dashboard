# Metric Definitions

Global rules:
- Trial counts are always `COUNT(DISTINCT nct_id)`.
- Scope authority: Ingestion and scope are authoritatively defined per indication profile (`config/profiles/adrd.yml` for default ADRD), capturing all 8 overall statuses: `RECRUITING`, `ACTIVE_NOT_RECRUITING`, `NOT_YET_RECRUITING`, `COMPLETED`, `TERMINATED`, `WITHDRAWN`, `SUSPENDED`, `ENROLLING_BY_INVITATION`.
- Downstream implications:
  - **Tracked trial totals**: Total trial counts (`dim_trial`, `trial_count`, warehouse trials) encompass the full 8-status landscape rather than only active/completed studies.
  - **Stopped studies**: Studies with premature termination statuses (`TERMINATED`, `WITHDRAWN`, `SUSPENDED`) are retained to support why-stopped and feasibility risk analyses.
  - **Similarity pool vs. recruiting competition**: The trial similarity candidate pool spans all 8 statuses for historical precedent matching, whereas active competition metrics (`recruiting_trial_count`, `new_recruiting_30d/90d`, `sponsor_hhi`, etc.) and Registry Segments filter specifically for confirmed currently-recruiting listings (`overall_status = 'RECRUITING'`).
- A **segment** is condition_group × state × phase (the shared grain from
  `int_condition_geography_activity`; only trials with ≥1 usable U.S.
  location appear).
- Only **complete snapshots** (`status = 'success'`) feed metrics.
- Every metric is a *registry-listing signal*. None measures patients,
  enrollment performance, or site capacity.

## Condition identity (Issue #52)
A trial's condition identity comes from the registry's MeSH browse module
(`silver_trial_condition_mesh`: direct terms plus the supplied ancestor
closure, study-level); the YAML taxonomies are optional overlays on top. The
`mapping_source` column says how a condition row's group was assigned:

| mapping_source | meaning |
|---|---|
| `mesh` | registry MeSH browse identity (bridge/dim mesh nodes) |
| `yaml_overlay` | a profile taxonomy rule matched the condition text |
| `text_fallback` | no taxonomy match (or no taxonomy); the profile's catch-all group, or `unmapped` when the profile has no taxonomy |

- `dim_condition` grain: profile × condition node — `group:<slug>` (taxonomy)
  or a MeSH id. MeSH nodes include ancestor-closure edges, so any tree node is
  selectable; `is_ancestor` is true only where every membership edge is a
  closure edge.
- `bridge_trial_condition` grain: profile × trial × node, with the same node
  namespace. Existing group-based segments read only the `group:` rows; count
  marts read `int_trial_condition_mapping` (which contains only taxonomy condition
  groups), and similarity excludes all MeSH rows (`mesh_id is null`) so closure
  adds browsing reach without multiplying counts or shifting similarity.
- `same_condition` in `mart_trial_similarity` = two trials share ≥1 taxonomy
  condition group (`mesh_id is null`); MeSH rows are excluded so candidate pair
  discovery, similarity scores, and factor weights are unchanged from the
  taxonomy-only definitions.
- Coverage/disagreement review: `docs/condition_mesh_coverage.md`
  (fixture-limited).

## Activity metrics (`mart_trial_activity`)
| Metric | Definition |
|---|---|
| trial_count | distinct trials listed in the segment/status at the snapshot |
| sponsor_count | distinct normalized lead sponsors |
| listed_site_count | sum of per-study/state distinct normalized facility+city pairs; see identity limits below |
| entered_recruiting_count | trials whose status became RECRUITING vs the previous project snapshot |
| left_recruiting_count | trials whose status left RECRUITING vs the previous snapshot |
| flagged_record_count | trials with `record_quality_flag != 'ok'` |

## Competition metrics (`mart_recruiting_competition`)
| Metric | Definition |
|---|---|
| recruiting_trial_count | distinct RECRUITING listings in the segment — a raw count, without population adjustment |
| observed_history_days | days of snapshot history observed as of the snapshot (earliest retained snapshot through the row's own date); 0 on a first snapshot |
| new_recruiting_30d / 90d | sum of entered_recruiting transitions observed at snapshot dates inside a 30/90-day window (`RANGE BETWEEN INTERVAL n DAYS PRECEDING`); **null unless observed history covers the window** (observed_history_days ≥ n, the earliest in-window snapshot predecessor predates the window boundary by at most 1 day — the remaining 1-day bound under exact weekly cadence — and every entrant trial's own predecessor snapshot date falls within that 1-day covered window tolerance, with the latest gap between snapshots at most n days) — a transition observed across an unobserved gap has unknown timing, so it cannot be counted as if observed within the window. Null means not enough history, never zero transitions |
| newly_registered_90d | recruiting trials with `study_first_post_date` ≥ snapshot date minus 90 days (inclusive lower bound, no upper bound) — a **registration count** from registry dates, not a recruiting transition; computable from a single snapshot |
| recruiting_count_90d_baseline | first recruiting-count value inside the 90-day window |
| recruiting_growth_90d | (recruiting count − baseline) / baseline; null unless the 90-day window is covered, the segment was observed at the window's earliest snapshot (guaranteeing baseline was captured at the window start), the baseline predates counted transitions (no transitions at the baseline snapshot itself, preventing manufactured 0.0), and baseline count is > 0 (safe_divide). Never manufactured 0.0 for newly appearing segments or unknown history |
| top_sponsor_share | max lead-sponsor share of segment recruiting trials |
| sponsor_hhi | Σ (lead-sponsor share)², 0..1; 1.0 = single sponsor |
| density_percentile | `percent_rank()` ordered by recruiting count within profile × snapshot date |
| competition_signal_band | percentile cuts: < 0.5 `low`, < 0.8 `moderate`, else `elevated` — **relative** cuts, not absolute judgments |

## Site metrics (`mart_site_overlap`)
| Metric | Definition |
|---|---|
| listed_trial_count / recruiting_trial_count | distinct trials listing the facility (name+city+state identity, best-effort) |
| phase_mix | distinct phases, `string_agg` with " \| " |
| repeated_site_participation_flag | recruiting_trial_count > 1. Neutral term by design — never "overloaded" |

Facilities with no listed name are excluded from this mart only (identity
required for overlap); they remain in silver and staging.

## Trend metrics (`mart_condition_geography_trends`)
Month grain (snapshot month). `recruiting_trial_count_3m_avg` and
`recruiting_growth_3m` use a 3-month `RANGE` window over the profile ×
condition_group × state series. `recruiting_growth_3m` is **null unless the
window's earliest observed `activity_month` sits at least three months back,
the baseline snapshot date sits at least 3 calendar months back
(`baseline_snapshot_date <= latest_snapshot_date - interval 3 month`), and
observed history spans at least 90 days**: on a shorter window the figure would be labelled
"3-month" while covering less than that, and on a single-month window the baseline
`first_value` is the row itself, so `(x − x) / x` would read 0.0 — "recruiting
is flat" — for a quantity that is genuinely unknown. Null means not enough
history; zero is reserved for a covered window with no change (pinned by
`dbt_clinical_trials/tests/assert_trends_growth_needs_full_window.sql`, with
the superseded two-month guard kept in
`assert_trends_growth_needs_two_months.sql`). `observed_history_days` is the
profile's as-of-month observed span (earliest snapshot through the latest
snapshot in or before the month) and travels with the growth figure. The mean
is the opposite case: a one-point mean is honest, so
`recruiting_trial_count_3m_avg` keeps its value even at a single month.
`newly_registered_in_month` counts trials whose registry first-post month
equals the snapshot month — registrations, not transitions.

| Metric | Definition |
|---|---|
| recruiting_trials_per_100k | `100000.0 * recruiting_trial_count / population` (rounded to 4 decimals); null when population is missing or nonpositive |
| population | Resident population from U.S. Census Bureau (Vintage 2024, July 1, 2024 estimates, `POPESTIMATE2024` from `NST-EST2024-ALLDATA.csv`); null for unmatched territories |
| population_source | `U.S. Census Bureau` when population is present, else null |
| population_vintage | `Vintage 2024` when population is present, else null |

Population normalization controls for state size so that large states (CA, TX, FL, NY) do not dominate maps solely due to resident count. **Interpretation guardrail:** neither raw listing counts nor population-adjusted rates measure clinical need, disease prevalence, or patient access. Unmatched jurisdictions (e.g. Guam, Virgin Islands) retain raw counts but carry null per-100k rates.

## Reliability metrics (`mart_data_reliability`)
| Metric | Definition |
|---|---|
| manifest_reconciled_flag | silver trial rows == manifest record_count |
| unique_nct_flag | silver rows == distinct NCT IDs |
| flagged_record_share | trials with quality flag ≠ ok / all trials |
| usable_location_share | locations with `usable_geography_flag` / all locations |
| coordinates_exact_count / coordinates_exact_share | locations with registry geoPoint latitude and longitude (exact coordinates); share over all locations |
| coordinates_fallback_eligible_count / coordinates_fallback_eligible_share | locations without geoPoint that carry the fields the offline Census Gazetteer fallback needs — a ZIP (first 5 digits) or a normalized city+state; share over all locations. Fallback-eligible rather than verified: the Gazetteer reference files are not part of the dbt build, so actual resolution (and the per-site `coordinate_approximate` flag) is decided at query time, making this an upper bound on approximate coordinates. Reported so the exact-vs-approximate coordinate mix behind distance search is visible |
| low_confidence_condition_share | taxonomy fallback mappings / all condition rows |

## Distance search (read API, Issue #63)
- A patient's `postal_code` (5 digits) resolves to coordinates from the offline
  Census Gazetteer ZCTA centroid file (`make gazetteer`); no geocoding API is
  called and no address is stored.
- "Available near me" counts only sites whose site-level `location_status` is
  `RECRUITING` or `NOT_YET_RECRUITING`; sites with other or missing statuses
  are inactive for the radius filter and never surface as the nearest site
  (the detail endpoint lists every site with its status).
- Site coordinates prefer the registry `geoPoint`; without one they fall back
  to the Gazetteer ZIP, then city+state centroid, and the response flags
  `coordinate_approximate: true`. Sites with no resolvable coordinates are
  excluded from distance results. Their exact-vs-approximate mix is the
  coordinate coverage reported above.
- Known limitation of the city fallback: when several Gazetteer places share
  the same normalized city+state name, the fallback resolves to one
  deterministic centroid (lowest latitude, then longitude) rather than the
  one closest to the user, and that centroid can be far from the actual site.
  It is flagged `coordinate_approximate`; the ZIP fallback is the primary
  approximate path.

## Registry Segments and Raw Feasibility Metrics (`mart_feasibility_priority_queue`)
Purpose: **provide transparent, sortable raw registry signals for human feasibility review.** Not a forecast.

Per ADR 0077, the composite priority score and categorical priority bands were removed to eliminate arbitrary weighting and ambiguous ranking direction. Users sort directly by the raw observable metrics relevant to their inquiry:

1. **Active Recruiting Trials (`active_recruiting_trials`, `recruiting_trial_count`):** Number of distinct trials actively recruiting in the segment (`COUNT(DISTINCT nct_id)`).
2. **Recent Growth (`new_recruiting_90d`):** sum of recruiting transitions inside the 90-day window; **null until the profile's observed history covers 90 days** (`observed_history_days` quantifies how short the history is). Shown as "not enough history" — unknown, never zero. `newly_registered_90d` is reported beside it as a separately-labelled registration count.
3. **Sponsor Concentration (`sponsor_hhi`):** Herfindahl-Hirschman Index (Σ s_i²) measuring market concentration across segment sponsors, bounded in [0, 1.0].
4. **Site Overlap Rate (`site_overlap_rate`, `site_overlap_share`):** Share of segment trials listing ≥1 facility that hosts multiple recruiting trials in this indication profile.
5. **Operational Completeness (`operational_completeness_share`):** 0.5 × segment record-quality-ok share + 0.5 × run-level usable-location share. The usable-location half is a run-level value from the profile's latest successful ingestion run, so it is constant across all segments of one profile. Reflects reporting and location usability; not scientific confidence.
6. `priority_explanation` is assembled from fixed component phrases — the same inputs always yield the same sentence. Every row carries an `interpretation_note`.

## Audit definitions and scope

**Data coverage and caveats** appears on Overview, Registry Segments, Competition
Landscape, Geography Trends, Site Overlap, Sponsor Landscape, and Data Reliability.
The source chain is unchanged page JSON → page receipt sidecar → silver study and
original-location rows → staging → successful-snapshot audit marts → selected
metric observations → filter decisions → contributing NCT IDs and JSON export.
`source_json_hash` is the canonical study JSON SHA-256; page reference plus
zero-based study ordinal resolves the captured record while bronze remains available.

The study audit grain is profile × immutable ingestion run ID (`snapshot_id`) ×
NCT ID. The location audit adds original `location_ordinal`; identical recorded
locations remain separate rows. Daily metric history selects the latest observation
per study/profile/date, so one date may have multiple contributing run IDs. All
successful runs remain in the audit marts; an audit uses the displayed metric's
actual selected observations, including selected history windows. Current study
counts follow `int_current_trial_status`; competition and facility audits use their
respective mart dates. Snapshot identity survives an empty contributor selection.

Sponsor filters match the displayed `lead_sponsor_name` exactly; HHI grouping separately
uses normalized lead-sponsor names. Filters record condition groups, phases, states,
displayed lead-sponsor names, and facility
triples (normalized name, city, state). Sidebar selections are retained separately
from drill-through refinements. A default segment audit covers sidebar scope before
segment-table row selections; it is not the sum of displayed overlapping segments.
Confirmed recruiting is exactly overall status `RECRUITING`; missing/unknown and
other statuses do not qualify. The all-status mode supports listed-trial and current
study counts. Location status is inspectable but does not gate state counts.

Geography uses reported U.S. country and normalized supported state under active
`config/geography_rules.yml`. City and coordinates are displayed, not radius inputs.
A missing facility name retains usable state geography. Membership is `inside` when
an original location matches the selected state; otherwise it is `undetermined` if
locations are absent, country is absent, or U.S. state geography is unresolved;
only known nonmatching geography is `outside`. Usable TX plus unresolved geography
under a CA filter is undetermined and does not contribute to CA. Missing required
geography and unsupported geography have distinct exclusions.

An explicitly empty facility restriction selects no facilities while retaining the
eligible study denominator, study decisions, exclusions and original recorded locations.
It is distinct from an unrestricted empty sidebar selection.

Eligible denominator = distinct captured studies after observation/profile,
condition, phase and sponsor scope, before status/geography/facility exclusions.
Coverage = distinct contributors / eligible studies. Recruiting coverage = distinct
confirmed-recruiting contributors / eligible confirmed-recruiting studies. Zero
denominators yield null. Region-undetermined studies remain in the denominator;
this does not claim that they belong to the selected state. For competition/history,
missing condition mappings remain inspectable when no condition filter removes
them, but do not contribute. Exclusion precedence per observation is status,
condition mapping, missing/unsupported geography, selected region, facility.
For a history union, an NCT ID contributing at any observation is counted once;
never-contributing IDs take their latest observation's reason. Contributors plus
mutually exclusive exclusions reconcile to eligible studies; flags can overlap.

## Three independent clocks and enrollment

| Clock | Calculation and precision |
|---|---|
| Pipeline age | evaluation UTC minus successful ingestion end UTC, in hours |
| Posted-update age | snapshot UTC date minus valid full-day public last-update-post date, in days |
| Verification age | snapshot month minus reported verification month, in months for day/month source dates |

Raw posted/verification text survives. Verification precision is day, month, year,
missing or unparseable; year-only verification has no month-age value. Missing or
unparseable posted dates stay unknown. Page retrieval UTC is separate from these
clocks and remains null for legacy pages without receipt metadata. The warning is
strictly **greater than 180 days**, a project-defined review threshold; warn and
retain, with no age exclusion. The pure computation accepts a threshold parameter;
the panel provides a nonnegative day control, default 180, and exports the selected
threshold/policy with evaluation time. Changing it changes warnings, not contributors.

Enrollment categories are estimated target, reported actual, missing count, and
present count with absent/unrecognized type (`unknown_type`). Counts and totals are
separate by category; missing totals are null. Enrollment totals use the latest
included observation per NCT ID; per-snapshot totals are separate. Enrollment is
never apportioned to facilities or summed again across repeated history observations.

## Identity and derived-value boundaries

Listed-site count sums counts of distinct normalized facility/city pairs per
study/state within a segment. Duplicate pairs collapse; null city coalesces to
empty text and missing normalized facility name contributes no pair. Across studies
the same facility may be counted repeatedly. Site Overlap instead groups normalized
name/city/state triples and counts distinct NCT IDs listing each triple. Neither
identity resolves a real-world facility, investigator, site capacity, or recruitment
performance. Segment counts overlap across states/conditions; dashboard study totals
use the distinct union of contributing NCT IDs.

Sponsor shares use each normalized lead-sponsor group's distinct recruiting-study
count / sum of those group counts in the segment. A null sponsor group contributes
to the share denominator, although `sponsor_count` excludes null names. HHI is the
sum of squared shares; top share is their maximum. Percentiles are profile/snapshot
relative with 0.5/0.8 band cuts. Entrant counts require a previous non-null,
non-recruiting status; 30/90-day windows include their lower boundary and are
emitted only when observed history covers the window (and the latest snapshot gap
does not itself span it); a short history shows null, never 0. `newly_registered_90d`
counts recruiting listings whose first-post date is ≥ snapshot minus 90 days —
registrations, not transitions, computable from a single captured date. Site
overlap share is distinct recruiting segment
trials listing any facility with >1 recruiting trial in the profile / distinct
recruiting segment trials. The operational completeness input (`operational_completeness_share`)
is 0.5 × segment record-quality-ok share + 0.5 × latest successful-run usable-location share;
missing shares coalesce to zero. The usable-location half is a run-level value, constant
across segments of one profile. It is not scientific confidence.

Monthly counts use distinct NCT IDs across captured observations in the month.
The three-calendar-month `RANGE` includes the current month and its three prior
month boundaries (up to four monthly rows); the mean averages available rows.
Growth = (current count − first available window count) / that baseline, null for
zero baseline or until the window's earliest observed month sits at least three
months back — "not enough history", never zero. The panel limits observation scope
to displayed months or this inclusive input window. Competition's 90-day input
selector identifies only true entry events and exports event date/current snapshot plus
predecessor status, snapshot and raw-record lineage, including predecessors before the
window. Continuing recruiters are not event contributors. The separate newly
registered (first posted) selector uses RECRUITING and source first-post date >=
snapshot date minus 90 days, with an inclusive lower bound and no upper bound; raw
and parsed first-post dates survive.
Eligible coverage and count-input contributors remain separate from event contributors.

The panel audits count inputs and identifies qualifying recruiting transition and newly registered contributing events, without
recomputing unrelated HHI or percentiles. Derived formulas, displayed values, source-rule
hashes and relevant windows are exported. These derived values cannot be inferred merely
from the union contributor count.

## Export and rule identity

The UTF-8 JSON download includes definition, `competition-audit-v2`, active
configuration/model SHA-256 identifiers, exact filters and sidebar scope, profile,
selected run IDs/dates, evaluation UTC, threshold, count status, contributing IDs,
study decisions and overlapping flags, original locations, raw references/ordinals,
content hashes, source clock precision, retrieval times, and enrollment categories.
Active hashes identify current taxonomy/geography/model rules; historical
configuration identity was not recorded. Raw page references can stop resolving
after configured bronze pruning. Export does not promise an indefinite archive.

Registry-derived signals support preliminary feasibility review. They do not measure site-level recruitment performance or establish scientific validity. Counts reflect captured public records and the displayed inclusion rules.
## Baseline demographics and coverage (`mart_baseline_demographics_coverage`, Issue #74)

Source: `resultsSection.baselineCharacteristicsModule` (posted results), extracted as
`silver_trial_baseline_demographics` — one row per (measure, class, category, group)
measurement, with labels kept exactly as sponsors reported them. The participant
head-count is parsed only when the measure is a participant count (`COUNT_OF_PARTICIPANTS`
or `NUMBER` with unit Participants); mean/median ages stay raw strings.
`group_denominator` resolves from the most specific scope the registry reports for
the group — class, then measure, then module — preferring the participant-denominated
`denoms` entry when a scope lists several units; `percent_of_group_denominator` derives
from that base in staging.

**Dimension classification** (staging, from the measure title, on word boundaries so
"Gender" is sex, "Mean Age" is age, and "GRACE score" is not race): `sex` (sex/gender),
`age`, `race`, `ethnicity`, `race_ethnicity_combined`, `race_ethnicity_not_collected`,
`other`.

**OMB mapping** (`dbt_clinical_trials/seeds/omb_race_ethnicity_mapping.csv`, versioned and PR-reviewed):
maps a normalized sponsor label to an OMB race/ethnicity category with
`mapping_confidence` (`high`/`medium`/`low`) and `manually_reviewed`. Unmapped labels stay
unmapped. Combined race/ethnicity tables are mapped per label only where the dimension is
explicit — a `Race:`/`Ethnicity:` label prefix, an exact `Race`/`Ethnicity` class title,
or a seed row scoped to one dimension. Dimension-neutral buckets ("Unknown or Not
Reported", "Not Stated", "Other") inside a combined table without such context keep
their reported label, stay unmapped, and set `is_ambiguous_combined_flag`.

**Coverage metrics** (per indication profile × condition group × completion year):

| Metric | Definition |
|---|---|
| trials_with_results_count | distinct COMPLETED trials with posted results in their profile's latest successful ingestion run (denominator) |
| reports_sex_count / share | baseline characteristics include a sex measure |
| reports_age_count / share | include an age measure |
| reports_race_count / share | ≥1 race row whose dimension resolved from explicit evidence |
| reports_ethnicity_count / share | ≥1 ethnicity row whose dimension resolved from explicit evidence |
| ambiguous_combined_trials_count / share | denominator trials carrying ≥1 ambiguous combined row |

Demographics count only from the same latest successful run as the trial row: a race
table that a sponsor later amended out of the results section stops counting
once the newer snapshot lands. `completion_year` is null when the registry
completion date is missing or unparseable; those trials stay on a null-year row
rather than being dropped. Coverage numbers are queryable wherever demographics
are consumed; a dashboard surface is out of scope for #74.
