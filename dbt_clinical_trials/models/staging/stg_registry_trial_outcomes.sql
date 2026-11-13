-- One row per NCT ID + outcome type + outcome index + ingestion run at canonical registry grain.
select
    {{ generate_surrogate_key(['nct_id']) }} as registry_trial_key,
    ingestion_run_id,
    nct_id,
    outcome_type,
    cast(outcome_index as integer) as outcome_index,
    outcome_measure,
    outcome_description,
    time_frame,
    source_json_hash
from {{ source('silver', 'registry_trial_outcomes') }}
where nct_id is not null
