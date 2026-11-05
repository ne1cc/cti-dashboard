-- Census ACS 5-year population denominators (Issue #71). Grain: geography
-- (state, county or ZCTA) x dimension: one 'total' row, age band x sex rows
-- (B01001) and race/ethnicity rows (B03002). Within a geography the three
-- dimensions each sum to the total, so a consumer picks one dimension and never
-- adds across them. Geographies are the seed's pinned subset; a missing
-- county or ZCTA means "not loaded", not zero population. state_code is null for
-- ZCTAs (a ZCTA can cross state lines).
select
    {{ generate_surrogate_key([
        'e.geo_level', 'e.geo_id', 'e.dimension',
        'e.age_band', 'e.sex', 'e.race_ethnicity',
    ]) }} as population_key,
    e.geo_level,
    e.geo_id,
    e.geo_name,
    nullif(e.state_fips, '') as state_fips,
    sp.state_code,
    e.dimension,
    nullif(e.age_band, '') as age_band,
    nullif(e.sex, '') as sex,
    nullif(e.race_ethnicity, '') as race_ethnicity,
    e.population,
    e.table_id,
    e.vintage,
    'U.S. Census Bureau' as source_name,
    'https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-'
        || lower(e.table_id) || '.dat' as source_url
from {{ ref('acs_population_extract') }} e
left join {{ ref('state_population') }} sp
    on nullif(e.state_fips, '') = sp.fips_code
