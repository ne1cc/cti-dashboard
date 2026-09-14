-- One row per indication profile per U.S. state (MVP grain). Structured to
-- extend to county/metro later; county/metro are NOT derived from city text
-- (would be unreliable).
select
    {{ generate_surrogate_key(['state_normalized', 'indication_profile_id']) }}
        as geography_key,
    indication_profile_id,
    state_normalized as state_code,
    'state' as geo_level,
    'United States' as country,
    cast(null as varchar) as county_fips,
    cast(null as varchar) as metro_area,
    count(distinct nct_id) as trial_count,
    count(distinct facility_normalized || '|' || coalesce(city_normalized, ''))
        as listed_site_count
from {{ ref('int_geography_normalized') }}
group by indication_profile_id, state_normalized
