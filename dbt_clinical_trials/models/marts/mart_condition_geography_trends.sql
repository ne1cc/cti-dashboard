-- Monthly condition x geography trend view, per indication profile.
-- Grain: profile x month (snapshot month) x condition_group x state.
-- Built from this project's snapshots. recruiting_growth_3m is null unless
-- the trailing window's earliest observed month sits at least three months
-- back: on a shorter window the figure would be labelled "3-month" while
-- covering less than that, and on a single-month window an unguarded
-- (x - x) / x would read "flat" for a quantity that is unknown. Null means
-- "not enough history"; zero is reserved for a covered window with no
-- change. The 3-month mean is a different case: a one-point mean is honest,
-- so recruiting_trial_count_3m_avg keeps its value. observed_history_days
-- is the profile's retained day span and travels with the growth figure.
-- newly_registered_in_month counts registry first-post months: a
-- registration count, not a recruiting transition. The rolling baseline is
-- partitioned by profile: unscoped, one profile's seasonality would blend
-- into another's.
with activity as (
    select * from {{ ref('int_condition_geography_activity') }}
),

-- observed_history_days uses as-of semantics per month row: the span from
-- the earliest snapshot up to the latest snapshot observed in or before that
-- month, so an old row is quantified by what the project had captured then,
-- not by the warehouse's final depth.
snapshot_calendar as (
    select distinct
        indication_profile_id,
        snapshot_date,
        date_trunc('month', snapshot_date) as snapshot_month
    from {{ ref('fct_trial_snapshot') }}
),

month_history as (
    select
        indication_profile_id,
        snapshot_month as activity_month,
        datediff('day', min(first_observed), max(latest_observed))
            as observed_history_days
    from (
        select
            indication_profile_id,
            snapshot_month,
            min(snapshot_date) over running_history as first_observed,
            max(snapshot_date) over running_history as latest_observed
        from snapshot_calendar
        window running_history as (
            partition by indication_profile_id order by snapshot_month
        )
    )
    group by 1, 2
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
        ) as newly_registered_in_month,
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
        min(activity_month) over trailing_3m as window_first_month
    from monthly
    window trailing_3m as (
        partition by indication_profile_id, condition_group, state_normalized
        order by activity_month
        range between interval 3 months preceding and current row
    )
)

select
    w.indication_profile_id,
    w.activity_month,
    w.condition_group,
    w.state_normalized,
    w.trial_count,
    w.recruiting_trial_count,
    w.newly_registered_in_month,
    w.sponsor_count,
    w.recruiting_trial_count_3m_avg,
    w.recruiting_count_3m_baseline,
    h.observed_history_days,
    case
        when datediff('month', w.window_first_month, w.activity_month) >= 3
            and h.observed_history_days >= 90
            then {{ safe_divide(
                'w.recruiting_trial_count - w.recruiting_count_3m_baseline',
                'w.recruiting_count_3m_baseline',
            ) }}
        else null
    end as recruiting_growth_3m
from windowed w
inner join month_history h
    on h.indication_profile_id = w.indication_profile_id
    and h.activity_month = w.activity_month
