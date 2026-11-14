-- One row per NCT ID + arm group + run at canonical registry grain.
select
    {{ generate_surrogate_key(['nct_id']) }} as registry_trial_key,
    ingestion_run_id,
    snapshot_id,
    nct_id,
    cast(arm_ordinal as bigint) as arm_ordinal,
    arm_label,
    arm_type,
    arm_description,
    intervention_names,
    source_json_hash
from {{ source('silver', 'registry_trial_arms') }}
where nct_id is not null
