-- Feasibility Review Priority Queue (Registry Segments).
-- Grain: indication profile x condition_group x state x phase at that
-- profile's latest complete snapshot.
-- Provides transparent, sortable raw public-registry signals for human
-- feasibility review. Potential competition signals from public registry
-- listings, NOT a recruitment forecast and NOT a measure of patient
-- availability. Ordered by active_recruiting_trials descending by default.
with latest_snapshot as (
    select indication_profile_id, max(snapshot_date) as snapshot_date
    from {{ ref('fct_trial_snapshot') }}
    group by 1
),

history_depth as (
    select
        indication_profile_id,
        count(distinct snapshot_date) > 1 as has_multi_snapshot_history
    from {{ ref('fct_trial_snapshot') }}
    group by 1
),

-- Latest successful run *per profile*: mart_data_reliability is one row per
-- ingestion run, and a global `limit 1` would mix profile A's operational
-- completeness with profile B's location completeness.
run_reliability as (
    select indication_profile_id, usable_location_share
    from (
        select
            indication_profile_id,
            usable_location_share,
            row_number() over (
                partition by indication_profile_id
                order by snapshot_date desc, ingestion_run_id desc
            ) as rn
        from {{ ref('mart_data_reliability') }}
        where status = 'success'
    )
    where rn = 1
),

competition as (
    select c.*
    from {{ ref('mart_recruiting_competition') }} c
    inner join latest_snapshot ls using (indication_profile_id, snapshot_date)
),

-- Trials listing at least one facility that also hosts other recruiting
-- trials *within the same profile's listings* (best-effort facility
-- identity; a listing signal only).
overlapping_trials as (
    select distinct
        f.indication_profile_id, f.nct_id
    from {{ ref('fct_trial_site') }} f
    inner join latest_snapshot ls using (indication_profile_id, snapshot_date)
    inner join {{ ref('mart_site_overlap') }} o
        on o.indication_profile_id = f.indication_profile_id
        and o.snapshot_date = f.snapshot_date
        and o.facility_normalized = f.facility_normalized
        and coalesce(o.city_normalized, '') = coalesce(f.city_normalized, '')
        and o.state_normalized = f.state_normalized
    where o.repeated_site_participation_flag
),

segment_trials as (
    select
        a.indication_profile_id,
        a.condition_group,
        a.state_normalized,
        a.phase_normalized,
        count(distinct a.nct_id) as segment_trial_check,
        {{ safe_divide(
            'count(distinct a.nct_id) filter (ot.nct_id is not null)',
            'count(distinct a.nct_id)',
        ) }} as site_overlap_share,
        {{ safe_divide(
            "count(distinct a.nct_id) filter (a.record_quality_flag = 'ok')",
            'count(distinct a.nct_id)',
        ) }} as record_quality_ok_share
    from {{ ref('int_condition_geography_activity') }} a
    inner join latest_snapshot ls using (indication_profile_id, snapshot_date)
    left join overlapping_trials ot
        on a.nct_id = ot.nct_id
        and a.indication_profile_id = ot.indication_profile_id
    where a.overall_status = 'RECRUITING'
    group by 1, 2, 3, 4
),

inputs as (
    select
        c.indication_profile_id,
        c.condition_group,
        c.state_normalized,
        c.phase_normalized,
        c.snapshot_date,
        c.snapshot_date as latest_snapshot_date,
        c.recruiting_trial_count,
        c.recruiting_trial_count as active_recruiting_trials,
        c.listed_site_count,
        c.new_recruiting_90d,
        c.newly_posted_90d_proxy,
        h.has_multi_snapshot_history,
        case
            when h.has_multi_snapshot_history then c.new_recruiting_90d
            else c.newly_posted_90d_proxy
        end as recent_growth_input,
        (not h.has_multi_snapshot_history) as growth_uses_registry_proxy_flag,
        c.sponsor_count,
        c.sponsor_count as distinct_sponsors,
        c.top_sponsor_share,
        c.sponsor_hhi,
        c.competition_signal_band,
        s.site_overlap_share,
        s.site_overlap_share as site_overlap_rate,
        s.record_quality_ok_share,
        0.5 * coalesce(s.record_quality_ok_share, 0)
            + 0.5 * coalesce(r.usable_location_share, 0)
            as operational_completeness_share
    from competition c
    inner join segment_trials s
        using (indication_profile_id, condition_group, state_normalized, phase_normalized)
    inner join history_depth h using (indication_profile_id)
    left join run_reliability r using (indication_profile_id)
)

select
    {{ generate_surrogate_key([
        'indication_profile_id', 'condition_group', 'state_normalized',
        'phase_normalized', 'snapshot_date',
    ]) }} as priority_queue_key,
    snapshot_date,
    latest_snapshot_date,
    indication_profile_id,
    condition_group,
    state_normalized,
    phase_normalized,
    recruiting_trial_count,
    active_recruiting_trials,
    listed_site_count,
    new_recruiting_90d,
    newly_posted_90d_proxy,
    has_multi_snapshot_history,
    recent_growth_input,
    growth_uses_registry_proxy_flag,
    sponsor_count,
    distinct_sponsors,
    top_sponsor_share,
    sponsor_hhi,
    competition_signal_band,
    site_overlap_share,
    site_overlap_rate,
    record_quality_ok_share,
    operational_completeness_share,
    concat_ws(
        '; ',
        recruiting_trial_count || ' recruiting listing(s) in segment',
        case
            when growth_uses_registry_proxy_flag then
                newly_posted_90d_proxy
                || ' first posted within 90 days (registry-date proxy;'
                || ' snapshot history not yet accrued)'
            else new_recruiting_90d
                || ' newly recruiting in 90 days (snapshot transitions)'
        end,
        'sponsor concentration HHI ' || round(sponsor_hhi, 2)
            || ' across ' || sponsor_count || ' sponsor(s)',
        round(coalesce(site_overlap_share, 0) * 100)
            || '% of trials share a multi-trial facility listing',
        'operational completeness ' || round(operational_completeness_share * 100) || '%'
    ) as priority_explanation,
    'Potential competition signal from public registry listings.'
        || ' Not a recruitment forecast; requires human feasibility review.'
        as interpretation_note
from inputs
order by active_recruiting_trials desc, distinct_sponsors desc, priority_queue_key asc
