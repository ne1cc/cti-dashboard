-- Monthly condition x geography trend view, per indication profile.
-- Grain: profile x month (snapshot month) x condition_group x state.
-- Built from this project's snapshots; with a single snapshot the series
-- has one month and rolling growth is null. Registry first-post month is
-- included as a longer historical proxy for when trials were registered.
-- The rolling baseline is partitioned by profile: unscoped, one profile's
-- seasonality would blend into another's and recruiting_growth_3m in the
-- final select would be uninterpretable.
with activity as (
    select * from {{ ref('int_condition_geography_activity') }}
),

monthly as (
    select
        indication_profile_id,
        date_trunc('month', snapshot_date) as activity_month,
        condition_group,
        state_normalized,
        count(distinct nct_id) as trial_count,
        count(distinct nct_id) filter (overall_status = 'RECRUITING')
            as recruiting_trial_count,
        count(distinct nct_id) filter (
            study_first_post_date is not null
            and date_trunc('month', study_first_post_date)
                = date_trunc('month', snapshot_date)
        ) as newly_posted_in_month_proxy,
        count(distinct lead_sponsor_normalized) as sponsor_count
    from activity
    group by 1, 2, 3, 4
),

windowed as (
    select
        *,
        avg(recruiting_trial_count) over trailing_3m
            as recruiting_trial_count_3m_avg,
        first_value(recruiting_trial_count) over trailing_3m
            as recruiting_count_3m_baseline
    from monthly
    window trailing_3m as (
        partition by indication_profile_id, condition_group, state_normalized
        order by activity_month
        range between interval 3 months preceding and current row
    )
)

select
    *,
    {{ safe_divide(
        'recruiting_trial_count - recruiting_count_3m_baseline',
        'recruiting_count_3m_baseline',
    ) }} as recruiting_growth_3m
from windowed
