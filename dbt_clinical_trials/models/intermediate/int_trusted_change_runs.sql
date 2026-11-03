-- Run-trust gate for the Change Radar. A run may produce change events only if
-- (1) its manifest status is 'success' (complete snapshot) and (2) it was newly
-- fetched: its latest page retrieval is not earlier than the run's own start.
-- A re-used earlier snapshot carries an older retrieved_at_utc. Runs with no
-- retrieval stamp (legacy pages) pass condition (2) because it cannot be tested.
-- previous_ingestion_run_id is the prior *trusted* run of the same profile.
with run_retrieval as (
    select ingestion_run_id, indication_profile_id, max(retrieved_at_utc) as retrieved_at_utc
    from {{ ref('stg_trials') }}
    group by 1, 2
),

trusted as (
    select
        s.ingestion_run_id,
        s.indication_profile_id,
        s.started_at_utc as observed_at_utc
    from {{ ref('stg_trial_snapshots') }} s
    left join run_retrieval r
        on s.ingestion_run_id = r.ingestion_run_id
        and s.indication_profile_id = r.indication_profile_id
    where s.status = 'success'
        and (r.retrieved_at_utc is null or r.retrieved_at_utc >= s.started_at_utc)
)

select
    *,
    lag(ingestion_run_id) over run_order as previous_ingestion_run_id,
    lag(observed_at_utc) over run_order as previous_observed_at_utc
from trusted
window run_order as (partition by indication_profile_id order by observed_at_utc, ingestion_run_id)
