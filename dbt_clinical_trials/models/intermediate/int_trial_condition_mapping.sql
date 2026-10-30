-- Distinct trial-to-condition-group mapping per snapshot run, per profile.
-- Grain: profile + nct_id + condition_group + ingestion_run_id.
-- mapping_source rolls the row-level overlay/fallback split up to the group:
-- a group is yaml_overlay when any of its condition texts matched a taxonomy
-- rule, else text_fallback (Issue #52).
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    condition_group,
    bool_or(dementia_relevance_flag) as dementia_relevance_flag,
    min(mapping_confidence) as mapping_confidence,
    case
        when bool_or(mapping_source = 'yaml_overlay') then 'yaml_overlay'
        else 'text_fallback'
    end as mapping_source,
    count(distinct condition_raw) as source_condition_count
from {{ ref('stg_trial_conditions') }}
group by 1, 2, 3, 4
