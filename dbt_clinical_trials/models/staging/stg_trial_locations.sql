-- One row per NCT ID + facility + city + state + country + ingestion run.
-- All rows preserved; usable_geography_flag gates U.S.-scope marts.
-- Facility names are NOT stable unique site identifiers; facility_normalized
-- is a best-effort matching key (documented limitation).
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    facility_name,
    facility_normalized,
    city,
    {{ normalize_text('city') }} as city_normalized,
    state as state_raw,
    state_normalized,
    cast(zip_code as varchar) as zip_code,
    country,
    geo_scope,
    try_cast(latitude as double) as latitude,
    try_cast(longitude as double) as longitude,
    location_status,
    cast(us_location_flag as boolean) as us_location_flag,
    cast(usable_geography_flag as boolean) as usable_geography_flag,
    source_json_hash
from {{ source('silver', 'silver_trial_locations') }}
where nct_id is not null
