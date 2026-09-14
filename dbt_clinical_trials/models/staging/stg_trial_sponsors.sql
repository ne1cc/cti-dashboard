-- One row per NCT ID + sponsor/collaborator + ingestion run.
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    sponsor_name,
    sponsor_role,
    sponsor_class,
    sponsor_normalized,
    source_json_hash
from {{ source('silver', 'silver_trial_sponsors') }}
where nct_id is not null
