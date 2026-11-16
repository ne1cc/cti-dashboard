{{ config(
    materialized='incremental',
    unique_key='event_id',
    full_refresh=false,
    on_schema_change='fail'
) }}
-- Change Radar at canonical registry grain.
-- One row per NCT ID x observation pair (previous trusted run, current trusted run)
-- x event_type x field. Scoped to authoritative whole-registry trusted runs via
-- int_registry_trusted_change_runs; preset consumers filter via bridge_preset_trial.
with pairs as (
    select
        cur.nct_id,
        r.previous_ingestion_run_id,
        r.ingestion_run_id,
        r.previous_observed_at_utc,
        r.observed_at_utc,
        prev.overall_status as before_status,
        cur.overall_status as after_status,
        prev.enrollment_count as before_enrollment_count,
        cur.enrollment_count as after_enrollment_count,
        prev.enrollment_type as before_enrollment_type,
        cur.enrollment_type as after_enrollment_type,
        prev.primary_completion_date as before_pcd,
        cur.primary_completion_date as after_pcd,
        prev.primary_completion_date_type as before_pcd_type,
        cur.primary_completion_date_type as after_pcd_type,
        prev.completion_date as before_cd,
        cur.completion_date as after_cd,
        prev.completion_date_type as before_cd_type,
        cur.completion_date_type as after_cd_type
    from {{ ref('stg_registry_trials') }} cur
    inner join {{ ref('int_registry_trusted_change_runs') }} r
        on cur.snapshot_id = r.ingestion_run_id
    inner join {{ ref('stg_registry_trials') }} prev
        on prev.snapshot_id = r.previous_ingestion_run_id
        and prev.nct_id = cur.nct_id
),

events as (
    select
        nct_id,
        previous_ingestion_run_id,
        ingestion_run_id,
        previous_observed_at_utc,
        observed_at_utc,
        'status_change' as event_type,
        'overall_status' as field_name,
        before_status as before_value,
        after_status as after_value,
        cast(null as varchar) as before_value_type,
        cast(null as varchar) as after_value_type
    from pairs
    where before_status is distinct from after_status

    union all
    select
        nct_id,
        previous_ingestion_run_id,
        ingestion_run_id,
        previous_observed_at_utc,
        observed_at_utc,
        'enrollment_change' as event_type,
        'enrollment_count' as field_name,
        cast(before_enrollment_count as varchar) as before_value,
        cast(after_enrollment_count as varchar) as after_value,
        before_enrollment_type as before_value_type,
        after_enrollment_type as after_value_type
    from pairs
    where before_enrollment_count is distinct from after_enrollment_count

    union all
    select
        nct_id,
        previous_ingestion_run_id,
        ingestion_run_id,
        previous_observed_at_utc,
        observed_at_utc,
        'enrollment_change' as event_type,
        'enrollment_type' as field_name,
        before_enrollment_type as before_value,
        after_enrollment_type as after_value,
        cast(null as varchar) as before_value_type,
        cast(null as varchar) as after_value_type
    from pairs
    where before_enrollment_type is distinct from after_enrollment_type

    union all
    select
        nct_id,
        previous_ingestion_run_id,
        ingestion_run_id,
        previous_observed_at_utc,
        observed_at_utc,
        'completion_date_change' as event_type,
        'primary_completion_date' as field_name,
        cast(before_pcd as varchar) as before_value,
        cast(after_pcd as varchar) as after_value,
        before_pcd_type as before_value_type,
        after_pcd_type as after_value_type
    from pairs
    where before_pcd is distinct from after_pcd
       or before_pcd_type is distinct from after_pcd_type

    union all
    select
        nct_id,
        previous_ingestion_run_id,
        ingestion_run_id,
        previous_observed_at_utc,
        observed_at_utc,
        'completion_date_change' as event_type,
        'completion_date' as field_name,
        cast(before_cd as varchar) as before_value,
        cast(after_cd as varchar) as after_value,
        before_cd_type as before_value_type,
        after_cd_type as after_value_type
    from pairs
    where before_cd is distinct from after_cd
       or before_cd_type is distinct from after_cd_type

    union all
    select
        nct_id,
        previous_ingestion_run_id,
        ingestion_run_id,
        previous_observed_at_utc,
        observed_at_utc,
        'outcome_set_change' as event_type,
        'outcome_set' as field_name,
        before_outcome_summary as before_value,
        after_outcome_summary as after_value,
        cast(null as varchar) as before_value_type,
        cast(null as varchar) as after_value_type
    from {{ ref('int_registry_trial_outcome_changes') }}
)

select
    {{ generate_surrogate_key([
        'nct_id',
        'previous_ingestion_run_id',
        'ingestion_run_id',
        'event_type',
        'field_name',
    ]) }} as event_id,
    {{ generate_surrogate_key(['nct_id']) }} as registry_trial_key,
    nct_id,
    event_type,
    field_name,
    before_value,
    after_value,
    before_value_type,
    after_value_type,
    previous_ingestion_run_id as before_ingestion_run_id,
    ingestion_run_id as after_ingestion_run_id,
    previous_observed_at_utc as before_observed_at_utc,
    observed_at_utc as after_observed_at_utc,
    'https://clinicaltrials.gov/study/' || nct_id as registry_url
from events
