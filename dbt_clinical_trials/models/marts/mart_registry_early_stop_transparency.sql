-- Early stop transparency mart over canonical registry observations.
-- Grain: preset_id x condition_group x sponsor_class x stopped_year x overall_status x why_stopped.
-- Preset denominators come from bridge_preset_trial membership (excluding unassigned);
-- source-wide stopped trial totals across the entire canonical registry (including
-- unassigned and overlapping-preset trials counted once) are preserved as separate fields.
with lead_sponsors as (
    select
        snapshot_id,
        nct_id,
        min(sponsor_class) as sponsor_class
    from {{ ref('stg_registry_trial_sponsors') }}
    where lower(sponsor_role) = 'lead_sponsor'
      and sponsor_normalized is not null
    group by 1, 2
),

canonical_stopped_trials as (
    select
        d.registry_trial_key,
        d.nct_id,
        d.snapshot_id,
        d.current_overall_status as overall_status,
        d.why_stopped,
        cast(coalesce(
            year(d.primary_completion_date),
            year(d.completion_date),
            year(d.start_date),
            year(d.study_first_post_date)
        ) as integer) as stopped_year,
        coalesce(s.sponsor_class, 'UNKNOWN') as sponsor_class
    from {{ ref('dim_registry_trial') }} d
    left join lead_sponsors s
        on d.snapshot_id = s.snapshot_id
        and d.nct_id = s.nct_id
    where d.current_overall_status in ('TERMINATED', 'WITHDRAWN', 'SUSPENDED')
),

source_stratum_totals as (
    select
        sponsor_class,
        stopped_year,
        overall_status,
        why_stopped,
        count(distinct nct_id) as source_stopped_trial_count
    from canonical_stopped_trials
    group by 1, 2, 3, 4
),

source_registry_totals as (
    select count(distinct nct_id) as source_total_stopped_trials
    from canonical_stopped_trials
),

preset_stopped_trials as (
    select
        b.preset_id,
        c.registry_trial_key,
        c.nct_id,
        c.overall_status,
        c.why_stopped,
        c.stopped_year,
        c.sponsor_class,
        coalesce(m.condition_group, 'unmapped') as condition_group
    from canonical_stopped_trials c
    inner join {{ ref('bridge_preset_trial') }} b
        on c.nct_id = b.nct_id
        and c.snapshot_id = b.snapshot_id
        and b.current_record_flag
        and not b.is_unassigned
    left join {{ ref('int_registry_preset_condition_mapping') }} m
        on m.preset_id = b.preset_id
        and m.snapshot_id = c.snapshot_id
        and m.nct_id = c.nct_id
),

grouped as (
    select
        preset_id,
        condition_group,
        sponsor_class,
        stopped_year,
        overall_status,
        why_stopped,
        count(distinct nct_id) as stopped_trial_count,
        string_agg(distinct nct_id, ', ' order by nct_id) as contributing_nct_ids
    from preset_stopped_trials
    group by 1, 2, 3, 4, 5, 6
)

select
    {{ generate_surrogate_key([
        'g.preset_id',
        'g.condition_group',
        'g.sponsor_class',
        "coalesce(cast(g.stopped_year as varchar), '__null__')",
        'g.overall_status',
        "coalesce(g.why_stopped, '__none__')",
    ]) }} as early_stop_key,
    g.preset_id,
    g.condition_group,
    {{ generate_surrogate_key(["'group:' || g.condition_group"]) }} as registry_condition_key,
    g.sponsor_class,
    g.stopped_year,
    g.overall_status,
    g.why_stopped,
    g.stopped_trial_count,
    st.source_stopped_trial_count,
    rt.source_total_stopped_trials,
    g.contributing_nct_ids
from grouped g
inner join source_stratum_totals st
    on g.sponsor_class = st.sponsor_class
    and g.stopped_year is not distinct from st.stopped_year
    and g.overall_status = st.overall_status
    and g.why_stopped is not distinct from st.why_stopped
cross join source_registry_totals rt
