-- mart_condition_geography_trends.recruiting_growth_3m is only meaningful when
-- its trailing 3-month window actually spans at least two distinct snapshot
-- months. On a single-month warehouse the window's first_value equals the row,
-- so safe_divide answers (x - x) / x = 0.0 -- "recruiting is flat" -- for a
-- quantity that is genuinely unknown. The mart nulls it (see its header); this
-- test pins that no non-null growth survives on a one-month window. The window
-- is re-derived here from the mart's own columns, not read from a mart-emitted
-- helper, so removing the nulling from the model turns this red.
select
    indication_profile_id,
    condition_group,
    state_normalized,
    activity_month,
    recruiting_growth_3m,
    months_in_window
from (
    select
        indication_profile_id,
        condition_group,
        state_normalized,
        activity_month,
        recruiting_growth_3m,
        count(distinct activity_month) over trailing_3m as months_in_window
    from {{ ref('mart_condition_geography_trends') }}
    window trailing_3m as (
        partition by indication_profile_id, condition_group, state_normalized
        order by activity_month
        range between interval 3 months preceding and current row
    )
)
where recruiting_growth_3m is not null
    and months_in_window < 2
