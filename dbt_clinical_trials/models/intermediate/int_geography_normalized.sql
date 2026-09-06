-- U.S.-usable locations only (all rows remain preserved in silver/staging).
-- Grain: profile + nct_id + facility_normalized + city_normalized + state + run.
select distinct
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    facility_name,
    facility_normalized,
    city,
    city_normalized,
    state_normalized,
    zip_code,
    location_status
from {{ ref('stg_trial_locations') }}
where usable_geography_flag
