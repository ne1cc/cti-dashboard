-- Results posting transparency mart over canonical registry observations.
-- Grain: preset_id x sponsor_class x lead_sponsor.
-- Preset denominators come from bridge_preset_trial membership (excluding unassigned);
-- source-wide sponsor totals across the entire canonical registry (including
-- unassigned and overlapping-preset trials counted once) are preserved as separate fields.
with lead_sponsors as (
    select
        snapshot_id,
        nct_id,
        min(sponsor_class) as sponsor_class,
        min(sponsor_name) as sponsor_name
    from {{ ref('stg_registry_trial_sponsors') }}
    where lower(sponsor_role) = 'lead_sponsor'
      and sponsor_normalized is not null
    group by 1, 2
),

canonical_trials as (
    select
        d.registry_trial_key,
        d.nct_id,
        d.snapshot_id,
        d.current_overall_status as overall_status,
        d.current_has_results_flag as has_results_flag,
        d.primary_completion_date,
        d.completion_date,
        d.latest_seen_snapshot_date,
        coalesce(s.sponsor_class, 'UNKNOWN') as sponsor_class,
        coalesce(d.current_lead_sponsor, s.sponsor_name, 'Unknown sponsor') as lead_sponsor,
        case
            when d.primary_completion_date is not null
                 and datediff('day', d.primary_completion_date, d.latest_seen_snapshot_date) > 365
                 and d.current_overall_status in ('COMPLETED', 'TERMINATED')
            then true
            else false
        end as is_past_12m_pcd
    from {{ ref('dim_registry_trial') }} d
    left join lead_sponsors s
        on d.snapshot_id = s.snapshot_id
        and d.nct_id = s.nct_id
),

source_sponsor_totals as (
    select
        sponsor_class,
        lead_sponsor,
        count(distinct case when overall_status = 'COMPLETED' then nct_id end)
            as source_total_completed_trials,
        count(distinct case when overall_status = 'TERMINATED' then nct_id end)
            as source_total_terminated_trials,
        count(distinct case when is_past_12m_pcd then nct_id end)
            as source_total_evaluable_trials,
        count(distinct case when is_past_12m_pcd and has_results_flag then nct_id end)
            as source_results_posted_evaluable_count,
        count(distinct case when is_past_12m_pcd and not has_results_flag then nct_id end)
            as source_results_overdue_count
    from canonical_trials
    group by 1, 2
),

preset_trial_scope as (
    select
        b.preset_id,
        c.*
    from canonical_trials c
    inner join {{ ref('bridge_preset_trial') }} b
        on c.nct_id = b.nct_id
        and c.snapshot_id = b.snapshot_id
        and b.current_record_flag
        and not b.is_unassigned
),

grouped as (
    select
        preset_id,
        sponsor_class,
        lead_sponsor,
        count(distinct case when overall_status = 'COMPLETED' then nct_id end)
            as total_completed_trials,
        count(distinct case when overall_status = 'TERMINATED' then nct_id end)
            as total_terminated_trials,
        count(distinct case when is_past_12m_pcd then nct_id end)
            as total_evaluable_trials,
        count(distinct case when is_past_12m_pcd and has_results_flag then nct_id end)
            as results_posted_evaluable_count,
        count(distinct case when is_past_12m_pcd and not has_results_flag then nct_id end)
            as results_overdue_count,
        string_agg(distinct nct_id, ', ' order by nct_id) as contributing_nct_ids,
        string_agg(
            distinct case when is_past_12m_pcd then nct_id end,
            ', '
            order by case when is_past_12m_pcd then nct_id end
        ) as evaluable_nct_ids,
        string_agg(
            distinct case when is_past_12m_pcd and not has_results_flag then nct_id end,
            ', '
            order by case when is_past_12m_pcd and not has_results_flag then nct_id end
        ) as overdue_nct_ids,
        string_agg(
            distinct case when is_past_12m_pcd and has_results_flag then nct_id end,
            ', '
            order by case when is_past_12m_pcd and has_results_flag then nct_id end
        ) as posted_nct_ids
    from preset_trial_scope
    group by 1, 2, 3
)

select
    {{ generate_surrogate_key([
        'g.preset_id',
        'g.sponsor_class',
        'g.lead_sponsor',
    ]) }} as results_transparency_key,
    g.preset_id,
    g.sponsor_class,
    g.lead_sponsor,
    g.total_completed_trials,
    g.total_terminated_trials,
    g.total_evaluable_trials,
    g.results_posted_evaluable_count,
    g.results_overdue_count,
    {{ safe_divide('g.results_posted_evaluable_count', 'g.total_evaluable_trials') }}
        as results_posted_rate,
    {{ wilson_ci_lower('g.results_posted_evaluable_count', 'g.total_evaluable_trials') }}
        as ci_lower,
    {{ wilson_ci_upper('g.results_posted_evaluable_count', 'g.total_evaluable_trials') }}
        as ci_upper,
    {{ safe_divide('g.results_overdue_count', 'g.total_evaluable_trials') }}
        as results_overdue_rate,
    (g.total_evaluable_trials >= 5) as is_ranked,
    ss.source_total_completed_trials,
    ss.source_total_terminated_trials,
    ss.source_total_evaluable_trials,
    ss.source_results_posted_evaluable_count,
    ss.source_results_overdue_count,
    g.evaluable_nct_ids,
    g.contributing_nct_ids,
    g.overdue_nct_ids,
    g.posted_nct_ids
from grouped g
inner join source_sponsor_totals ss
    on g.sponsor_class = ss.sponsor_class
    and g.lead_sponsor = ss.lead_sponsor
