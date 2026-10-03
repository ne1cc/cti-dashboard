# Proposed business use: registry-based competition screening

## Decision and intended user

A clinical-operations analyst preparing an initial geographic review needs to
understand the reported study landscape and identify questions for further
investigation. This project supports that preliminary review through captured
ClinicalTrials.gov records, explicit metric rules and contributing-study evidence.

The proposed question is: **Which patterns and data gaps warrant further
feasibility investigation under the selected condition, phase and geography rules?**
The workflow has not been validated through stakeholder interviews. The project
has not measured changes in review time, costs, site selection or recruitment outcomes.

## Proposed workflow

1. Agree on the condition mapping, phase, geography and captured dataset.
2. Examine distinct matching study counts alongside coverage and exclusions.
3. Inspect study contributors, public source dates and original location listings.
4. Record limitations and compare the evidence with other screening configurations
   where those configurations have explicit rules.
5. Export the evidence and formulate follow-up questions for qualified review.

A low count is an observation about recorded study presence. It does not establish
better recruitment prospects. A high count does not establish that a facility or
region lacks capacity. The analysis provides no automatic pivot/proceed threshold.

## Implemented capabilities and proposed benefits

| Implemented capability | Evidence | Proposed benefit requiring evaluation |
|---|---|---|
| Distinct recruiting-study counts by condition, phase and state | dbt marts, metric dictionary and contributor audits | Organize an initial landscape review |
| Repeated captured snapshots | Project-created status history and explicit time windows | Identify reported changes that need investigation |
| Coverage and caveats | Eligible denominators, exclusions and separate freshness clocks | Make incomplete reporting visible during interpretation |
| Record-level traceability | NCT IDs, raw references, hashes and exported rules/filters | Help an analyst inspect how a result was produced |
| Preliminary review-priority signal | Disclosed component formulas, weights and proxy limitations | Organize review effort; predictive or operational usefulness remains unvalidated |

Refreshes capture publicly available records on the configured cadence. Registry
updates and operations can differ, and the data is not a real-time view of recruitment.
The national [case study](feasibility_case_study.md) demonstrates a descriptive
workflow with actual captured records; it does not validate a score or recommend regions.

## Follow-up information

Before making an operational decision, reviewers would need independent evidence
about patient availability, relevant site capabilities, current study operations
and the comparability of participant populations and protocols. The registry
screen does not supply those answers.

## Evaluating usefulness

A proposed workflow evaluation could ask analysts to reproduce a count, locate its
contributors, explain the coverage denominator and identify missing evidence.
Document participant feedback and any measured task completion times before
claiming usability or efficiency improvements. Any comparison of review time
would need a stated baseline and comparable tasks.

## Scenario costs and benefit status

The existing [scenario calculator](metric_definitions.md) uses editable assumptions
in `config/roi_assumptions.yml`. Its arithmetic is hypothetical and does not show
observed savings, delay avoidance or financial return. Organization-specific
assumptions and a measured workflow evaluation would be needed for such claims.

## Interpretation and reproducibility

Records are public registry listings, not patient-level data. Results support
preliminary feasibility review and do not establish scientific validity or
site-level recruitment performance. Counts reflect captured records and displayed
inclusion rules. See the [project brief](project_brief.md),
[metric definitions](metric_definitions.md) and
[limitations](assumptions_and_limitations.md) for scope and retention boundaries.
