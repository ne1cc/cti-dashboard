-- One row per NCT ID across the whole registry: the latest canonical
-- observation for each trial, plus whether it appeared in the latest
-- authoritative whole-registry snapshot date.
with history as (
    select * from {{ ref('int_registry_trial_status_history') }}
),

latest_per_trial as (
    select *
    from history
    qualify row_number() over (
        partition by nct_id order by snapshot_date desc
    ) = 1
),

global_max_snapshot as (
    select max(snapshot_date) as latest_snapshot_date
    from {{ ref('stg_registry_trial_snapshots') }}
    where status = 'success'
      and profile in ('full_catalog', 'full-catalog', 'default')
      and (condition is null or trim(condition) = '')
)

select
    t.*,
    g.latest_snapshot_date,
    (t.snapshot_date = g.latest_snapshot_date) as active_in_latest_snapshot_flag
from latest_per_trial t
cross join global_max_snapshot g
