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
with profile_history as (
    select
        indication_profile_id,
        snapshot_date,
        datediff('day', min(snapshot_date) over running_window, snapshot_date)
            as observed_history_days
    from (
        select distinct indication_profile_id, snapshot_date
        from {{ ref('fct_trial_snapshot') }}
    )
    window running_window as (
        partition by indication_profile_id order by snapshot_date
    )
),

previous_snapshot as (
    select
        indication_profile_id,
        snapshot_date,
        lag(snapshot_date) over previous_window as previous_snapshot_date
    from (
        select distinct indication_profile_id, snapshot_date
        from {{ ref('fct_trial_snapshot') }}
    )
    window previous_window as (
        partition by indication_profile_id order by snapshot_date
    )
)

select
    c.indication_profile_id,
    c.snapshot_date,
    c.condition_group,
    c.state_normalized,
    c.phase_normalized,
    h.observed_history_days,
    p.previous_snapshot_date,
    c.new_recruiting_30d,
    c.new_recruiting_90d,
    c.recruiting_growth_90d
from {{ ref('mart_recruiting_competition') }} c
inner join profile_history h
    on h.indication_profile_id = c.indication_profile_id
    and h.snapshot_date = c.snapshot_date
inner join previous_snapshot p
    on p.indication_profile_id = c.indication_profile_id
    and p.snapshot_date = c.snapshot_date
where (
        h.observed_history_days >= 30
        and coalesce(
            p.previous_snapshot_date >= c.snapshot_date - interval 30 day, false
        )
      ) is distinct from (c.new_recruiting_30d is not null)
   or (
        h.observed_history_days >= 90
        and coalesce(
            p.previous_snapshot_date >= c.snapshot_date - interval 90 day, false
        )
      ) is distinct from (c.new_recruiting_90d is not null)
   or (
        c.recruiting_growth_90d is not null
        and not (
            h.observed_history_days >= 90
            and coalesce(
                p.previous_snapshot_date >= c.snapshot_date - interval 90 day, false
            )
        )
      )
