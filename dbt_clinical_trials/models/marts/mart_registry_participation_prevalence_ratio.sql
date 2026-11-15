-- Participation-to-prevalence ratio at canonical registry grain.
-- Grain: mesh_id x time_window x dimension x group_label.
with scope as (
    select
        c.snapshot_id,
        c.nct_id,
        b.mesh_id,
        b.registry_condition_key,
        (year(t.completion_date) // 5) * 5 as window_start_year
    from {{ ref('int_registry_current_trial_status') }} c
    inner join {{ ref('stg_registry_trials') }} t
        on c.snapshot_id = t.snapshot_id
        and c.nct_id = t.nct_id
    inner join {{ ref('bridge_registry_trial_condition') }} b
        on c.nct_id = b.nct_id
    where c.active_in_latest_snapshot_flag
      and t.overall_status = 'COMPLETED'
      and t.has_results_flag
      and t.completion_date is not null
      and b.mesh_id is not null
),

window_trials as (
    select
        mesh_id,
        window_start_year,
        count(distinct nct_id) as trials_in_window_count
    from scope
    group by 1, 2
),

measured as (
    select
        snapshot_id,
        nct_id,
        measure_index,
        case
            when dimension = 'sex' then 'sex'
            when resolved_dimension in ('race', 'ethnicity') then resolved_dimension
        end as dimension,
        case
            when dimension = 'sex' then case lower(trim(coalesce(category_title, class_title)))
                when 'female' then 'Female'
                when 'male' then 'Male'
            end
            when omb_category <> 'Unknown or Not Reported' then omb_category
        end as group_label,
        participant_count,
        regexp_matches(lower(trim(coalesce(group_title, ''))), '^(total|overall)\b') as is_total_group
    from {{ ref('stg_registry_trial_baseline_demographics') }}
    where param_type = 'COUNT_OF_PARTICIPANTS'
      and participant_count is not null
),

arm_scoped as (
    select *
    from measured
    where dimension is not null and group_label is not null
    qualify not is_total_group
        or not bool_or(not is_total_group) over (
            partition by snapshot_id, nct_id, measure_index
        )
),

one_measure as (
    select *
    from arm_scoped
    qualify measure_index = min(measure_index) over (
        partition by snapshot_id, nct_id, dimension
    )
),

trial_groups as (
    select
        snapshot_id,
        nct_id,
        dimension,
        group_label,
        sum(participant_count) as participants
    from one_measure
    group by 1, 2, 3, 4
),

cells as (
    select
        s.mesh_id,
        s.registry_condition_key,
        s.window_start_year,
        g.dimension,
        g.group_label,
        count(distinct s.nct_id) as group_trial_count,
        cast(sum(g.participants) as bigint) as group_participants,
        string_agg(distinct s.nct_id, ', ' order by s.nct_id) as contributing_nct_ids
    from scope s
    inner join trial_groups g
        on s.snapshot_id = g.snapshot_id
        and s.nct_id = g.nct_id
    group by 1, 2, 3, 4, 5
),

dimension_totals as (
    select
        s.mesh_id,
        s.window_start_year,
        g.dimension,
        count(distinct s.nct_id) as trials_reporting_dimension_count,
        cast(sum(g.participants) as bigint) as dimension_participants
    from scope s
    inner join trial_groups g
        on s.snapshot_id = g.snapshot_id
        and s.nct_id = g.nct_id
    group by 1, 2, 3
),

burden_shares as (
    select
        b.burden_source_id,
        b.dimension,
        b.group_label,
        b.annual_cases,
        b.period,
        b.geography,
        cast(b.annual_cases as double) / t.total_cases as burden_share
    from {{ ref('burden_demographic_share') }} b
    inner join (
        select burden_source_id, dimension, sum(annual_cases) as total_cases
        from {{ ref('burden_demographic_share') }}
        group by 1, 2
    ) t
        on b.burden_source_id = t.burden_source_id and b.dimension = t.dimension
),

node_labels as (
    select mesh_id, condition_display_name as condition_label
    from {{ ref('dim_registry_condition') }}
    where mesh_id is not null
),

joined as (
    select
        c.mesh_id,
        c.registry_condition_key,
        l.condition_label,
        c.window_start_year,
        c.window_start_year || '-' || (c.window_start_year + 4) as time_window,
        c.dimension,
        c.group_label,
        w.trials_in_window_count,
        d.trials_reporting_dimension_count,
        c.group_trial_count,
        c.group_participants,
        d.dimension_participants,
        c.contributing_nct_ids,
        m.burden_source_id,
        m.burden_source,
        m.burden_measure,
        m.burden_vintage,
        coalesce(m.denominator_available, false) and bs.burden_share is not null
            as burden_denominator_available,
        bs.burden_share,
        bs.annual_cases as burden_group_annual_cases,
        bs.geography as burden_geography,
        bs.period as burden_period,
        d.trials_reporting_dimension_count < 3 as suppressed_flag
    from cells c
    inner join dimension_totals d
        on c.mesh_id = d.mesh_id
        and c.window_start_year = d.window_start_year
        and c.dimension = d.dimension
    inner join window_trials w
        on c.mesh_id = w.mesh_id
        and c.window_start_year = w.window_start_year
    left join node_labels l
        on c.mesh_id = l.mesh_id
    left join {{ ref('condition_burden_map') }} m on c.mesh_id = m.mesh_id
    left join burden_shares bs
        on m.burden_source_id = bs.burden_source_id
        and m.denominator_available
        and c.dimension = bs.dimension
        and c.group_label = bs.group_label
),

shared as (
    select
        *,
        case when not suppressed_flag then
            {{ safe_divide('group_participants', 'dimension_participants') }}
        end as participation_share
    from joined
)

select
    {{ generate_surrogate_key([
        'mesh_id', 'time_window', 'dimension', 'group_label',
    ]) }} as ratio_key,
    mesh_id,
    registry_condition_key,
    condition_label,
    window_start_year,
    window_start_year + 4 as window_end_year,
    time_window,
    dimension,
    group_label,
    trials_in_window_count,
    trials_reporting_dimension_count,
    {{ safe_divide('trials_reporting_dimension_count', 'trials_in_window_count') }}
        as reporting_trials_share,
    group_trial_count,
    case when not suppressed_flag then group_participants end as group_participants,
    case when not suppressed_flag then dimension_participants end as dimension_participants,
    participation_share,
    case when not suppressed_flag then
        {{ wilson_ci_lower('group_participants', 'dimension_participants') }}
    end as participation_share_ci_low,
    case when not suppressed_flag then
        {{ wilson_ci_upper('group_participants', 'dimension_participants') }}
    end as participation_share_ci_high,
    case when burden_denominator_available then burden_share end as burden_share,
    case when burden_denominator_available and not suppressed_flag then
        participation_share / burden_share
    end as participation_prevalence_ratio,
    case when burden_denominator_available and not suppressed_flag then
        {{ wilson_ci_lower('group_participants', 'dimension_participants') }} / burden_share
    end as ratio_ci_low,
    case when burden_denominator_available and not suppressed_flag then
        {{ wilson_ci_upper('group_participants', 'dimension_participants') }} / burden_share
    end as ratio_ci_high,
    trials_reporting_dimension_count < 5 as low_n_flag,
    suppressed_flag,
    burden_denominator_available,
    case when burden_denominator_available
        then 'participation_vs_burden' else 'participant_composition_only'
    end as comparison_basis,
    case when burden_denominator_available then burden_source_id end as burden_source_id,
    case when burden_denominator_available then burden_source end as burden_source,
    case when burden_denominator_available then burden_measure end as burden_measure,
    case when burden_denominator_available then burden_vintage end as burden_vintage,
    case when burden_denominator_available then burden_geography end as burden_geography,
    case when burden_denominator_available then burden_group_annual_cases end
        as burden_group_annual_cases,
    case
        when burden_denominator_available then
            'Participant share divided by share of estimated cases ('
            || burden_measure || '; ' || burden_source || ', ' || burden_vintage || ', '
            || burden_geography || ')'
        else 'Participant composition only; no comparable burden denominator available'
    end as denominator_label,
    contributing_nct_ids,
    'Describes a difference between reported participation and disease burden. Not evidence '
        || 'of intent, quotas, causation, or sponsor quality; requires human review.'
        as interpretation_note
from shared
