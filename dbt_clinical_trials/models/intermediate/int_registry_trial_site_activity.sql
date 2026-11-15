-- Site-level activity per canonical snapshot: which trials list which U.S. facilities.
-- Facility identity is best-effort (facility_normalized + city + state);
-- this is a listing signal, not verified site capacity.
with history as (
    select
        registry_trial_key,
        nct_id,
        snapshot_id,
        ingestion_run_id,
        snapshot_date,
        started_at_utc,
        overall_status,
        phase_normalized,
        lead_sponsor_normalized
    from {{ ref('int_registry_trial_status_history') }}
),

locations as (
    select * from {{ ref('int_registry_geography_normalized') }}
)

select
    h.snapshot_date,
    h.snapshot_id,
    h.ingestion_run_id,
    h.started_at_utc,
    h.registry_trial_key,
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
    on h.snapshot_id = l.snapshot_id
    and h.nct_id = l.nct_id
