-- One row per NCT ID + raw condition + ingestion run, with the
-- config-driven ADRD taxonomy mapping applied upstream in silver.
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    condition_raw,
    condition_normalized,
    condition_group,
    cast(dementia_relevance_flag as boolean) as dementia_relevance_flag,
    mapping_confidence,
    source_json_hash
from {{ source('silver', 'silver_trial_conditions') }}
where nct_id is not null
