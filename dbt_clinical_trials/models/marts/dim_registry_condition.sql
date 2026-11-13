-- One row per condition node across the whole registry (canonical registry grain).
-- A node is a MeSH browse identity (bare mesh id) or a taxonomy group ("group:<slug>").
-- Grain: node_id. Primary key: registry_condition_key.

select
    registry_condition_key,
    node_id,
    max(condition_group) as condition_normalized,
    coalesce(
        min(case when mapping_source != 'mesh' then condition_group end),
        max(condition_group)
    ) as condition_display_name,
    max(condition_group) as condition_group,
    max(mesh_id) as mesh_id,
    bool_and(is_ancestor) as is_ancestor,
    max(mapping_source) as mapping_source,
    coalesce(bool_or(dementia_relevance_flag), false) as dementia_relevance_flag,
    min(mapping_confidence) as mapping_confidence,
    count(distinct nct_id) as trial_count
from {{ ref('bridge_registry_trial_condition') }}
group by registry_condition_key, node_id
