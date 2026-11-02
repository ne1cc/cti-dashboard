-- mart_condition_geography_trends.recruiting_growth_3m is only meaningful
-- when the trailing 3-calendar-month window actually spans three months:
-- the earliest observed month inside the window must sit at least three
-- months back, and the segment's baseline snapshot date must be at least
-- 3 calendar months back from the latest snapshot date. The superseded
-- two-month guard (assert_trends_growth_needs_two_months.sql) stays as history;
-- this test pins the stricter rule at the segment grain matching the mart.
-- The window is re-derived from the mart's own inputs rather than read from a
-- mart-emitted helper, so weakening the model's guard turns this red.
with segment_month_snapshots as (
    select
        indication_profile_id,
        condition_group,
        state_normalized,
        date_trunc('month', snapshot_date) as activity_month,
        max(snapshot_date) as latest_snapshot_date
    from {{ ref('int_condition_geography_activity') }}
    group by 1, 2, 3, 4
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
    inner join segment_month_snapshots m
        on m.indication_profile_id = t.indication_profile_id
        and m.condition_group = t.condition_group
        and coalesce(m.state_normalized, '') = coalesce(t.state_normalized, '')
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
        or baseline_snapshot_date > latest_snapshot_date - interval 3 month
        or observed_history_days < 90
    )
