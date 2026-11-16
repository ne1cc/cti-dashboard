-- One row per NCT ID x consecutive trusted canonical run pair whose
-- outcome-set hash differs. Pairs follow int_registry_trusted_change_runs, so
-- untrusted or profile-only runs never appear on either side.
select
    c.registry_trial_key,
    c.nct_id,
    r.previous_ingestion_run_id,
    r.ingestion_run_id,
    r.previous_observed_at_utc,
    r.observed_at_utc,
    p.outcome_set_hash as before_outcome_set_hash,
    c.outcome_set_hash as after_outcome_set_hash,
    p.outcome_count as before_outcome_count,
    c.outcome_count as after_outcome_count,
    p.outcome_summary as before_outcome_summary,
    c.outcome_summary as after_outcome_summary
from {{ ref('int_registry_trial_outcome_sets') }} c
inner join {{ ref('int_registry_trusted_change_runs') }} r
    on c.snapshot_id = r.ingestion_run_id
inner join {{ ref('int_registry_trial_outcome_sets') }} p
    on p.snapshot_id = r.previous_ingestion_run_id
    and p.nct_id = c.nct_id
where p.outcome_set_hash != c.outcome_set_hash
