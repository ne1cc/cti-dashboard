-- One row per NCT ID + intervention + ingestion run at canonical registry grain.
select
    {{ generate_surrogate_key(['nct_id']) }} as registry_trial_key,
    ingestion_run_id,
    snapshot_id,
    nct_id,
    intervention_name,
    intervention_type,
    intervention_description,
    intervention_normalized,
    source_json_hash
from {{ source('silver', 'registry_trial_interventions') }}
where nct_id is not null
