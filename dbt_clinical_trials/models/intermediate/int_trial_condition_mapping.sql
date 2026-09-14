-- Distinct trial-to-condition-group mapping per snapshot run, per profile.
-- Grain: profile + nct_id + condition_group + ingestion_run_id.
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    condition_group,
    bool_or(dementia_relevance_flag) as dementia_relevance_flag,
    min(mapping_confidence) as mapping_confidence,
    count(distinct condition_raw) as source_condition_count
from {{ ref('stg_trial_conditions') }}
group by 1, 2, 3, 4
