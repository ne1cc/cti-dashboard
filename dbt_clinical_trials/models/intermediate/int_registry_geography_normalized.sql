-- U.S.-usable locations only, joined after selecting one canonical trial
-- observation per NCT ID x snapshot_date.
-- Grain: snapshot_id + nct_id + facility_normalized + city_normalized + state + zip + location_status.
with canonical_observations as (
    select
        snapshot_id,
        ingestion_run_id,
        snapshot_date,
        registry_trial_key,
        nct_id
    from {{ ref('int_registry_trial_status_history') }}
)

select distinct
    c.snapshot_id,
    c.ingestion_run_id,
    c.snapshot_date,
    c.registry_trial_key,
    c.nct_id,
    l.facility_name,
    l.facility_normalized,
    l.city,
    l.city_normalized,
    l.state_normalized,
    l.zip_code,
    l.location_status
from canonical_observations c
inner join {{ ref('stg_registry_trial_locations') }} l
    on c.snapshot_id = l.snapshot_id
    and c.nct_id = l.nct_id
where l.usable_geography_flag
