# Metric Definitions

Global rules:
- Trial counts are always `COUNT(DISTINCT nct_id)`.
- A **segment** is condition_group × state × phase (the shared grain from
  `int_condition_geography_activity`; only trials with ≥1 usable U.S.
  location appear).
- Only **complete snapshots** (`status = 'success'`) feed metrics.
- Every metric is a *registry-listing signal*. None measures patients,
  enrollment performance, or site capacity.

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
| new_recruiting_30d / 90d | sum of entered_recruiting transitions in a 30/90-day window over snapshot dates (`RANGE BETWEEN INTERVAL n DAYS PRECEDING`); 0 until multi-snapshot history accrues |
| newly_posted_90d_proxy | recruiting trials with `study_first_post_date` ≥ snapshot date minus 90 days (no upper-bound check) — labeled proxy, not a transition |
| recruiting_count_90d_baseline | first recruiting-count value inside the 90-day window |
| recruiting_growth_90d | (recruiting count − baseline) / baseline; null when baseline is 0 |
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
condition_group × state series. `recruiting_growth_3m` is **null unless that
window spans at least two distinct `activity_month`s**: on a single-month
window the baseline `first_value` is the row itself, so `(x − x) / x` would read
0.0 — "recruiting is flat" — for a quantity that is genuinely unknown. The model
nulls it instead (pinned by
`dbt_clinical_trials/tests/assert_trends_growth_needs_two_months.sql`). The
mean is the opposite case: a one-point mean is honest, so
`recruiting_trial_count_3m_avg` keeps its value even at a single month.

## Reliability metrics (`mart_data_reliability`)
| Metric | Definition |
|---|---|
| manifest_reconciled_flag | silver trial rows == manifest record_count |
| unique_nct_flag | silver rows == distinct NCT IDs |
| flagged_record_share | trials with quality flag ≠ ok / all trials |
| usable_location_share | locations with `usable_geography_flag` / all locations |
| low_confidence_condition_share | taxonomy fallback mappings / all condition rows |

## Feasibility Review Priority Score (`mart_feasibility_priority_queue`)
Purpose: **rank segments for human feasibility review.** Not a forecast.

1. Component inputs at the latest complete snapshot:
   - recruiting count: `recruiting_trial_count`
   - recent growth: `new_recruiting_90d` if multi-snapshot history exists,
     else `newly_posted_90d_proxy` (`growth_uses_registry_proxy_flag`
     exposes which source was used)
   - sponsor concentration: `sponsor_hhi`
   - site overlap: share of segment trials listing ≥1 multi-trial facility
   - legacy operational completeness: 0.5 × segment record-quality-ok share + 0.5 ×
     run-level usable-location share
2. Each input is min-max normalized to 0..1 across scored segments;
   degenerate spread (max = min) normalizes to 0 — no signal, no penalty.
3. Weighted sum (weights in the `feasibility_score_weights` seed, mirrored
   in `config/score_weights.yml`; a unit test enforces sync):
   0.35 recruiting count + 0.20 growth + 0.20 concentration + 0.15 overlap +
   0.10 legacy operational completeness. Bounded [0, 1] by construction (singular test).
4. Bands (dbt vars, synced with YAML): ≥ 0.70 `priority_review`,
   ≥ 0.45 `review`, else `watch`.
5. `priority_explanation` is assembled from fixed component phrases — the
   same inputs always yield the same sentence. Every row carries an
   `interpretation_note`.

## Scenario values (`src/analysis/roi_scenarios.py`)
Pure products of user-editable assumptions in `config/roi_assumptions.yml`
(reviews_per_cycle × deprioritized share × unit cost, etc.). Outputs embed
the disclaimer; **no observed outcomes are ever used or implied**.

## Audit definitions and scope

**Data coverage and caveats** appears on Overview, Priority Queue, Competition
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

Filters record condition groups, phases, states, normalized sponsors, and facility
triples (normalized name, city, state). Sidebar selections are retained separately
from drill-through refinements. A default segment audit covers sidebar scope before
rank/band row filters; it is not the sum of displayed overlapping segments.
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
the current dashboard passes 180 and exports it with evaluation time.

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
non-recruiting status; 30/90-day windows include their lower boundary. A single
captured date uses the explicitly labeled first-post ≥ snapshot minus 90 days proxy
for the score's growth input. Site overlap share is distinct recruiting segment
trials listing any facility with >1 recruiting trial in the profile / distinct
recruiting segment trials. The legacy operational completeness input is 0.5 ×
segment record-quality-ok share + 0.5 × latest successful-run usable-location share;
missing shares coalesce to zero. It is not scientific confidence.

Monthly counts use distinct NCT IDs across captured observations in the month.
The three-calendar-month `RANGE` includes the current month and its three prior
month boundaries (up to four monthly rows); the mean averages available rows.
Growth = (current count − first available window count) / that baseline, null for
zero baseline or fewer than two distinct months. The panel limits observation scope
to displayed months or this inclusive input window. Competition's 90-day input
selector retains the actual daily observations; a prior observation may additionally
be needed to classify an entrant.

The panel audits count inputs, not a recomputation of growth transitions, HHI,
percentiles or composite scores. Derived formulas, displayed values, source-rule
hashes and relevant windows are exported. Min-max normalization is profile-wide,
returns zero for no spread, and uses active weights; priority bands are fixed
0.45/0.70 score thresholds. These derived values cannot be inferred merely from the
union contributor count.

## Export and rule identity

The UTF-8 JSON download includes definition, `competition-audit-v1`, active
configuration/model SHA-256 identifiers, exact filters and sidebar scope, profile,
selected run IDs/dates, evaluation UTC, threshold, count status, contributing IDs,
study decisions and overlapping flags, original locations, raw references/ordinals,
content hashes, source clock precision, retrieval times, and enrollment categories.
Active hashes identify current taxonomy/geography/score/model rules; historical
configuration identity was not recorded. Raw page references can stop resolving
after configured bronze pruning. Export does not promise an indefinite archive.

Registry-derived signals support preliminary feasibility review. They do not measure site-level recruitment performance or establish scientific validity. Counts reflect captured public records and the displayed inclusion rules.
