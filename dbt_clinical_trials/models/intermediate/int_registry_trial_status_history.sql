-- Longitudinal status history constructed from canonical whole-registry
-- snapshots (the registry API serves only current records).
-- Grain: one canonical observation per NCT ID per complete snapshot date.
-- Uses approved version clock (started_at_utc desc, snapshot_id asc) before
-- any condition or site membership expansion.
with complete_snapshots as (
    select
        ingestion_run_id as snapshot_id,
        started_at_utc,
        snapshot_date
    from {{ ref('stg_registry_trial_snapshots') }}
    where status = 'success'
      and profile in ('full_catalog', 'full-catalog', 'default')
      and (condition is null or trim(condition) = '')
),

trials as (
    select
        t.* exclude (snapshot_date),
        s.snapshot_date,
        s.started_at_utc
    from {{ ref('stg_registry_trials') }} t
    inner join complete_snapshots s
        on t.snapshot_id = s.snapshot_id
    qualify row_number() over (
        partition by t.nct_id, s.snapshot_date
        order by s.started_at_utc desc, t.snapshot_id asc
    ) = 1
)

select
    registry_trial_key,
    nct_id,
    snapshot_id,
    ingestion_run_id,
    snapshot_date,
    snapshot_timestamp_utc,
    started_at_utc,
    overall_status,
    phase_normalized,
    study_type,
    enrollment_count,
    lead_sponsor_name,
    lead_sponsor_normalized,
    has_results_flag,
    study_first_post_date,
    record_quality_flag,
    source_json_hash,
    lag(snapshot_date) over trial_window as previous_snapshot_date,
    lag(overall_status) over trial_window as previous_status,
    (
        lag(overall_status) over trial_window is not null
        and lag(overall_status) over trial_window is distinct from overall_status
    ) as status_changed_from_previous_snapshot_flag,
    (
        overall_status = 'RECRUITING'
        and lag(overall_status) over trial_window is not null
        and lag(overall_status) over trial_window != 'RECRUITING'
    ) as entered_recruiting_flag,
    (
        overall_status != 'RECRUITING'
        and lag(overall_status) over trial_window = 'RECRUITING'
    ) as left_recruiting_flag,
    min(snapshot_date) over trial_partition as first_seen_snapshot_date,
    datediff('day', min(snapshot_date) over trial_partition, snapshot_date)
        as days_since_first_seen
from trials
window
    trial_window as (
        partition by nct_id order by snapshot_date
    ),
    trial_partition as (
        partition by nct_id
    )
