-- One row per NCT ID + raw condition + ingestion run at canonical registry grain.
-- Mapping source is text_fallback with group 'unmapped' (no taxonomy overlay).
select
    {{ generate_surrogate_key(['nct_id']) }} as registry_trial_key,
    ingestion_run_id,
    nct_id,
    condition_raw,
    condition_normalized,
    condition_group,
    cast(dementia_relevance_flag as boolean) as dementia_relevance_flag,
    mapping_confidence,
    coalesce(mapping_source, 'text_fallback') as mapping_source,
    source_json_hash
from {{ source('silver', 'registry_trial_conditions') }}
where nct_id is not null
