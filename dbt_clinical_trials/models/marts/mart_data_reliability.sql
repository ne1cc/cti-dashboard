-- Run-level data reliability summary. One row per ingestion run; each run
-- belongs to exactly one indication profile.
-- Surfaces reconciliation and usability shares so every downstream chart
-- can disclose the confidence of its inputs.
with runs as (
    select * from {{ ref('stg_trial_snapshots') }}
),

trial_stats as (
    select
        ingestion_run_id,
        -- Silver is authoritative, so this is the profile the final select
        -- prefers; runs (r) supplies the fallback. It is carried here and only
        -- here: the location/condition CTEs' copies were dropped because
        -- nothing downstream reads them.
        any_value(indication_profile_id) as indication_profile_id,
        count(*) as trial_row_count,
        count(distinct nct_id) as distinct_trial_count,
        count(*) filter (record_quality_flag != 'ok') as flagged_record_count,
        count(*) filter (enrollment_count is null) as missing_enrollment_count,
        count(*) filter (start_date is null) as missing_start_date_count,
        count(*) filter (lead_sponsor_normalized is null)
            as missing_lead_sponsor_count
    from {{ ref('stg_trials') }}
    group by 1
),

location_stats as (
    select
        ingestion_run_id,
        count(*) as location_row_count,
        count(*) filter (usable_geography_flag) as usable_location_count
    from {{ ref('stg_trial_locations') }}
    group by 1
),

condition_stats as (
    select
        ingestion_run_id,
        count(*) as condition_row_count,
        count(*) filter (mapping_confidence = 'low')
            as low_confidence_condition_count
    from {{ ref('stg_trial_conditions') }}
    group by 1
)

select
    r.ingestion_run_id,
    -- Silver is authoritative when it exists (a manifest written before profile
    -- stamping says 'default'; its silver rows say which profile actually
    -- produced them). The manifest is the fallback, and it is the only side
    -- that exists for an ingest_only profile's bronze-only run: full_catalog
    -- has manifests and no silver, so a t-only profile is NULL there, Step 6
    -- declares this column not_null, and Task 14's first full_catalog refresh
    -- would fail the build. One of the two is non-null for every run that has
    -- a manifest at all, which is exactly this table's grain.
    coalesce(t.indication_profile_id, r.indication_profile_id)
        as indication_profile_id,
    r.snapshot_date,
    r.started_at_utc,
    r.condition,
    r.mode,
    r.status,
    r.page_count,
    r.record_count as manifest_record_count,
    r.total_count_reported,
    r.quarantined_record_count,
    t.trial_row_count,
    t.distinct_trial_count,
    (t.trial_row_count = r.record_count) as manifest_reconciled_flag,
    (t.trial_row_count = t.distinct_trial_count) as unique_nct_flag,
    t.flagged_record_count,
    {{ safe_divide('t.flagged_record_count', 't.trial_row_count') }}
        as flagged_record_share,
    {{ safe_divide('t.missing_enrollment_count', 't.trial_row_count') }}
        as missing_enrollment_share,
    {{ safe_divide('t.missing_start_date_count', 't.trial_row_count') }}
        as missing_start_date_share,
    {{ safe_divide('t.missing_lead_sponsor_count', 't.trial_row_count') }}
        as missing_lead_sponsor_share,
    l.location_row_count,
    l.usable_location_count,
    {{ safe_divide('l.usable_location_count', 'l.location_row_count') }}
        as usable_location_share,
    c.condition_row_count,
    c.low_confidence_condition_count,
    {{ safe_divide(
        'c.low_confidence_condition_count', 'c.condition_row_count',
    ) }} as low_confidence_condition_share,
    r.error
from runs r
left join trial_stats t using (ingestion_run_id)
left join location_stats l using (ingestion_run_id)
left join condition_stats c using (ingestion_run_id)
