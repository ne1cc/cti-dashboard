-- Results posting transparency mart: tracking posted results vs primary completion
-- dates under a conservative 12-month overdue heuristic.
--
-- Informational reporting benchmark only: legal applicability under FDAAA 801
-- ("applicable clinical trials") cannot be fully derived from public fields.
-- Includes 95% Wilson score confidence intervals and flags sponsors with
-- small sample sizes (n < 5) as unranked (Issue #76).
with trial_scope as (
    select
        d.trial_key,
        d.nct_id,
        d.indication_profile_id,
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
    from {{ ref('dim_trial') }} d
    left join {{ ref('bridge_trial_sponsor') }} s
        on d.trial_key = s.trial_key and s.lead_sponsor_flag
),

grouped as (
    select
        indication_profile_id,
        sponsor_class,
        lead_sponsor,
        count(distinct case when overall_status = 'COMPLETED' then nct_id end) as total_completed_trials,
        count(distinct case when overall_status = 'TERMINATED' then nct_id end) as total_terminated_trials,
        count(distinct case when is_past_12m_pcd then nct_id end) as total_evaluable_trials,
        count(distinct case when is_past_12m_pcd and has_results_flag then nct_id end) as results_posted_evaluable_count,
        count(distinct case when is_past_12m_pcd and not has_results_flag then nct_id end) as results_overdue_count,
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
    from trial_scope
    group by
        indication_profile_id,
        sponsor_class,
        lead_sponsor
)

select
    {{ generate_surrogate_key([
        'indication_profile_id',
        'sponsor_class',
        'lead_sponsor',
    ]) }} as results_transparency_key,
    indication_profile_id,
    sponsor_class,
    lead_sponsor,
    total_completed_trials,
    total_terminated_trials,
    total_evaluable_trials,
    results_posted_evaluable_count,
    results_overdue_count,
    {{ safe_divide('results_posted_evaluable_count', 'total_evaluable_trials') }} as results_posted_rate,
    {{ wilson_ci_lower('results_posted_evaluable_count', 'total_evaluable_trials') }} as ci_lower,
    {{ wilson_ci_upper('results_posted_evaluable_count', 'total_evaluable_trials') }} as ci_upper,
    {{ safe_divide('results_overdue_count', 'total_evaluable_trials') }} as results_overdue_rate,
    (total_evaluable_trials >= 5) as is_ranked,
    evaluable_nct_ids,
    contributing_nct_ids,
    overdue_nct_ids,
    posted_nct_ids
from grouped
