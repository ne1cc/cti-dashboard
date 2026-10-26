-- One row per profile + NCT ID + ingestion run + original location ordinal.
-- All rows preserved; usable_geography_flag gates U.S.-scope marts.
-- Facility names are NOT stable unique site identifiers; facility_normalized
-- is a best-effort matching key (documented limitation).
select
    ingestion_run_id,
    snapshot_id,
    timezone('UTC', try_cast(retrieved_at_utc as timestamptz)) as retrieved_at_utc,
    raw_page_reference,
    cast(raw_study_ordinal as bigint) as raw_study_ordinal,
    indication_profile_id,
    nct_id,
    cast(location_ordinal as bigint) as location_ordinal,
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
