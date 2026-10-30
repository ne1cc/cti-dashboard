-- One row per NCT ID + raw condition + ingestion run, with the
-- config-driven taxonomy overlay mapping applied upstream in silver.
-- mapping_source records how the group was assigned: yaml_overlay (a
-- taxonomy rule matched) or text_fallback (Issue #52 — MeSH browse terms
-- carry the identity for unmatched trials).
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    condition_raw,
    condition_normalized,
    condition_group,
    cast(dementia_relevance_flag as boolean) as dementia_relevance_flag,
    mapping_confidence,
    mapping_source,
    source_json_hash
from {{ source('silver', 'silver_trial_conditions') }}
where nct_id is not null
