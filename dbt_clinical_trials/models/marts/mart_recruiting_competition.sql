-- Potential competition signal per recruiting segment, per indication profile.
-- Grain: profile x snapshot_date x condition_group x state x phase.
-- Density proxy = count of RECRUITING registry listings; this is a
-- potential competition signal, not a recruitment forecast and not a
-- measure of patient availability.
-- Snapshot-transition metrics (new_recruiting_*) stay 0 until this
-- project accrues multi-snapshot history; newly_posted_90d_proxy uses the
-- registry study_first_post_date as an interim proxy.
-- density_percentile and competition_signal_band are distributions *within
-- one profile's* segments: partitioned by snapshot_date alone, an ADRD
-- segment would be ranked against NSCLC's segment sizes.
with activity as (
    select * from {{ ref('int_condition_geography_activity') }}
    where overall_status = 'RECRUITING'
),

segments as (
    select
        indication_profile_id,
        snapshot_date,
        condition_group,
        state_normalized,
        phase_normalized,
        count(distinct nct_id) as recruiting_trial_count,
        sum(listed_site_count_in_state) as listed_site_count,
        count(distinct nct_id) filter (entered_recruiting_flag)
            as entered_recruiting_count,
        count(distinct nct_id) filter (
            study_first_post_date is not null
            and study_first_post_date >= snapshot_date - interval 90 day
        ) as newly_posted_90d_proxy
    from activity
    group by 1, 2, 3, 4, 5
),

windowed as (
    select
        *,
        sum(entered_recruiting_count) over segment_30d as new_recruiting_30d,
        sum(entered_recruiting_count) over segment_90d as new_recruiting_90d,
        first_value(recruiting_trial_count) over segment_90d
            as recruiting_count_90d_baseline
    from segments
    window
        segment_30d as (
            partition by indication_profile_id, condition_group, state_normalized, phase_normalized
            order by snapshot_date
            range between interval 30 days preceding and current row
        ),
        segment_90d as (
            partition by indication_profile_id, condition_group, state_normalized, phase_normalized
            order by snapshot_date
            range between interval 90 days preceding and current row
        )
),

with_concentration as (
    select
        w.*,
        {{ safe_divide(
            'w.recruiting_trial_count - w.recruiting_count_90d_baseline',
            'w.recruiting_count_90d_baseline',
        ) }} as recruiting_growth_90d,
        sc.sponsor_count,
        sc.top_sponsor_share,
        sc.sponsor_hhi
    from windowed w
    left join {{ ref('int_sponsor_concentration') }} sc
        using (
            snapshot_date, indication_profile_id, condition_group,
            state_normalized, phase_normalized
        )
)

select
    *,
    percent_rank() over (
        partition by indication_profile_id, snapshot_date
        order by recruiting_trial_count
    ) as density_percentile,
    case
        when percent_rank() over (
            partition by indication_profile_id, snapshot_date
            order by recruiting_trial_count
        ) < 0.5 then 'low'
        when percent_rank() over (
            partition by indication_profile_id, snapshot_date
            order by recruiting_trial_count
        ) < 0.8 then 'moderate'
        else 'elevated'
    end as competition_signal_band
from with_concentration
