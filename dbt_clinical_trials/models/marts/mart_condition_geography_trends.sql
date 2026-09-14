-- Monthly condition x geography trend view, per indication profile.
-- Grain: profile x month (snapshot month) x condition_group x state.
-- Built from this project's snapshots; with a single snapshot the series
-- has one month. recruiting_growth_3m is then null, not 0.0: on a one-month
-- window first_value equals the row, so an ungarded (x - x) / x would read
-- "flat" for a quantity that is unknown. It is emitted only when the window
-- holds at least two distinct activity_months (guarded by months_in_window).
-- The 3-month mean is a different case: a one-point mean is honest, so
-- recruiting_trial_count_3m_avg keeps its value. Registry first-post month is
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
            as recruiting_count_3m_baseline,
        count(distinct activity_month) over trailing_3m
            as months_in_window
    from monthly
    window trailing_3m as (
        partition by indication_profile_id, condition_group, state_normalized
        order by activity_month
        range between interval 3 months preceding and current row
    )
)

select
    indication_profile_id,
    activity_month,
    condition_group,
    state_normalized,
    trial_count,
    recruiting_trial_count,
    newly_posted_in_month_proxy,
    sponsor_count,
    recruiting_trial_count_3m_avg,
    recruiting_count_3m_baseline,
    case
        when months_in_window >= 2 then {{ safe_divide(
            'recruiting_trial_count - recruiting_count_3m_baseline',
            'recruiting_count_3m_baseline',
        ) }}
        else null
    end as recruiting_growth_3m
from windowed
