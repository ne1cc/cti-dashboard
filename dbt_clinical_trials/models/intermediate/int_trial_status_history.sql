-- Longitudinal status history constructed from this project's own snapshots
-- (the registry API serves only current records). One row per profile per NCT
-- ID per complete (status='success') snapshot date; latest run wins within a
-- date *within a profile*. Two profiles listing the same trial on the same day
-- are two observations of the same registry record through two query lenses —
-- not a duplicate to be resolved.
with complete_snapshots as (
    select ingestion_run_id, snapshot_date, indication_profile_id
    from {{ ref('stg_trial_snapshots') }}
    where status = 'success'
),

trials as (
    select t.*
    from {{ ref('stg_trials') }} t
    inner join complete_snapshots s
        using (ingestion_run_id, indication_profile_id)
    qualify row_number() over (
        partition by t.indication_profile_id, t.nct_id, t.snapshot_date
        order by t.snapshot_timestamp_utc desc
    ) = 1
)

select
    nct_id,
    indication_profile_id,
    ingestion_run_id,
    snapshot_date,
    snapshot_timestamp_utc,
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
        partition by indication_profile_id, nct_id order by snapshot_date
    ),
    trial_partition as (
        partition by indication_profile_id, nct_id
    )
