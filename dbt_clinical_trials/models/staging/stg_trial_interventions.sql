-- One row per NCT ID + intervention + ingestion run.
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    intervention_name,
    intervention_type,
    intervention_description,
    intervention_normalized,
    source_json_hash
from {{ source('silver', 'silver_trial_interventions') }}
where nct_id is not null
