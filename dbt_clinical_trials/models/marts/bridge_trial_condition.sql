-- Grain (Issue #52): one row per profile x trial x condition node, from each
-- trial's latest snapshot in that profile (matching dim_trial currency).
--
-- A node is either a taxonomy condition_group (prefix "group:", from the
-- overlay/fallback text mapping — the segments every existing count mart and
-- dashboard view consume) or a registry MeSH browse identity (the mesh id,
-- e.g. D000544). MeSH rows carry the registry-supplied ancestor closure:
-- a trial appears under every ancestor of its direct terms, so selecting any
-- tree node (e.g. D009369 Neoplasms) reaches all descendant trials without
-- per-disease configuration. is_ancestor flags those closure edges; group
-- rows are always false.
--
-- New-condition browsing reads mesh rows; existing group-based views filter
-- on condition_group slugs, which MeSH term labels never collide with.
-- Count marts and similarity keep their overlay-only semantics by filtering
-- is_ancestor = false (and similarity additionally excludes direct mesh rows
-- so its weighting is unchanged this release).
with current_trials as (
    select nct_id, indication_profile_id, ingestion_run_id
    from {{ ref('int_current_trial_status') }}
),

group_nodes as (
    select
        m.nct_id,
        m.indication_profile_id,
        'group:' || m.condition_group as node_id,
        m.condition_group,
        cast(null as varchar) as mesh_id,
        false as is_ancestor,
        m.mapping_source,
        m.dementia_relevance_flag,
        m.mapping_confidence,
        m.source_condition_count
    from {{ ref('int_trial_condition_mapping') }} m
    inner join current_trials c
        on m.ingestion_run_id = c.ingestion_run_id
        and m.indication_profile_id = c.indication_profile_id
        and m.nct_id = c.nct_id
),

mesh_nodes as (
    select
        s.nct_id,
        s.indication_profile_id,
        s.mesh_id as node_id,
        s.mesh_term as condition_group,
        s.mesh_id,
        s.is_ancestor,
        'mesh' as mapping_source,
        cast(null as boolean) as dementia_relevance_flag,
        cast(null as varchar) as mapping_confidence,
        cast(null as bigint) as source_condition_count
    from {{ ref('stg_trial_condition_mesh') }} s
    inner join current_trials c
        on s.ingestion_run_id = c.ingestion_run_id
        and s.indication_profile_id = c.indication_profile_id
        and s.nct_id = c.nct_id
),

nodes as (
    select * from group_nodes
    union all
    select * from mesh_nodes
)

select
    {{ generate_surrogate_key([
        'nct_id', 'indication_profile_id', 'node_id',
    ]) }} as trial_condition_key,
    {{ generate_surrogate_key(['nct_id', 'indication_profile_id']) }}
        as trial_key,
    {{ generate_surrogate_key(['indication_profile_id', 'node_id']) }}
        as condition_key,
    nct_id,
    indication_profile_id,
    node_id,
    condition_group,
    mesh_id,
    is_ancestor,
    mapping_source,
    dementia_relevance_flag,
    mapping_confidence,
    source_condition_count
from nodes
