-- One row per NCT ID + sponsor/collaborator + ingestion run at canonical registry grain.
select
    {{ generate_surrogate_key(['nct_id']) }} as registry_trial_key,
    ingestion_run_id,
    nct_id,
    sponsor_name,
    sponsor_role,
    sponsor_class,
    sponsor_normalized,
    source_json_hash
from {{ source('silver', 'registry_trial_sponsors') }}
where nct_id is not null
