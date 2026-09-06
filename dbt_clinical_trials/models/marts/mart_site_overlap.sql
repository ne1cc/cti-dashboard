-- Facility-level trial overlap per snapshot, per indication profile.
-- Grain: profile x snapshot_date x facility (normalized name + city + state).
-- Facility identity is best-effort string matching of public listings;
-- repeated participation is a listing signal, not a site-capacity claim.
-- The flag counts recruiting trials *within one profile's listings*: a
-- facility shared between two profiles' trial sets is not overlap within
-- either query scope, which is what the dashboard's site cards claim.
with sites as (
    select * from {{ ref('int_trial_site_activity') }}
    -- Overlap requires a facility identity; listings without a facility
    -- name cannot be attributed and are excluded here (kept upstream).
    where facility_normalized is not null
)

select
    indication_profile_id,
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
