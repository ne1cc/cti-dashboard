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
| listed_site_count | sum of distinct facility+city listings in the state |
| entered_recruiting_count | trials whose status became RECRUITING vs the previous project snapshot |
| left_recruiting_count | trials whose status left RECRUITING vs the previous snapshot |
| flagged_record_count | trials with `record_quality_flag != 'ok'` |

## Competition metrics (`mart_recruiting_competition`)
| Metric | Definition |
|---|---|
| recruiting_trial_count | distinct RECRUITING listings in the segment — the **density proxy** |
| new_recruiting_30d / 90d | sum of entered_recruiting transitions in a 30/90-day window over snapshot dates (`RANGE BETWEEN INTERVAL n DAYS PRECEDING`); 0 until multi-snapshot history accrues |
| newly_posted_90d_proxy | recruiting trials with `study_first_post_date` within 90 days of the snapshot — labeled proxy, not a transition |
| recruiting_count_90d_baseline | first density value inside the 90-day window |
| recruiting_growth_90d | (density − baseline) / baseline; null when baseline is 0 |
| top_sponsor_share | max lead-sponsor share of segment recruiting trials |
| sponsor_hhi | Σ (lead-sponsor share)², 0..1; 1.0 = single sponsor |
| density_percentile | `percent_rank()` of density within the snapshot |
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
   - density: `recruiting_trial_count`
   - recent growth: `new_recruiting_90d` if multi-snapshot history exists,
     else `newly_posted_90d_proxy` (`growth_uses_registry_proxy_flag`
     exposes which source was used)
   - sponsor concentration: `sponsor_hhi`
   - site overlap: share of segment trials listing ≥1 multi-trial facility
   - data confidence: 0.5 × segment record-quality-ok share + 0.5 ×
     run-level usable-location share
2. Each input is min-max normalized to 0..1 across scored segments;
   degenerate spread (max = min) normalizes to 0 — no signal, no penalty.
3. Weighted sum (weights in the `feasibility_score_weights` seed, mirrored
   in `config/score_weights.yml`; a unit test enforces sync):
   0.35 density + 0.20 growth + 0.20 concentration + 0.15 overlap +
   0.10 confidence. Bounded [0, 1] by construction (singular test).
4. Bands (dbt vars, synced with YAML): ≥ 0.70 `priority_review`,
   ≥ 0.45 `review`, else `watch`.
5. `priority_explanation` is assembled from fixed component phrases — the
   same inputs always yield the same sentence. Every row carries an
   `interpretation_note`.

## Scenario values (`src/analysis/roi_scenarios.py`)
Pure products of user-editable assumptions in `config/roi_assumptions.yml`
(reviews_per_cycle × deprioritized share × unit cost, etc.). Outputs embed
the disclaimer; **no observed outcomes are ever used or implied**.
