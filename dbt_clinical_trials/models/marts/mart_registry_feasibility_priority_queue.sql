-- Feasibility Review Priority Queue (Registry Segments, per preset).
-- Grain: preset_id x condition_group x state x phase at that preset's latest snapshot.
with latest_snapshot as (
    select max(f.snapshot_date) as snapshot_date
    from {{ ref('fct_registry_trial_snapshot') }} f
    inner join {{ ref('bridge_preset_trial') }} b
        on f.nct_id = b.nct_id
        and f.snapshot_date = b.snapshot_date
        and f.snapshot_id = b.snapshot_id
),

preset_history as (
    select
        b.preset_id,
        count(distinct f.snapshot_date) > 1 as has_multi_snapshot_history,
        datediff('day', min(f.snapshot_date), max(f.snapshot_date))
            as observed_history_days
    from {{ ref('fct_registry_trial_snapshot') }} f
    inner join {{ ref('bridge_preset_trial') }} b
        on f.nct_id = b.nct_id
        and f.snapshot_date = b.snapshot_date
        and f.snapshot_id = b.snapshot_id
    where not b.is_unassigned
    group by 1
),

-- Preset-scoped location completeness over the current authoritative snapshot
-- (canonical observation per trial before expanding location rows).
preset_location_reliability as (
    select
        b.preset_id,
        {{ safe_divide(
            'count(*) filter (l.usable_geography_flag)',
            'count(*)',
        ) }} as usable_location_share
    from {{ ref('bridge_preset_trial') }} b
    inner join latest_snapshot ls
        on b.snapshot_date = ls.snapshot_date
    inner join {{ ref('int_registry_trial_status_history') }} h
        on b.nct_id = h.nct_id
        and b.snapshot_date = h.snapshot_date
        and b.snapshot_id = h.snapshot_id
    inner join {{ ref('stg_registry_trial_locations') }} l
        on h.snapshot_id = l.snapshot_id
        and h.nct_id = l.nct_id
    where not b.is_unassigned
    group by 1
),

competition as (
    select c.*
    from {{ ref('mart_registry_recruiting_competition') }} c
    inner join latest_snapshot ls using (snapshot_date)
),

overlapping_trials as (
    select distinct
        b.preset_id,
        f.nct_id
    from {{ ref('fct_registry_trial_site') }} f
    inner join {{ ref('bridge_preset_trial') }} b
        on f.nct_id = b.nct_id
        and f.snapshot_date = b.snapshot_date
        and f.snapshot_id = b.snapshot_id
    inner join latest_snapshot ls
        on f.snapshot_date = ls.snapshot_date
    inner join {{ ref('mart_registry_site_overlap') }} o
        on o.preset_id = b.preset_id
        and o.snapshot_date = f.snapshot_date
        and o.facility_normalized = f.facility_normalized
        and coalesce(o.city_normalized, '') = coalesce(f.city_normalized, '')
        and o.state_normalized = f.state_normalized
    where o.repeated_site_participation_flag
      and not b.is_unassigned
),

segment_trials as (
    select
        a.preset_id,
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
    from {{ ref('int_registry_condition_geography_activity') }} a
    inner join latest_snapshot ls using (snapshot_date)
    left join overlapping_trials ot
        on a.nct_id = ot.nct_id
        and a.preset_id = ot.preset_id
    where a.overall_status = 'RECRUITING'
    group by 1, 2, 3, 4
),

inputs as (
    select
        c.preset_id,
        c.condition_group,
        c.state_normalized,
        c.phase_normalized,
        c.snapshot_date,
        c.snapshot_date as latest_snapshot_date,
        c.recruiting_trial_count,
        c.recruiting_trial_count as active_recruiting_trials,
        c.listed_site_count,
        c.new_recruiting_90d,
        c.newly_registered_90d,
        h.has_multi_snapshot_history,
        h.observed_history_days,
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
        using (preset_id, condition_group, state_normalized, phase_normalized)
    inner join preset_history h using (preset_id)
    left join preset_location_reliability r using (preset_id)
)

select
    {{ generate_surrogate_key([
        'preset_id', 'condition_group', 'state_normalized',
        'phase_normalized', 'snapshot_date',
    ]) }} as priority_queue_key,
    snapshot_date,
    latest_snapshot_date,
    preset_id,
    condition_group,
    {{ generate_surrogate_key(["'group:' || condition_group"]) }} as registry_condition_key,
    state_normalized,
    phase_normalized,
    recruiting_trial_count,
    active_recruiting_trials,
    listed_site_count,
    new_recruiting_90d,
    newly_registered_90d,
    has_multi_snapshot_history,
    observed_history_days,
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
            when new_recruiting_90d is not null then
                new_recruiting_90d
                || ' newly recruiting in 90 days (snapshot transitions;'
                || ' ' || observed_history_days || ' days of history observed)'
            when observed_history_days < 90 then
                'new recruiting: not enough history ('
                || observed_history_days || ' of 90 days observed)'
            else
                'new recruiting: not enough history (snapshots do not cover the window)'
        end,
        newly_registered_90d
            || ' newly registered within 90 days (registry first-post date,'
            || ' not a recruiting transition)',
        case when sponsor_hhi is null then
            'sponsor concentration: insufficient data (' || sponsor_count || ' sponsors)'
        else
            'sponsor concentration HHI ' || round(sponsor_hhi, 2)
                || ' across ' || sponsor_count || ' sponsor(s)'
        end,
        round(coalesce(site_overlap_share, 0) * 100)
            || '% of trials share a multi-trial facility listing',
        'operational completeness ' || round(operational_completeness_share * 100) || '%'
    ) as priority_explanation,
    'Potential competition signal from public registry listings.'
        || ' Not a recruitment forecast; requires human feasibility review.'
        as interpretation_note
from inputs
order by active_recruiting_trials desc, distinct_sponsors desc, priority_queue_key asc
