-- One row per indication profile per condition node (Issue #52 grain).
-- A node is a taxonomy condition_group ("group:<slug>", overlay/fallback text
-- mapping) or a registry MeSH browse identity (mesh id, e.g. D000544), from
-- each trial's current record in that profile. MeSH nodes include the
-- registry-supplied ancestor closure, so selecting any tree node — including
-- nodes no YAML taxonomy knows about — reaches every trial whose direct terms
-- descend from it, with no new config file. Counts are per profile: the ADRD
-- and NSCLC taxonomies can map the same raw condition to different groups, so
-- a shared trial_count would be wrong on both profiles' rows.
with current_runs as (
    select ingestion_run_id, indication_profile_id, nct_id
    from {{ ref('int_current_trial_status') }}
),

current_conditions as (
    select c.*
    from {{ ref('stg_trial_conditions') }} c
    inner join current_runs r
        on c.ingestion_run_id = r.ingestion_run_id
        and c.indication_profile_id = r.indication_profile_id
        and c.nct_id = r.nct_id
    where c.condition_normalized is not null
),

-- Taxonomy overlay / fallback group nodes: the segments every existing
-- group-based consumer reads. Grain: profile x condition_group.
group_nodes as (
    select
        'group:' || condition_group as node_id,
        indication_profile_id,
        condition_group as node_label,
        cast(null as varchar) as mesh_id,
        false as is_ancestor,
        min(condition_raw) as condition_raw,
        coalesce(bool_or(dementia_relevance_flag), false) as dementia_relevance_flag,
        min(mapping_confidence) as mapping_confidence,
        case
            when bool_or(mapping_source = 'yaml_overlay') then 'yaml_overlay'
            else 'text_fallback'
        end as mapping_source,
        count(distinct nct_id) as trial_count
    from current_conditions
    group by indication_profile_id, condition_group
),

-- Registry MeSH nodes, direct terms and ancestor closure alike
-- (is_ancestor is true only where every membership edge is a closure edge).
-- Grouped solely by mesh_id per profile so term renames across runs do not
-- split nodes; deterministic label chosen from the latest run (Issue #52 review).
current_mesh_rows as (
    select
        s.indication_profile_id,
        s.mesh_id,
        s.mesh_term,
        s.is_ancestor,
        s.nct_id,
        s.ingestion_run_id
    from {{ ref('stg_trial_condition_mesh') }} s
    inner join current_runs r
        on s.ingestion_run_id = r.ingestion_run_id
        and s.indication_profile_id = r.indication_profile_id
        and s.nct_id = r.nct_id
),

mesh_labels as (
    select
        indication_profile_id,
        mesh_id,
        mesh_term as node_label
    from current_mesh_rows
    qualify row_number() over (
        partition by indication_profile_id, mesh_id
        order by ingestion_run_id desc, mesh_term asc
    ) = 1
),

mesh_nodes as (
    select
        c.mesh_id as node_id,
        c.indication_profile_id,
        l.node_label,
        c.mesh_id,
        bool_and(c.is_ancestor) as is_ancestor,
        cast(null as varchar) as condition_raw,
        cast(null as boolean) as dementia_relevance_flag,
        cast(null as varchar) as mapping_confidence,
        'mesh' as mapping_source,
        count(distinct c.nct_id) as trial_count
    from current_mesh_rows c
    inner join mesh_labels l
        on c.indication_profile_id = l.indication_profile_id
        and c.mesh_id = l.mesh_id
    group by c.indication_profile_id, c.mesh_id, l.node_label
),

nodes as (
    select * from group_nodes
    union all
    select * from mesh_nodes
)

select
    {{ generate_surrogate_key(['indication_profile_id', 'node_id']) }}
        as condition_key,
    indication_profile_id,
    node_id,
    node_label as condition_normalized,
    coalesce(condition_raw, node_label) as condition_display_name,
    node_label as condition_group,
    mesh_id,
    is_ancestor,
    mapping_source,
    dementia_relevance_flag,
    mapping_confidence,
    trial_count
from nodes
