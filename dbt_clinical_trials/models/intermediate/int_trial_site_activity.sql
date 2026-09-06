-- Site-level activity per snapshot: which trials list which U.S. facilities.
-- Facility identity is best-effort (facility_normalized + city + state);
-- this is a listing signal, not verified site capacity.
with history as (
    select
        nct_id, indication_profile_id, ingestion_run_id, snapshot_date,
        overall_status, phase_normalized, lead_sponsor_normalized
    from {{ ref('int_trial_status_history') }}
),

locations as (
    select * from {{ ref('int_geography_normalized') }}
)

select
    h.snapshot_date,
    h.indication_profile_id,
    h.ingestion_run_id,
    h.nct_id,
    h.overall_status,
    h.phase_normalized,
    h.lead_sponsor_normalized,
    l.facility_name,
    l.facility_normalized,
    l.city,
    l.city_normalized,
    l.state_normalized,
    l.zip_code,
    l.location_status
from history h
inner join locations l
    on h.ingestion_run_id = l.ingestion_run_id
    and h.indication_profile_id = l.indication_profile_id
    and h.nct_id = l.nct_id
