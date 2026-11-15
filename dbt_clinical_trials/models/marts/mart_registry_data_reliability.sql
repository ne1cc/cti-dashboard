-- Run-level and preset-membership data reliability summary at canonical
-- registry grain. Grain: ingestion_run_id x preset_id.
--
-- Distinguishes source ingestion completeness (manifest reconciliation and
-- source-wide trial/location/condition totals across the authoritative
-- whole-registry run) from each preset's membership denominator (bridge_preset_trial,
-- including the explicit 'unassigned' scope for no-MeSH/unmapped trials).
-- Runs without preset memberships (e.g. failed or bronze-only runs) appear once
-- under preset_id = '__source__' with membership_scope = 'source_only'.
with runs as (
    select *
    from {{ ref('stg_registry_trial_snapshots') }}
    where profile in ('full_catalog', 'full-catalog', 'default')
      and (condition is null or trim(condition) = '')
),

source_trial_stats as (
    select
        snapshot_id as ingestion_run_id,
        count(*) as source_trial_row_count,
        count(distinct nct_id) as source_distinct_trial_count,
        count(*) filter (where ingestion_run_id = snapshot_id) as source_fetched_trial_row_count,
        count(*) filter (where record_quality_flag != 'ok') as source_flagged_record_count
    from {{ ref('stg_registry_trials') }}
    group by 1
),

source_location_stats as (
    select
        snapshot_id as ingestion_run_id,
        count(*) as source_location_row_count,
        count(*) filter (where usable_geography_flag) as source_usable_location_count,
        count(*) filter (where latitude is not null and longitude is not null)
            as source_coordinates_exact_count,
        count(*) filter (
            where latitude is null
              and (
                  nullif(trim(cast(zip_code as varchar)), '') is not null
                  or (
                      city_normalized is not null
                      and state_normalized is not null
                      and state_normalized != 'UNKNOWN'
                  )
              )
        ) as source_coordinates_fallback_eligible_count
    from {{ ref('stg_registry_trial_locations') }}
    group by 1
),

source_condition_stats as (
    select
        snapshot_id as ingestion_run_id,
        count(*) as source_condition_row_count
    from {{ ref('stg_registry_trial_conditions') }}
    group by 1
),

run_presets as (
    select distinct
        snapshot_id as ingestion_run_id,
        preset_id,
        membership_scope
    from {{ ref('bridge_preset_trial') }}
),

run_scopes as (
    select
        r.ingestion_run_id,
        coalesce(rp.preset_id, '__source__') as preset_id,
        coalesce(rp.membership_scope, 'source_only') as membership_scope
    from runs r
    left join run_presets rp
        on r.ingestion_run_id = rp.ingestion_run_id
),

preset_trial_stats as (
    select
        b.snapshot_id as ingestion_run_id,
        b.preset_id,
        count(*) as trial_row_count,
        count(distinct t.nct_id) as distinct_trial_count,
        count(*) filter (where t.record_quality_flag != 'ok') as flagged_record_count,
        count(*) filter (where t.enrollment_count is null) as missing_enrollment_count,
        count(*) filter (where t.start_date is null) as missing_start_date_count,
        count(*) filter (where t.lead_sponsor_normalized is null) as missing_lead_sponsor_count
    from {{ ref('bridge_preset_trial') }} b
    inner join {{ ref('stg_registry_trials') }} t
        on b.snapshot_id = t.snapshot_id
        and b.nct_id = t.nct_id
    group by 1, 2
),

preset_location_stats as (
    select
        b.snapshot_id as ingestion_run_id,
        b.preset_id,
        count(*) as location_row_count,
        count(*) filter (where l.usable_geography_flag) as usable_location_count,
        count(*) filter (where l.latitude is not null and l.longitude is not null)
            as coordinates_exact_count,
        count(*) filter (
            where l.latitude is null
              and (
                  nullif(trim(cast(l.zip_code as varchar)), '') is not null
                  or (
                      l.city_normalized is not null
                      and l.state_normalized is not null
                      and l.state_normalized != 'UNKNOWN'
                  )
              )
        ) as coordinates_fallback_eligible_count
    from {{ ref('bridge_preset_trial') }} b
    inner join {{ ref('stg_registry_trial_locations') }} l
        on b.snapshot_id = l.snapshot_id
        and b.nct_id = l.nct_id
    group by 1, 2
),

preset_mapped_condition_stats as (
    select
        snapshot_id as ingestion_run_id,
        preset_id,
        cast(sum(source_condition_count) as bigint) as condition_row_count,
        cast(
            coalesce(
                sum(source_condition_count) filter (where mapping_confidence = 'low'),
                0
            ) as bigint
        ) as low_confidence_condition_count
    from {{ ref('int_registry_preset_condition_mapping') }}
    group by 1, 2
),

unassigned_condition_stats as (
    select
        b.snapshot_id as ingestion_run_id,
        b.preset_id,
        count(*) as condition_row_count,
        cast(0 as bigint) as low_confidence_condition_count
    from {{ ref('bridge_preset_trial') }} b
    inner join {{ ref('stg_registry_trial_conditions') }} c
        on b.snapshot_id = c.snapshot_id
        and b.nct_id = c.nct_id
    where b.is_unassigned
    group by 1, 2
),

preset_condition_stats as (
    select * from preset_mapped_condition_stats
    union all
    select * from unassigned_condition_stats
)

select
    {{ generate_surrogate_key(['r.ingestion_run_id', 'rs.preset_id']) }} as reliability_key,
    r.ingestion_run_id,
    rs.preset_id,
    rs.membership_scope,
    r.profile as source_profile,
    r.snapshot_date,
    r.started_at_utc,
    r.condition,
    r.mode,
    r.status,
    r.page_count,
    r.record_count as manifest_record_count,
    r.total_count_reported,
    r.quarantined_record_count,
    st.source_trial_row_count,
    st.source_distinct_trial_count,
    st.source_fetched_trial_row_count,
    case
        when st.source_trial_row_count is not null
            then (coalesce(st.source_fetched_trial_row_count, 0) = r.record_count)
    end as manifest_reconciled_flag,
    (st.source_trial_row_count = st.source_distinct_trial_count) as unique_nct_flag,
    st.source_flagged_record_count,
    sl.source_location_row_count,
    sl.source_usable_location_count,
    {{ safe_divide('sl.source_usable_location_count', 'sl.source_location_row_count') }}
        as source_usable_location_share,
    sl.source_coordinates_exact_count,
    sl.source_coordinates_fallback_eligible_count,
    sc.source_condition_row_count,
    pt.trial_row_count,
    pt.distinct_trial_count,
    pt.flagged_record_count,
    {{ safe_divide('pt.flagged_record_count', 'pt.trial_row_count') }} as flagged_record_share,
    {{ safe_divide('pt.missing_enrollment_count', 'pt.trial_row_count') }}
        as missing_enrollment_share,
    {{ safe_divide('pt.missing_start_date_count', 'pt.trial_row_count') }}
        as missing_start_date_share,
    {{ safe_divide('pt.missing_lead_sponsor_count', 'pt.trial_row_count') }}
        as missing_lead_sponsor_share,
    pl.location_row_count,
    pl.usable_location_count,
    {{ safe_divide('pl.usable_location_count', 'pl.location_row_count') }}
        as usable_location_share,
    pl.coordinates_exact_count,
    pl.coordinates_fallback_eligible_count,
    {{ safe_divide('pl.coordinates_exact_count', 'pl.location_row_count') }}
        as coordinates_exact_share,
    {{ safe_divide('pl.coordinates_fallback_eligible_count', 'pl.location_row_count') }}
        as coordinates_fallback_eligible_share,
    pc.condition_row_count,
    pc.low_confidence_condition_count,
    {{ safe_divide('pc.low_confidence_condition_count', 'pc.condition_row_count') }}
        as low_confidence_condition_share,
    r.error
from runs r
inner join run_scopes rs
    on r.ingestion_run_id = rs.ingestion_run_id
left join source_trial_stats st
    on r.ingestion_run_id = st.ingestion_run_id
left join source_location_stats sl
    on r.ingestion_run_id = sl.ingestion_run_id
left join source_condition_stats sc
    on r.ingestion_run_id = sc.ingestion_run_id
left join preset_trial_stats pt
    on rs.ingestion_run_id = pt.ingestion_run_id
    and rs.preset_id = pt.preset_id
left join preset_location_stats pl
    on rs.ingestion_run_id = pl.ingestion_run_id
    and rs.preset_id = pl.preset_id
left join preset_condition_stats pc
    on rs.ingestion_run_id = pc.ingestion_run_id
    and rs.preset_id = pc.preset_id
