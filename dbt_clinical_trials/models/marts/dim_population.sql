-- Census ACS 5-year population denominators (see seeds/acs_population.csv and
-- src/ingest/acs_population.py). Grain: geo_level x geo_id x sex x age_band x
-- race_ethnicity. Sex x age band comes from table B01001 and race/ethnicity from
-- B03002, so race_ethnicity is only ever broken out with sex and age 'all'
-- (no three-way cross-tab). state_code is null for ZCTAs, which can cross state lines.
select
    {{ generate_surrogate_key([
        'p.geo_level', 'p.geo_id', 'p.sex', 'p.age_band', 'p.race_ethnicity',
    ]) }} as population_key,
    p.geo_level,
    p.geo_id,
    p.geo_name,
    p.state_fips,
    s.state_code,
    p.sex,
    p.age_band,
    p.race_ethnicity,
    p.population,
    p.margin_of_error,
    p.table_id,
    p.vintage
from {{ ref('acs_population') }} p
left join {{ ref('state_population') }} s
    on p.state_fips = s.fips_code
