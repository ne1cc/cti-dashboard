-- Grain: one row per registry trial x condition node, from each trial's
-- latest snapshot in the registry (matching dim_registry_trial currency).
--
-- A node is either a taxonomy condition_group (prefix "group:", e.g. "group:unmapped")
-- or a registry MeSH browse identity (mesh id, e.g. D000544). MeSH rows carry the
-- registry-supplied ancestor closure (is_ancestor).

with current_trials as (
    select nct_id, ingestion_run_id
    from {{ ref('dim_registry_trial') }}
),

mesh_nodes as (
    select
        m.nct_id,
        m.mesh_id as node_id,
        max(m.mesh_term) as condition_group,
        m.mesh_id,
        bool_and(m.is_ancestor) as is_ancestor,
        'mesh' as mapping_source,
        cast(null as boolean) as dementia_relevance_flag,
        cast(null as varchar) as mapping_confidence,
        cast(null as bigint) as source_condition_count
    from {{ ref('stg_registry_trial_condition_mesh') }} m
    inner join current_trials t
        on m.ingestion_run_id = t.ingestion_run_id
        and m.nct_id = t.nct_id
    group by
        m.nct_id,
        m.mesh_id
)

select
    {{ generate_surrogate_key(['nct_id', 'node_id']) }} as registry_trial_condition_key,
    {{ generate_surrogate_key(['nct_id']) }} as registry_trial_key,
    {{ generate_surrogate_key(['node_id']) }} as registry_condition_key,
    nct_id,
    node_id,
    condition_group,
    mesh_id,
    is_ancestor,
    mapping_source,
    dementia_relevance_flag,
    mapping_confidence,
    source_condition_count
from mesh_nodes
