-- One row per NCT ID across the whole registry (canonical registry grain).
-- Grain: nct_id. Primary key: registry_trial_key (md5 of nct_id).
-- Follows approved version clock: source-manifest started_at_utc (UTC).
-- Only complete, successful snapshots publish.

with successful_snapshots as (
    select
        ingestion_run_id,
        started_at_utc,
        snapshot_date
    from {{ ref('stg_registry_trial_snapshots') }}
    where status = 'success'
),

trials_with_snapshots as (
    select
        t.*,
        s.started_at_utc
    from {{ ref('stg_registry_trials') }} t
    inner join successful_snapshots s
        on t.ingestion_run_id = s.ingestion_run_id
),

trial_timeline as (
    select
        nct_id,
        min(snapshot_date) as first_seen_snapshot_date,
        max(snapshot_date) as latest_seen_snapshot_date,
        max(started_at_utc) as max_started_at_utc
    from trials_with_snapshots
    group by nct_id
),

latest_run_per_trial as (
    select
        t.nct_id,
        max(t.ingestion_run_id) as max_ingestion_run_id
    from trials_with_snapshots t
    inner join trial_timeline l
        on t.nct_id = l.nct_id
        and t.started_at_utc = l.max_started_at_utc
    group by t.nct_id
),

latest_trials as (
    select t.*
    from trials_with_snapshots t
    inner join latest_run_per_trial lr
        on t.nct_id = lr.nct_id
        and t.ingestion_run_id = lr.max_ingestion_run_id
),

global_max_snapshot as (
    select max(snapshot_date) as max_registry_snapshot_date
    from successful_snapshots
),

mesh_extracted_runs as (
    select distinct regexp_extract(file, 'run_id=([^.]+)\.parquet', 1) as ingestion_run_id
    from (
        select file from glob('data/silver/registry_trial_condition_mesh/*.parquet')
        union all
        select file from glob('data/silver/silver_trial_condition_mesh/*.parquet')
    )
),

current_mesh as (
    select distinct ingestion_run_id, nct_id
    from {{ ref('stg_registry_trial_condition_mesh') }}
)

select
    t.registry_trial_key,
    t.nct_id,
    'https://clinicaltrials.gov/study/' || t.nct_id as registry_url,
    t.brief_title as current_brief_title,
    t.overall_status as current_overall_status,
    t.phase_normalized as current_phase,
    t.study_type as current_study_type,
    t.lead_sponsor_name as current_lead_sponsor,
    t.lead_sponsor_normalized as current_lead_sponsor_normalized,
    t.start_date,
    t.primary_completion_date,
    t.completion_date,
    t.study_first_post_date,
    t.enrollment_count,
    t.enrollment_type,
    t.has_results_flag as current_has_results_flag,
    t.record_quality_flag,
    l.first_seen_snapshot_date,
    l.latest_seen_snapshot_date,
    (t.snapshot_date = g.max_registry_snapshot_date) as active_in_latest_snapshot_flag,
    t.minimum_age_raw,
    t.maximum_age_raw,
    t.minimum_age_years as min_age_years,
    t.maximum_age_years as max_age_years,
    t.std_age_groups,
    t.sex as sex_eligibility,
    t.gender_based,
    t.gender_description,
    t.healthy_volunteers as accepts_healthy_volunteers,
    t.intervention_model as current_intervention_model,
    t.masking as current_masking,
    t.has_placebo_arm,
    t.why_stopped,
    t.brief_summary,
    t.primary_outcome_time_frame,
    case
        when t.minimum_age_years is not null and t.maximum_age_years is not null and t.minimum_age_years > t.maximum_age_years then false
        when t.minimum_age_raw is not null and trim(t.minimum_age_raw) != '' and upper(trim(t.minimum_age_raw)) not in ('N/A', 'NA', 'NOT APPLICABLE') and t.minimum_age_years is null then false
        when t.maximum_age_raw is not null and trim(t.maximum_age_raw) != '' and upper(trim(t.maximum_age_raw)) not in ('N/A', 'NA', 'NOT APPLICABLE') and t.maximum_age_years is null then false
        else true
    end as age_bounds_valid_flag,
    case
        when t.minimum_age_years is not null and t.maximum_age_years is not null and t.minimum_age_years > t.maximum_age_years then true
        else false
    end as age_bounds_contradictory_flag,
    case when t.minimum_age_years is null then true else false end as has_unspecified_min_age,
    case when t.maximum_age_years is null then true else false end as has_unspecified_max_age,
    case when t.healthy_volunteers is null then true else false end as healthy_volunteers_unknown_flag,
    case when t.sex is null or upper(trim(t.sex)) in ('', 'UNKNOWN') then true else false end as sex_eligibility_unknown_flag,
    case
        when er.ingestion_run_id is not null then case when m.nct_id is not null then true else false end
        else cast(null as boolean)
    end as has_derived_mesh,
    t.ingestion_run_id
from latest_trials t
inner join trial_timeline l
    on t.nct_id = l.nct_id
cross join global_max_snapshot g
left join mesh_extracted_runs er
    on t.ingestion_run_id = er.ingestion_run_id
left join current_mesh m
    on t.ingestion_run_id = m.ingestion_run_id
    and t.nct_id = m.nct_id
