# ADR 0077: Removal of Composite Feasibility Priority Score in Favor of Transparent Raw Metrics

- **Status:** Accepted (Approved User Mandate)
- **Deciders:** Repository Owner / Product & Analytics Review (2026-10-23 Mandate)
- **Date:** 2026-10-25
- **Technical Story:** Issue #77 (Part of Epic #46 — Rework the analyst workbench: score, small-n handling, provenance UX)

---

## Context

The Clinical Trial Access & Recruitment Competition Intelligence platform initially computed a composite "feasibility review priority score" (`feasibility_review_priority_score`) and assigned segments to discrete priority tiers (`priority_band`: `watch`, `review`, `priority_review`) in the mart model `mart_feasibility_priority_queue`.

### Historical Configuration (Commit `7853f57`)

As configured in `config/score_weights.yml` (and replicated into the dbt seed `dbt_clinical_trials/seeds/feasibility_score_weights.csv`), the composite score was computed as a weighted sum of min-max normalized component features:
1. `recruiting_trial_count`: 0.35 (count of recruiting trials in segment)
2. `recent_recruiting_growth`: 0.20 (90-day recruiting entrants or first-posted proxy)
3. `sponsor_concentration`: 0.20 (Herfindahl-Hirschman Index, HHI)
4. `site_overlap`: 0.15 (share of recruiting trials sharing facilities with competing studies in the same indication profile)
5. `data_confidence_adjustment`: 0.10 (operational completeness adjustment)

Segments were partitioned into fixed priority bands based on absolute cutoffs:
- `watch`: score < 0.45
- `review`: 0.45 <= score < 0.70
- `priority_review`: score >= 0.70

### Identified Defects and Operational Limitations (Issue #77)

1. **Ambiguous Ranking Direction:** The composite score combined opposing operational dimensions into a single scalar value. In clinical feasibility, high recruiting trial volume may signal proven regional clinical infrastructure, but it also indicates intense patient recruitment competition. Combining trial volume with sponsor concentration (HHI) and site overlap into a single number obscured whether a high score represented an attractive, actionable expansion opportunity or an oversaturated, congested market.
2. **Rewarding Sponsor Concentration Without Stated Rationale:** The model positively weighted sponsor concentration (`sponsor_concentration`: 0.20), awarding higher priority scores to segments monopolized by one or two sponsors. No stated rationale was documented for rewarding sponsor concentration; highly concentrated segments may reflect competitive dominance or entrenched investigator relationships that present recruitment barriers rather than advantages.
3. **Min-Max Relative Scaling Evaluated Against Fixed Absolute Bands:** Component inputs were min-max scaled relative to the observed minimum and maximum within the current indication profile cohort: `(input - min) / (max - min)`. Because min-max scaling is cohort-relative, the resulting scalar score varies wildly depending on cohort bounds. Evaluating cohort-relative scores against fixed, hardcoded absolute thresholds (0.45 and 0.70) caused the clinical meaning of `priority_review`, `review`, and `watch` to shift unpredictably across indication profiles and data refreshes.
4. **Arbitrary Weights Without Sensitivity Analysis:** The weights (0.35, 0.20, 0.20, 0.15, 0.10) were unvalidated mathematical heuristics. They were never calibrated against historical site activation success, patient accrual timelines, or formal practitioner interviews, nor were they subjected to sensitivity or rank-stability analyses.

---

## Options Evaluated (Issue #77)

- **Option A (Adopted — Approved):** Remove composite score and priority bands; retain raw sortable metrics with explicit column definitions.
  - *Pros:* Eliminates unvalidated pseudo-science; restores transparency; empowers domain experts to sort by their specific operational criterion; eliminates dead seeds and weight configs.
  - *Cons:* Requires analysts to choose their own sorting criteria rather than presenting an automated single-column ranking.
- **Option B (Rejected):** Rebuild the composite score with a single defined meaning ("recruitment crowding index"), documented directions, and rank-stability analysis.
  - *Pros:* Preserves a single ranking index with a clearly stated operational interpretation.
  - *Cons:* High research cost; practitioner weights vary wildly by indication, phase, and organization; still compresses multidimensional trade-offs into an index.
- **Option C (Rejected):** Keep as-is (requires written justification of items 1–4).
  - *Pros:* No code or interface changes needed.
  - *Cons:* Perpetuates unvalidated, misleading scoring and fails to address the 4 critical analytical defects identified in Issue #77.

---

## Decision

The platform adopts **Option A (User Approved Mandate)**:
> **"Remove the composite feasibility priority score and priority bands entirely; retain raw sortable metrics with column definitions."**

Specifically:
1. **Model Deletions & Refactoring:**
   - Remove `feasibility_review_priority_score`, `priority_band`, `priority_rank`, `normalized_*`, `weight_*`, and `weighted_*` calculations from `mart_feasibility_priority_queue.sql`.
   - Remove the join against the `feasibility_score_weights` seed.
   - Delete `config/score_weights.yml` and `dbt_clinical_trials/seeds/feasibility_score_weights.csv`.
   - Delete obsolete dbt schema tests `assert_feasibility_score_within_bounds.sql` and `assert_weighted_components_sum_to_score.sql`.
2. **Preservation of Raw Descriptive Metrics:**
   - Retain all underlying, observable registry metrics:
     - `recruiting_trial_count` (standardized alias: `active_recruiting_trials`): Raw count of actively recruiting interventional trials.
     - `sponsor_count` (standardized alias: `distinct_sponsors`): Count of distinct lead sponsors operating in the segment.
     - `sponsor_hhi`: Herfindahl-Hirschman Index of sponsor concentration, bounded in the warehouse in the range [0, 1].
     - `site_overlap_share` (standardized alias: `site_overlap_rate`): Share of recruiting trials in the segment that share at least one facility with another competing trial in the same indication profile, bounded in [0, 1].
     - `data_confidence_share`: Operational data completeness share (0.5 * `record_quality_ok_share` + 0.5 * `usable_location_share`), retained temporarily as an operational completeness signal without affecting sorting/ranking, pending Issue #78 ("Separate completeness from ranking").
     - `recent_growth_input`: Raw trial count or growth measure used for segment observation (90-day count of newly recruiting trials, or first-posted count when `growth_uses_registry_proxy_flag`).
     - `growth_uses_registry_proxy_flag`: Flag indicating whether first-posted date proxy is in use.
   - Note on retained descriptive competition tier: `competition_signal_band` (High/Moderate/Low competition derived from sponsor concentration) is retained in the mart as a categorical descriptive tier and is separate from the removed composite feasibility priority score.
   - Default sorting: Order the primary mart deterministically by `active_recruiting_trials desc, distinct_sponsors desc, priority_queue_key asc`.
3. **Standardized Readability Aliases:**
   - To provide clean, intuitive column names across analytics queries and UI consumers while maintaining backward compatibility with legacy schema conventions:
     - `active_recruiting_trials` is a standardized alias of base column `recruiting_trial_count`.
     - `distinct_sponsors` is a standardized alias of base column `sponsor_count`.
     - `site_overlap_rate` is a standardized alias of base column `site_overlap_share`.
     - `latest_snapshot_date` is a standardized alias of base column `snapshot_date`.
4. **User Interface Realignment:**
   - Rename the Streamlit priority queue page from "Priority Queue" to "Registry Segments".
   - Remove score and band filters, badges, and columns from the UI.
   - Present the segment table with transparent, user-sortable raw metrics, defaulting to descending sort by `active_recruiting_trials`.
   - Provide explicit metric definitions in informational tooltips and expanders so analysts can sort by whichever metric answers their specific question.
5. **Documentation & Catalog Updates:**
   - Update `docs/metric_definitions.md`, `PROJECT_DOCUMENTATION.md`, and `_marts.yml` to remove composite score references and document the raw metric formulas.

---

## Consequences

- Analysts and stakeholders view transparent, empirical counts and concentration measures directly from ClinicalTrials.gov.
- Pipeline logic is simpler with fewer seeds and tests to maintain; runtime impact is expected to be minor.
- The platform adheres strictly to its clinical interpretation guardrails (ADR 0065, `docs/clinical_interpretation_guardrails.md`) by presenting objective registry evidence without making unvalidated prescriptive claims.
