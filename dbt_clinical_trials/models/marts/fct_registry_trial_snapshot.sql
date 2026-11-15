-- Grain: one canonical observation per NCT ID per complete authoritative snapshot date.
-- Presets filter at read time via bridge_preset_trial.
with history as (
    select * from {{ ref('int_registry_trial_status_history') }}
),

condition_counts as (
    select
        snapshot_id,
        nct_id,
        count(distinct mesh_id) as condition_node_count
    from {{ ref('stg_registry_trial_condition_mesh') }}
    group by 1, 2
),

us_site_counts as (
    select
        snapshot_id,
        nct_id,
        count(*) as site_count_us
    from {{ ref('int_registry_geography_normalized') }}
    group by 1, 2
),

latest_snapshot as (
    select max(snapshot_date) as latest_snapshot_date
    from history
)

select
    {{ generate_surrogate_key(['h.nct_id', 'h.snapshot_date']) }} as snapshot_key,
    h.registry_trial_key,
    h.nct_id,
    h.snapshot_id,
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
    coalesce(cc.condition_node_count, 0) as condition_node_count,
    coalesce(sc.site_count_us, 0) as site_count_us,
    h.source_json_hash as record_hash,
    (h.snapshot_date = o.latest_snapshot_date) as current_record_flag
from history h
left join condition_counts cc
    on h.snapshot_id = cc.snapshot_id
    and h.nct_id = cc.nct_id
left join us_site_counts sc
    on h.snapshot_id = sc.snapshot_id
    and h.nct_id = sc.nct_id
cross join latest_snapshot o
