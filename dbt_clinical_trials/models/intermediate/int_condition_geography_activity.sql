-- Trial-level activity by segment: one row per profile + nct_id +
-- condition_group + state + snapshot_date. Feeds all condition-geography marts
-- so their grain and filters stay consistent. Trials without a usable U.S.
-- location do not appear here (U.S.-scope MVP; raw data preserved upstream).
with history as (
    select * from {{ ref('int_trial_status_history') }}
),

conditions as (
    select * from {{ ref('int_trial_condition_mapping') }}
),

state_sites as (
    select
        ingestion_run_id,
        indication_profile_id,
        nct_id,
        state_normalized,
        count(distinct facility_normalized || '|' || coalesce(city_normalized, ''))
            as listed_site_count_in_state
    from {{ ref('int_geography_normalized') }}
    group by 1, 2, 3, 4
)

select
    h.snapshot_date,
    h.indication_profile_id,
    h.ingestion_run_id,
    h.nct_id,
    c.condition_group,
    c.dementia_relevance_flag,
    s.state_normalized,
    h.phase_normalized,
    h.overall_status,
    h.previous_status,
    h.status_changed_from_previous_snapshot_flag,
    h.entered_recruiting_flag,
    h.left_recruiting_flag,
    h.first_seen_snapshot_date,
    h.days_since_first_seen,
    h.study_first_post_date,
    h.lead_sponsor_normalized,
    h.lead_sponsor_name,
    h.has_results_flag,
    h.record_quality_flag,
    s.listed_site_count_in_state
from history h
inner join conditions c
    on h.ingestion_run_id = c.ingestion_run_id
    and h.indication_profile_id = c.indication_profile_id
    and h.nct_id = c.nct_id
inner join state_sites s
    on h.ingestion_run_id = s.ingestion_run_id
    and h.indication_profile_id = s.indication_profile_id
    and h.nct_id = s.nct_id
