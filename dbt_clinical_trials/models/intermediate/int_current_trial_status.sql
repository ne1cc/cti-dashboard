-- One row per profile per NCT ID: the latest snapshot in which the trial
-- appears in that profile, plus whether that is the latest snapshot *for that
-- profile*. A trial's ADRD row may be current while its NSCLC row is stale —
-- the profiles refresh independently and one may be capped or skipped.
with history as (
    select * from {{ ref('int_trial_status_history') }}
),

latest_per_trial as (
    select *
    from history
    qualify row_number() over (
        partition by indication_profile_id, nct_id order by snapshot_date desc
    ) = 1
),

latest_per_profile as (
    select indication_profile_id, max(snapshot_date) as latest_snapshot_date
    from history
    group by 1
)

select
    t.*,
    p.latest_snapshot_date,
    (t.snapshot_date = p.latest_snapshot_date) as active_in_latest_snapshot_flag
from latest_per_trial t
inner join latest_per_profile p using (indication_profile_id)
