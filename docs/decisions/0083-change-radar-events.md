# 0083 — Change Radar event grain and guardrail wording

Status: accepted

## Context
The registry API serves only current records, so history exists only as this
project's own run snapshots. Silver and manifests are pruned by
`src/utils/retention.py`, which would erase any history derived from them.

## Decision
- `mart_trial_change_events` has one row per indication profile × NCT ID ×
  observation pair (previous trusted run, current trusted run) × event type ×
  field. Types: `status_change`, `enrollment_change` (count and type are separate
  field rows), `completion_date_change` (primary and overall, with registry date
  type ESTIMATED/ACTUAL), `outcome_set_change` (order-sensitive hash of primary
  and secondary outcome measure/description/time frame).
- `event_id` is an md5 of profile, NCT ID, both run ids, event type and field.
- Run-trust gate: a run may be either side of a pair only if its manifest status
  is `success` and its pages were retrieved no earlier than the run started
  (re-used earlier snapshots fail). Legacy runs with no retrieval stamp pass.
- The mart is incremental, `full_refresh=false`, keyed on `event_id`, so events
  stay in the warehouse after the source runs are pruned.

## Guardrail wording
Events describe changes in a public registry record between two observations by
this pipeline. They are not clinical conclusions, efficacy or safety signals, or
recommendations, and a change may reflect an edit to the record rather than a
change in the study. UI and docs must use "record changed" language.

## Consequences
Rebuilding the event table requires dropping it by hand; pruned history cannot be
re-derived. A pair that spans a pruned run is never created retroactively.
