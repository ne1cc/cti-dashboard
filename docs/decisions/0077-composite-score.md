# ADR 0077: Removal of Composite Feasibility Priority Score in Favor of Transparent Raw Metrics

- **Status:** Accepted (Approved User Mandate)
- **Deciders:** Product, Analytics Engineering, Clinical Operations Review
- **Date:** 2026-10-25
- **Technical Story:** Issue #77 (Part of Epic #46 — Rework the analyst workbench: score, small-n handling, provenance UX)

---

## Context

The Clinical Trial Access & Recruitment Competition Intelligence platform initially computed a composite "feasibility review priority score" (`feasibility_review_priority_score`) and assigned segments to discrete priority tiers (`priority_band`: High, Medium, Low) in the mart model `mart_feasibility_priority_queue`.

This composite score was calculated as a weighted linear combination of min-max normalized component features:
1. `norm_recruiting_trials` (weight 0.35)
2. `norm_active_trials` (weight 0.25)
3. `norm_distinct_sponsors` (weight 0.15)
4. `norm_sponsor_hhi` (weight 0.15)
5. `norm_site_overlap` (weight 0.10)

Weights were statically configured in `config/score_weights.yml` and replicated into a dbt seed file `dbt_clinical_trials/seeds/feasibility_score_weights.csv`.

### Identified Defects and Operational Limitations

1. **Directional Ambiguity:** The composite score combined opposing operational dimensions into a single scalar value. In clinical feasibility, a high number of active trials may indicate high patient availability or validated site infrastructure, but it also signals intense recruitment competition. Combining trial volume with Herfindahl-Hirschman Index (HHI) sponsor concentration and site overlap into a single number obscured whether a high score represented an attractive opportunity or an oversaturated market.
2. **Unvalidated Heuristic Weights:** The weights (0.35, 0.25, 0.15, 0.15, 0.10) were arbitrary mathematical placeholders. They had never been calibrated or validated through empirical clinical-operations time-and-motion studies, historical site activation outcomes, or practitioner interviews.
3. **Artificial Tiering:** The categorical priority bands (High, Medium, Low) imposed arbitrary cutoff thresholds that created false certainty and distorted ranking at the boundaries.
4. **Configuration Drift Risk:** Maintaining duplicate weight declarations across YAML config and dbt seeds introduced ongoing maintenance burden and schema synchronization overhead without providing analytical value.

---

## Decision

The platform adopts **Option A (User Approved)**:
> **"Remove the composite feasibility priority score and priority bands entirely; retain raw sortable metrics with column definitions."**

Specifically:
1. **Model Deletions & Refactoring:**
   - Remove `feasibility_review_priority_score`, `priority_band`, `priority_rank`, `norm_*`, `weight_*`, and `weighted_*` calculations from `mart_feasibility_priority_queue.sql`.
   - Remove the join against the `feasibility_score_weights` seed.
   - Delete `config/score_weights.yml` and `dbt_clinical_trials/seeds/feasibility_score_weights.csv`.
   - Delete obsolete dbt schema tests `assert_feasibility_score_within_bounds.sql` and `assert_weighted_components_sum_to_score.sql`.
2. **Preservation of Raw Descriptive Metrics:**
   - Retain all underlying, observable registry metrics:
     - `recruiting_trial_count` (total trials in recruiting or active status)
     - `active_recruiting_trials` (trials actively recruiting participants)
     - `distinct_sponsors` (unique trial sponsors operating in the segment)
     - `sponsor_hhi` (Herfindahl-Hirschman Index measuring sponsor concentration, bounded in $[0, 10000]$)
     - `site_overlap_rate` (ratio of shared trial sites across competing studies, bounded in $[0, 1.0]$)
     - `latest_snapshot_date` (provenance timestamp)
   - Order the primary mart by `active_recruiting_trials desc` by default.
3. **User Interface Realignment:**
   - Rename the Streamlit priority queue page from "Priority Queue" to "Registry Segments".
   - Remove score and band filters, badges, and columns from the UI.
   - Present the segment table with transparent, user-sortable raw metrics, defaulting to descending sort by `active_recruiting_trials`.
   - Provide explicit metric definitions in informational tooltips and expanders so analysts can sort by whichever metric answers their specific question.
4. **Documentation & Catalog Updates:**
   - Update `docs/metric_definitions.md`, `PROJECT_DOCUMENTATION.md`, and `_marts.yml` to remove composite score references and document the raw metric formulas.

---

## Options Evaluated

- **Option A (Adopted — Approved):** Remove composite score and priority bands; retain raw sortable metrics with clear column definitions.
  - *Pros:* Eliminates unvalidated pseudo-science; restores transparency; empowers domain experts to sort by their specific operational criterion; eliminates dead seeds and weight configs.
  - *Cons:* Requires analysts to choose their own sorting criteria rather than presenting an automated single-column ranking.
- **Option B (Rejected):** Rebuild the composite score using practitioner interview weights and sensitivity analysis.
  - *Pros:* Preserves a single ranking column.
  - *Cons:* High research cost; practitioner weights vary wildly by indication, phase, and organization; still compresses contradictory dimensions into a single scalar.
- **Option C (Rejected):** Keep score as an unweighted heuristic index with configurable presets.
  - *Pros:* Retains index functionality without claiming prescriptive priority.
  - *Cons:* Adds cognitive load and maintenance burden for an unvalidated feature that distracted from verified registry counts.

---

## Consequences

- Analysts and stakeholders view transparent, empirical counts and concentration measures directly from ClinicalTrials.gov.
- Pipeline execution is faster and simpler, with no seed joins or intermediate normalization CTEs.
- The platform adheres strictly to its clinical interpretation guardrails (ADR 0065, `docs/clinical_interpretation_guardrails.md`) by presenting objective registry evidence without making unvalidated prescriptive claims.
