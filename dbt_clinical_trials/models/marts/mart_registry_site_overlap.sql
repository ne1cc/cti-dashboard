-- Facility-level trial overlap per snapshot, per preset.
-- Grain: preset_id x snapshot_date x facility (normalized name + city + state).
-- Canonical observation per (nct_id, snapshot_date) is selected before
-- expanding site and preset membership.
with sites as (
    select
        b.preset_id,
        s.snapshot_date,
        s.nct_id,
        s.overall_status,
        s.phase_normalized,
        s.lead_sponsor_normalized,
        s.facility_name,
        s.facility_normalized,
        s.city,
        s.city_normalized,
        s.state_normalized
    from {{ ref('int_registry_trial_site_activity') }} s
    inner join {{ ref('bridge_preset_trial') }} b
        on s.nct_id = b.nct_id
        and s.snapshot_date = b.snapshot_date
        and s.snapshot_id = b.snapshot_id
    where s.facility_normalized is not null
      and not b.is_unassigned
)

select
    preset_id,
    snapshot_date,
    facility_normalized,
    city_normalized,
    state_normalized,
    any_value(facility_name) as facility_name,
    any_value(city) as city,
    count(distinct nct_id) as listed_trial_count,
    count(distinct nct_id) filter (overall_status = 'RECRUITING')
        as recruiting_trial_count,
    count(distinct lead_sponsor_normalized) as sponsor_count,
    string_agg(distinct phase_normalized, ' | ' order by phase_normalized)
        as phase_mix,
    (count(distinct nct_id) filter (overall_status = 'RECRUITING')) > 1
        as repeated_site_participation_flag
from sites
group by 1, 2, 3, 4, 5
