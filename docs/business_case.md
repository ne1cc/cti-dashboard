# Proposed business use: registry-based competition screening

## Executive summary

A clinical-operations analyst preparing a feasibility review needs to decide
which condition–phase–geography segments, study records and evidence gaps to
investigate next. The assumed current process combines manual registry searches,
spreadsheet comparisons and evidence preparation, making recurring reviews costly
to assemble and difficult to reproduce. Those difficulties and their business
consequences are hypotheses requiring stakeholder validation.

The product provides a captured study landscape, contributing NCT IDs, coverage
caveats and an exportable audit on a proposed weekly collection cadence. The analyst
uses this evidence to prepare a scoped research shortlist and follow-up questions
for qualified feasibility review. Success would be evaluated through preparation
time, evidence completeness and count reproducibility against a measured baseline.

Calculation and pipeline checks are implemented. The proposed workflow has not been
validated through stakeholder interviews, and no operational improvement has been
measured. The [national case study](feasibility_case_study.md) demonstrates a
descriptive workflow with actual captured records.

## Business-problem definition

| Component | Proposed definition | Evidence status |
|---|---|---|
| User | Clinical-operations analyst preparing an initial geographic feasibility review | Role and recurring need require practitioner validation |
| Decision | Which selected segments, study records and evidence gaps should I investigate next? | The implemented views support exploration; decision usefulness is unvalidated |
| Current workflow | Search registry listings, reconcile scope and counts in a spreadsheet, assemble references and questions for a reviewer | Assumed process; no observed workflow or interviews |
| Specific friction | Repeated scope reconciliation, unclear contributors, changing records and time spent checking a handoff | Hypotheses to measure during comparable review tasks |
| Business consequence | Analyst time spent rebuilding evidence and reviewer rework when observations cannot be checked | No measured cost, delay or downstream outcome |
| Data-product output | Captured landscape by condition, phase and geography, contributing-study detail, coverage caveats and an audit export | Implemented; the analyst prepares the research shortlist/memo |
| Action | Select records and segments for deeper research, confirm missing information and hand scoped evidence to a qualified reviewer | Proposed workflow |
| Freshness/cadence | Weekly collection for recurring reviews, with capture age and public source age inspected separately | Proposed cadence; freshness SLA and tolerance are unvalidated |
| Success KPI and baseline | Preparation minutes per comparable review, evidence completeness and count reproducibility | Baselines and numerical targets are unmeasured |

The analyst is the proposed end user. A feasibility lead is the proposed reviewer
and sponsor of the workflow; the economic buyer, budget owner and owner of each
success metric require confirmation. Interviews should establish who requests the
review, how frequently it occurs, how evidence is handed off and what happens when
the review is late or incomplete.

## Assumed current-state workflow

The starting hypothesis is that an analyst receives a condition, phase and
geographic scope, searches registry records, consolidates study/location listings
in a spreadsheet and prepares a memo. A reviewer then checks the scope, contributors
and gaps before requesting further research. Repeat reviews may require reconciling
changed records and rebuilding the evidence.

Validate this hypothesis by observing a recent review and its existing files,
handoffs and workarounds. Identify which steps actually consume time, cause rework
or prevent a useful decision. An existing efficient workflow may make the proposed
product unnecessary.

## Proposed workflow and usable output

1. Agree on the condition mapping, phase, geography and captured dataset.
2. Examine distinct matching study counts alongside coverage and exclusions.
3. Inspect study contributors, public source dates and original location listings.
4. Record limitations and compare the evidence with other screening configurations
   where those configurations have explicit rules.
5. Export the audit and prepare a research shortlist/memo identifying the records
   or segments to investigate, the reason for follow-up and missing information.
6. Hand the scoped evidence to a qualified feasibility reviewer and confirm the
   additional research needed.

The app supplies the landscape and evidence; the analyst authors the shortlist
and memo. A usable handoff contains condition/phase/geography filters, captured
dates and run IDs, metric definitions, contributing NCT IDs, coverage and exclusions,
source-date caveats and explicit follow-up questions. These fields let the reviewer
check an observation and decide what evidence to request next. See the
[project brief](project_brief.md) for reporting requirements and the
[memo template](executive_memo_template.md) for preparing the handoff.

A low count is an observation about recorded study presence. It does not establish
better recruitment prospects. A high count does not establish that a facility or
region lacks capacity. The analysis provides no automatic pivot/proceed threshold.

The optional weighted review-priority score provides a disclosed ordering of
segments. Its predictive and operational usefulness is unvalidated; priority bands
are not intervention or site-activation thresholds. The first use case can be
evaluated through descriptive counts and inspectable evidence without relying on
the score.

## Implemented capabilities and proposed benefits

| Implemented capability | Evidence | Proposed benefit requiring evaluation |
|---|---|---|
| Distinct recruiting-study counts by condition, phase and state | dbt marts, metric dictionary and contributor audits | Organize an initial landscape review |
| Repeated captured snapshots | Project-created status history and explicit time windows | Identify reported changes that need investigation |
| Coverage and caveats | Eligible denominators, exclusions and separate freshness clocks | Make incomplete reporting visible during interpretation |
| Record-level traceability | NCT IDs, raw references, hashes and exported rules/filters | Help an analyst inspect how a result was produced |
| Preliminary review-priority signal | Disclosed component formulas, weights and proxy limitations | Organize review effort; predictive or operational usefulness remains unvalidated |

Weekly collection is proposed for recurring reviews; it is not an enforced
freshness SLA. A reviewer must agree an acceptable capture age and how to handle
older source records. Refreshes capture publicly available records on the configured
cadence. Pipeline age, posted-update age and verification age answer different
questions. Registry updates and operations can differ, and the data is not a
real-time view of recruitment.
The national [case study](feasibility_case_study.md) demonstrates a descriptive
workflow with actual captured records; it does not validate a score or recommend regions.

## Alternatives and the need for reliable engineering

| Alternative | When it may be sufficient | What would justify the proposed product |
|---|---|---|
| Manual registry search and spreadsheet | An isolated question with a small cohort and an acceptable existing review process | Observed recurring scope reconciliation, contributor checking or evidence rework |
| One-off analysis or static report | A fixed captured dataset and a single handoff | Repeated comparisons requiring explicit collection history and consistent rules |
| Existing organizational tooling | The team already has trusted evidence and a suitable review workflow | A demonstrated gap that this project can address without duplicating that workflow |
| Continue the current process | Review friction is minor or proposed outputs do not improve the task | Measured pain and stakeholder willingness to use the output |

Reliable engineering becomes useful when the same review recurs. Ingestion manifests
identify complete captures; snapshots retain observed changes; documented taxonomy,
geography and metric rules make comparisons explicit; deduplication and quality
checks protect counts; lineage and audit exports let reviewers trace contributors.
Retained raw records allow recalculation under revised rules while available.
Configured pruning limits retention, and active rule hashes do not establish
unrecorded historical configuration identity.

The dashboard is the interface to that evidence. Product value depends on whether
the evidence removes a demonstrated workflow bottleneck.

## Follow-up information

Before making an operational decision, reviewers would need independent evidence
about patient availability, relevant site capabilities, current study operations
and the comparability of participant populations and protocols. The registry
screen does not supply those answers.

## Evaluating usefulness

First measure the existing process on a fixed captured dataset and agreed review
scope. Compare the same deliverable using the product, accounting for task order
and familiarity. Include evidence checking and memo preparation in both tasks;
a faster dashboard lookup alone does not establish a faster review.

| Measure | Definition and evaluation method | Baseline and target |
|---|---|---|
| Review preparation time | Minutes from receiving the agreed scope to delivering a reviewer-acceptable evidence memo; compare matched manual and product-assisted tasks | Unmeasured; establish baseline, then agree a meaningful reduction with the metric owner |
| Evidence completeness | Required applicable handoff fields present / required applicable fields, using the same checklist and reviewer in both tasks | Unmeasured; agree an acceptable level before evaluation |
| Count reproducibility | Sampled published counts independently reproduced with matching scope and NCT contributors / sampled published counts | Workflow baseline unmeasured; agree the acceptance level before evaluation |
| Use in recurring reviews | Eligible review tasks that use an exported audit or resulting evidence memo / eligible review tasks during an agreed observation period | Adoption unmeasured; define eligible tasks and target with the workflow owner |

Ask analysts to locate contributors, explain the denominator and identify missing
evidence. Record reviewer correction requests, participant feedback and unsuccessful
tasks alongside completion times. A favorable small pilot supports a workflow
hypothesis; it does not establish recruitment improvement or financial return.

Collection success, capture age and automated quality checks measure delivery
reliability. Preparation time and reviewer rework are the proposed operational
outcomes. Both matter: technically correct evidence can still fail if it does not
fit the reviewer handoff, takes too much interpretation or duplicates existing tools.

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
