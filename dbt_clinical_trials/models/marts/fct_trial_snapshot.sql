-- Grain: one row per profile per NCT ID per complete snapshot date.
-- History is built from this project's own snapshots; the registry API
-- serves only current records. current_record_flag is per profile: each
-- profile's own max(snapshot_date), never a global one.
with history as (
    select * from {{ ref('int_trial_status_history') }}
),

condition_counts as (
    select
        ingestion_run_id,
        indication_profile_id,
        nct_id,
        count(distinct condition_group) as condition_group_count
    from {{ ref('int_trial_condition_mapping') }}
    group by 1, 2, 3
),

us_site_counts as (
    select
        ingestion_run_id,
        indication_profile_id,
        nct_id,
        count(*) as site_count_us
    from {{ ref('int_geography_normalized') }}
    group by 1, 2, 3
),

latest_per_profile as (
    select indication_profile_id, max(snapshot_date) as latest_snapshot_date
    from history
    group by 1
)

select
    {{ generate_surrogate_key([
        'h.nct_id', 'h.indication_profile_id', 'h.snapshot_date',
    ]) }} as snapshot_key,
    {{ generate_surrogate_key(['h.nct_id', 'h.indication_profile_id']) }} as trial_key,
    h.nct_id,
    h.indication_profile_id,
    h.ingestion_run_id,
    h.snapshot_date,
    h.snapshot_timestamp_utc,
    h.overall_status,
    h.previous_status,
    h.status_changed_from_previous_snapshot_flag,
    h.entered_recruiting_flag,
    h.left_recruiting_flag,
    h.phase_normalized,
    h.study_type,
    h.enrollment_count,
    h.lead_sponsor_name,
    h.lead_sponsor_normalized,
    h.has_results_flag,
    h.study_first_post_date,
    h.record_quality_flag,
    h.first_seen_snapshot_date,
    h.days_since_first_seen,
    coalesce(cc.condition_group_count, 0) as condition_group_count,
    coalesce(sc.site_count_us, 0) as site_count_us,
    h.source_json_hash as record_hash,
    (h.snapshot_date = o.latest_snapshot_date) as current_record_flag
from history h
left join condition_counts cc
    on h.ingestion_run_id = cc.ingestion_run_id
    and h.indication_profile_id = cc.indication_profile_id
    and h.nct_id = cc.nct_id
left join us_site_counts sc
    on h.ingestion_run_id = sc.ingestion_run_id
    and h.indication_profile_id = sc.indication_profile_id
    and h.nct_id = sc.nct_id
-- Explicit predicate, not `using (indication_profile_id)`: the two left joins
-- above expose the same column name, and DuckDB then reports the USING column
-- as ambiguous between h and cc.
inner join latest_per_profile o
    on h.indication_profile_id = o.indication_profile_id
