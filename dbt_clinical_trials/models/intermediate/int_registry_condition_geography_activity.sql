-- Trial-level activity by segment over canonical registry observations:
-- one row per preset_id + nct_id + condition_group + state + snapshot_date.
-- Canonical observation per (nct_id, snapshot_date) is selected before
-- expanding condition and site membership.
with history as (
    select * from {{ ref('int_registry_trial_status_history') }}
),

conditions as (
    select * from {{ ref('int_registry_preset_condition_mapping') }}
),

state_sites as (
    select
        snapshot_id,
        nct_id,
        state_normalized,
        count(distinct facility_normalized || '|' || coalesce(city_normalized, ''))
            as listed_site_count_in_state
    from {{ ref('int_registry_geography_normalized') }}
    group by 1, 2, 3
)

select
    h.snapshot_date,
    c.preset_id,
    h.snapshot_id,
    h.ingestion_run_id,
    h.registry_trial_key,
    h.nct_id,
    c.condition_group,
    c.dementia_relevance_flag,
    s.state_normalized,
    h.phase_normalized,
    h.overall_status,
    h.previous_snapshot_date as previous_trial_snapshot_date,
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
    on h.snapshot_id = c.snapshot_id
    and h.nct_id = c.nct_id
inner join state_sites s
    on h.snapshot_id = s.snapshot_id
    and h.nct_id = s.nct_id
