-- One row per NCT ID + location ordinal + ingestion run at canonical registry grain.
select
    {{ generate_surrogate_key(['nct_id']) }} as registry_trial_key,
    ingestion_run_id,
    snapshot_id,
    timezone('UTC', try_cast(retrieved_at_utc as timestamptz)) as retrieved_at_utc,
    raw_page_reference,
    cast(raw_study_ordinal as bigint) as raw_study_ordinal,
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
from {{ source('silver', 'registry_trial_locations') }}
where nct_id is not null
