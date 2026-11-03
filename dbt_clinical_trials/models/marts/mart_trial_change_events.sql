{{ config(
    materialized='incremental', unique_key='event_id',
    full_refresh=false, on_schema_change='fail'
) }}
with pairs as (
    select
        cur.indication_profile_id, cur.nct_id,
        r.previous_ingestion_run_id, r.ingestion_run_id,
        r.previous_observed_at_utc, r.observed_at_utc,
        prev.overall_status as before_status, cur.overall_status as after_status,
        prev.enrollment_count as before_enrollment_count,
        cur.enrollment_count as after_enrollment_count,
        prev.enrollment_type as before_enrollment_type,
        cur.enrollment_type as after_enrollment_type,
        prev.primary_completion_date as before_pcd, cur.primary_completion_date as after_pcd,
        prev.primary_completion_date_type as before_pcd_type,
        cur.primary_completion_date_type as after_pcd_type,
        prev.completion_date as before_cd, cur.completion_date as after_cd,
        prev.completion_date_type as before_cd_type, cur.completion_date_type as after_cd_type
    from {{ ref('stg_trials') }} cur
    join {{ ref('int_trusted_change_runs') }} r
        on cur.ingestion_run_id = r.ingestion_run_id
        and cur.indication_profile_id = r.indication_profile_id
    join {{ ref('stg_trials') }} prev
        on prev.ingestion_run_id = r.previous_ingestion_run_id
        and prev.indication_profile_id = cur.indication_profile_id
        and prev.nct_id = cur.nct_id
),

events as (
    -- status: before comes from the gated previous trusted run (same value as
    -- int_trial_status_history.previous_status whenever no untrusted run sits between)
    select indication_profile_id, nct_id, previous_ingestion_run_id, ingestion_run_id,
        previous_observed_at_utc, observed_at_utc,
        'status_change' as event_type, 'overall_status' as field_name,
        before_status as before_value, after_status as after_value,
        cast(null as varchar) as before_value_type, cast(null as varchar) as after_value_type
    from pairs where before_status is distinct from after_status

    union all
    select indication_profile_id, nct_id, previous_ingestion_run_id, ingestion_run_id,
        previous_observed_at_utc, observed_at_utc,
        'enrollment_change', 'enrollment_count',
        cast(before_enrollment_count as varchar), cast(after_enrollment_count as varchar),
        before_enrollment_type, after_enrollment_type
    from pairs where before_enrollment_count is distinct from after_enrollment_count

    union all
    select indication_profile_id, nct_id, previous_ingestion_run_id, ingestion_run_id,
        previous_observed_at_utc, observed_at_utc,
        'enrollment_change', 'enrollment_type',
        before_enrollment_type, after_enrollment_type, null, null
    from pairs where before_enrollment_type is distinct from after_enrollment_type

    union all
    select indication_profile_id, nct_id, previous_ingestion_run_id, ingestion_run_id,
        previous_observed_at_utc, observed_at_utc,
        'completion_date_change', 'primary_completion_date',
        cast(before_pcd as varchar), cast(after_pcd as varchar), before_pcd_type, after_pcd_type
    from pairs
    where before_pcd is distinct from after_pcd or before_pcd_type is distinct from after_pcd_type

    union all
    select indication_profile_id, nct_id, previous_ingestion_run_id, ingestion_run_id,
        previous_observed_at_utc, observed_at_utc,
        'completion_date_change', 'completion_date',
        cast(before_cd as varchar), cast(after_cd as varchar), before_cd_type, after_cd_type
    from pairs
    where before_cd is distinct from after_cd or before_cd_type is distinct from after_cd_type

    union all
    select indication_profile_id, nct_id, previous_ingestion_run_id, ingestion_run_id,
        previous_observed_at_utc, observed_at_utc,
        'outcome_set_change', 'outcome_set',
        before_outcome_summary, after_outcome_summary, null, null
    from {{ ref('int_trial_outcome_changes') }}
)

select
    {{ generate_surrogate_key([
        'indication_profile_id', 'nct_id', 'previous_ingestion_run_id',
        'ingestion_run_id', 'event_type', 'field_name'
    ]) }} as event_id,
    indication_profile_id, nct_id, event_type, field_name,
    before_value, after_value, before_value_type, after_value_type,
    previous_ingestion_run_id as before_ingestion_run_id,
    ingestion_run_id as after_ingestion_run_id,
    previous_observed_at_utc as before_observed_at_utc,
    observed_at_utc as after_observed_at_utc,
    'https://clinicaltrials.gov/study/' || nct_id as registry_url
from events
