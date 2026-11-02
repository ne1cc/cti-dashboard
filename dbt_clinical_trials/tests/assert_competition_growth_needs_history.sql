-- mart_recruiting_competition's windowed recruiting-transition figures must
-- be null exactly when the profile's observed history does not cover their
-- window: null means "not enough history", never a silent 0, and a covered
-- window must emit a number rather than hiding behind null. The guards are
-- re-derived here from fct_trial_snapshot (same shape as the model's
-- profile_history CTE), so deleting or weakening the model's case guards
-- turns this red. recruiting_growth_90d additionally gets the one-sided
-- form: it may be null for a legitimate extra reason (zero baseline), but it
-- must never be non-null on an uncovered window.
-- observed_history_days is re-derived here with the model's running
-- as-of-snapshot semantics (span from the earliest snapshot up to the row's
-- own date), not the profile's final retained span: on 2026-09-08 with
-- snapshots at 09-01/09-08/10-01 a profile holds 30 retained days but had
-- observed only 7 when that snapshot was captured, and the guard must gate
-- on what was observed.
with snapshot_calendar as (
    select distinct indication_profile_id, snapshot_date
    from {{ ref('fct_trial_snapshot') }}
),

profile_history as (
    select
        indication_profile_id,
        snapshot_date,
        datediff('day', min(snapshot_date) over running_window, snapshot_date)
            as observed_history_days,
        lag(snapshot_date) over running_window as previous_snapshot_date
    from snapshot_calendar
    window running_window as (
        partition by indication_profile_id order by snapshot_date
    )
),

profile_window_coverage as (
    select
        indication_profile_id,
        snapshot_date,
        observed_history_days,
        previous_snapshot_date,
        first_value(snapshot_date) over profile_30d as window_30d_earliest_date,
        first_value(previous_snapshot_date) over profile_30d as window_30d_earliest_pred_date,
        first_value(snapshot_date) over profile_90d as window_90d_earliest_date,
        first_value(previous_snapshot_date) over profile_90d as window_90d_earliest_pred_date
    from profile_history
    window
        profile_30d as (
            partition by indication_profile_id order by snapshot_date
            range between interval 30 day preceding and current row
        ),
        profile_90d as (
            partition by indication_profile_id order by snapshot_date
            range between interval 90 day preceding and current row
        )
),

coverage_flags as (
    select
        indication_profile_id,
        snapshot_date,
        observed_history_days,
        previous_snapshot_date,
        (
            observed_history_days >= 30
            and window_30d_earliest_date <= snapshot_date - interval 30 day + interval 7 day
            and (
                window_30d_earliest_pred_date is null
                or window_30d_earliest_pred_date >= snapshot_date - interval 30 day - interval 7 day
            )
            and coalesce(previous_snapshot_date >= snapshot_date - interval 30 day, false)
        ) as is_30d_covered,
        (
            observed_history_days >= 90
            and window_90d_earliest_date <= snapshot_date - interval 90 day + interval 7 day
            and (
                window_90d_earliest_pred_date is null
                or window_90d_earliest_pred_date >= snapshot_date - interval 90 day - interval 7 day
            )
            and coalesce(previous_snapshot_date >= snapshot_date - interval 90 day, false)
        ) as is_90d_covered
    from profile_window_coverage
)

select
    c.indication_profile_id,
    c.snapshot_date,
    c.condition_group,
    c.state_normalized,
    c.phase_normalized,
    h.observed_history_days,
    h.previous_snapshot_date,
    c.new_recruiting_30d,
    c.new_recruiting_90d,
    c.recruiting_growth_90d
from {{ ref('mart_recruiting_competition') }} c
inner join coverage_flags h
    on h.indication_profile_id = c.indication_profile_id
    and h.snapshot_date = c.snapshot_date
where h.is_30d_covered is distinct from (c.new_recruiting_30d is not null)
   or h.is_90d_covered is distinct from (c.new_recruiting_90d is not null)
   or (
        c.recruiting_growth_90d is not null
        and not h.is_90d_covered
      )
