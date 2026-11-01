-- mart_condition_geography_trends.recruiting_growth_3m is only meaningful
-- when the trailing 3-calendar-month window actually spans three months:
-- the earliest observed month inside the window must sit at least three
-- months back. The superseded two-month guard (assert_trends_growth_needs_
-- two_months.sql) stays as history; this test pins the stricter rule. The
-- window is re-derived from the mart's own columns rather than read from a
-- mart-emitted helper, so weakening the model's guard turns this red.
with windowed as (
    select
        indication_profile_id,
        condition_group,
        state_normalized,
        activity_month,
        recruiting_growth_3m,
        min(activity_month) over trailing_3m as window_first_month
    from {{ ref('mart_condition_geography_trends') }}
    window trailing_3m as (
        partition by indication_profile_id, condition_group, state_normalized
        order by activity_month
        range between interval 3 months preceding and current row
    )
)
select
    indication_profile_id,
    condition_group,
    state_normalized,
    activity_month,
    window_first_month,
    recruiting_growth_3m
from windowed
where recruiting_growth_3m is not null
    and datediff('month', window_first_month, activity_month) < 3
