-- One row per NCT ID + outcome type + outcome index + ingestion run.
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    outcome_type,
    cast(outcome_index as integer) as outcome_index,
    outcome_measure,
    outcome_description,
    time_frame,
    source_json_hash
from {{ source('silver', 'silver_trial_outcomes') }}
where nct_id is not null
