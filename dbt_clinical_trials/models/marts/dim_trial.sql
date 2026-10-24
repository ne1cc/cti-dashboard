-- One row per indication profile per NCT ID (the trial's current record in
-- that profile's latest snapshot). trial_key hashes the full grain: the same
-- registry record observed through two query scopes is two dimension rows, and
-- collapsing them would attribute one profile's status to the other.
select
    {{ generate_surrogate_key(['c.nct_id', 'c.indication_profile_id']) }} as trial_key,
    c.nct_id,
    c.indication_profile_id,
    'https://clinicaltrials.gov/study/' || c.nct_id as registry_url,
    t.brief_title as current_brief_title,
    c.overall_status as current_overall_status,
    c.phase_normalized as current_phase,
    c.study_type as current_study_type,
    c.lead_sponsor_name as current_lead_sponsor,
    c.lead_sponsor_normalized as current_lead_sponsor_normalized,
    t.start_date,
    t.primary_completion_date,
    t.completion_date,
    t.study_first_post_date,
    c.enrollment_count,
    t.enrollment_type,
    c.has_results_flag as current_has_results_flag,
    c.record_quality_flag,
    c.first_seen_snapshot_date,
    c.snapshot_date as latest_seen_snapshot_date,
    c.active_in_latest_snapshot_flag,
    t.minimum_age_raw,
    t.maximum_age_raw,
    t.minimum_age_years as min_age_years,
    t.maximum_age_years as max_age_years,
    t.std_age_groups,
    t.sex as sex_eligibility,
    t.gender_based,
    t.gender_description,
    t.healthy_volunteers as accepts_healthy_volunteers,
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
    case when t.sex is null or upper(trim(t.sex)) in ('', 'UNKNOWN') then true else false end as sex_eligibility_unknown_flag
from {{ ref('int_current_trial_status') }} c
inner join {{ ref('stg_trials') }} t
    on c.ingestion_run_id = t.ingestion_run_id
    and c.nct_id = t.nct_id
    and c.indication_profile_id = t.indication_profile_id
