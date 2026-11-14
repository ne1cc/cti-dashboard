-- One row per NCT ID + raw condition + ingestion run at canonical registry grain.
-- Registry layer makes no taxonomy claims; overlay columns are NULL without a taxonomy.
select
    {{ generate_surrogate_key(['nct_id']) }} as registry_trial_key,
    ingestion_run_id,
    snapshot_id,
    nct_id,
    condition_raw,
    condition_normalized,
    condition_group,
    cast(dementia_relevance_flag as boolean) as dementia_relevance_flag,
    mapping_confidence,
    mapping_source,
    source_json_hash
from {{ source('silver', 'registry_trial_conditions') }}
where nct_id is not null
