-- mart_condition_geography_trends.recruiting_growth_3m is only meaningful
-- when the trailing 3-calendar-month window actually spans three months:
-- the earliest observed month inside the window must sit at least three
-- months back. The superseded two-month guard (assert_trends_growth_needs_
-- two_months.sql) stays as history; this test pins the stricter rule. The
-- window is re-derived from the mart's own columns rather than read from a
-- mart-emitted helper, so weakening the model's guard turns this red.
with month_snapshots as (
    select
        indication_profile_id,
        date_trunc('month', snapshot_date) as activity_month,
        max(snapshot_date) as latest_snapshot_date
    from {{ ref('fct_trial_snapshot') }}
    group by 1, 2
),
windowed as (
    select
        t.indication_profile_id,
        t.condition_group,
        t.state_normalized,
        t.activity_month,
        t.recruiting_growth_3m,
        t.observed_history_days,
        m.latest_snapshot_date,
        min(t.activity_month) over trailing_3m as window_first_month,
        first_value(m.latest_snapshot_date) over trailing_3m as baseline_snapshot_date
    from {{ ref('mart_condition_geography_trends') }} t
    inner join month_snapshots m
        on m.indication_profile_id = t.indication_profile_id
        and m.activity_month = t.activity_month
    window trailing_3m as (
        partition by t.indication_profile_id, t.condition_group, t.state_normalized
        order by t.activity_month
        range between interval 3 months preceding and current row
    )
)
select
    indication_profile_id,
    condition_group,
    state_normalized,
    activity_month,
    window_first_month,
    observed_history_days,
    baseline_snapshot_date,
    latest_snapshot_date,
    recruiting_growth_3m
from windowed
where recruiting_growth_3m is not null
    and (
        datediff('month', window_first_month, activity_month) < 3
        or datediff('day', baseline_snapshot_date, latest_snapshot_date) < 84
        or observed_history_days < 90
    )
