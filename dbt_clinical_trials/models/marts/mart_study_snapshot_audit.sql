-- Grain: profile x immutable successful snapshot x NCT ID.
-- Enrollment stays at study grain; no location join can apportion it.
with geography as (
    select indication_profile_id, snapshot_id, nct_id,
        count(*) as recorded_location_count,
        count(*) filter (where usable_geography_flag) as usable_location_count,
        count(*) filter (where country is null or trim(country) = ''
            or (us_location_flag and (state_raw is null or trim(state_raw) = '')))
            as missing_geography_location_count,
        count(*) filter (where facility_name is null or trim(facility_name) = '')
            as missing_facility_location_count
    from {{ ref('stg_trial_locations') }}
    group by indication_profile_id, snapshot_id, nct_id
), observations as (
    select t.* exclude (snapshot_date),
        s.snapshot_date,
        s.started_at_utc as snapshot_started_at_utc,
        s.ended_at_utc as snapshot_ended_at_utc,
        coalesce(g.recorded_location_count, 0) as recorded_location_count,
        coalesce(g.usable_location_count, 0) as usable_location_count,
        coalesce(g.missing_geography_location_count, 0) as missing_geography_location_count,
        coalesce(g.missing_facility_location_count, 0) as missing_facility_location_count,
        case when g.usable_location_count > 0 then 'usable'
             when g.recorded_location_count is null or g.missing_geography_location_count > 0
                 then 'missing'
             else 'unsupported' end as geography_category,
        coalesce(t.overall_status = 'RECRUITING', false) as confirmed_recruiting_flag,
        case when t.enrollment_count is null then 'missing'
             when t.enrollment_type = 'ESTIMATED' then 'estimated'
             when t.enrollment_type = 'ACTUAL' then 'actual'
             else 'unknown_type' end as enrollment_category,
        case when t.status_verified_date_raw is null or trim(t.status_verified_date_raw) = ''
                 then 'missing'
             when regexp_full_match(t.status_verified_date_raw, '[0-9]{4}-[0-9]{2}')
                 and try_strptime(t.status_verified_date_raw, '%Y-%m') is not null then 'month'
             when regexp_full_match(t.status_verified_date_raw, '[0-9]{4}-[0-9]{2}-[0-9]{2}')
                 and try_strptime(t.status_verified_date_raw, '%Y-%m-%d') is not null then 'day'
             when regexp_full_match(t.status_verified_date_raw, '[0-9]{4}')
                 and try_strptime(t.status_verified_date_raw, '%Y') is not null then 'year'
             else 'unparseable' end as verification_date_precision,
        case when regexp_full_match(t.last_update_post_date_raw, '[0-9]{4}-[0-9]{2}-[0-9]{2}')
             then cast(try_strptime(t.last_update_post_date_raw, '%Y-%m-%d') as date)
             end as posted_update_date
    from {{ ref('stg_trials') }} t
    join {{ ref('stg_trial_snapshots') }} s
        on t.ingestion_run_id = s.ingestion_run_id and s.status = 'success'
    left join geography g
        on t.indication_profile_id = g.indication_profile_id
        and t.snapshot_id = g.snapshot_id and t.nct_id = g.nct_id
), clocks as (
    select *,
        date_diff('day', posted_update_date, snapshot_date) as posted_update_age_days,
        case when verification_date_precision in ('month', 'day') then
            date_diff('month', {{ parse_partial_date('status_verified_date_raw') }}, snapshot_date)
            end as verification_age_months
    from observations
)
select
    {{ generate_surrogate_key(['indication_profile_id', 'snapshot_id', 'nct_id']) }} as study_audit_key,
    indication_profile_id, snapshot_id, ingestion_run_id, nct_id,
    'https://clinicaltrials.gov/study/' || nct_id as registry_url,
    brief_title, study_type, overall_status, phase_normalized, lead_sponsor_name,
    snapshot_date, snapshot_started_at_utc, snapshot_ended_at_utc, retrieved_at_utc,
    raw_page_reference, raw_study_ordinal, source_json_hash,
    last_update_post_date_raw, posted_update_date, posted_update_age_days,
    posted_update_age_days > 180 as older_posted_update_flag,
    status_verified_date_raw, verification_date_precision, verification_age_months,
    enrollment_count, enrollment_type, enrollment_category,
    recorded_location_count, usable_location_count, missing_geography_location_count,
    missing_facility_location_count, geography_category, confirmed_recruiting_flag,
    confirmed_recruiting_flag and geography_category = 'usable' as state_count_eligible_flag,
    case when not confirmed_recruiting_flag then 'not_confirmed_recruiting'
         when geography_category = 'missing' then 'missing_geography'
         when geography_category = 'unsupported' then 'unsupported_geography'
         end as state_count_exclusion_reason
from clocks
