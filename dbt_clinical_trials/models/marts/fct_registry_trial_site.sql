-- Grain: one row per canonical trial x listed U.S. facility x snapshot date.
-- Presets filter at read time via bridge_preset_trial.
select
    {{ generate_surrogate_key([
        'nct_id', 'facility_normalized', 'city_normalized',
        'state_normalized', 'snapshot_date',
    ]) }} as trial_site_key,
    registry_trial_key,
    {{ generate_surrogate_key([
        'facility_normalized', 'city_normalized', 'state_normalized',
    ]) }} as site_key,
    nct_id,
    snapshot_id,
    ingestion_run_id,
    snapshot_date,
    facility_name,
    facility_normalized,
    city,
    city_normalized,
    state_normalized,
    zip_code,
    location_status,
    overall_status as trial_overall_status,
    phase_normalized,
    lead_sponsor_normalized
from {{ ref('int_registry_trial_site_activity') }}
qualify row_number() over (
    partition by
        nct_id, facility_normalized, city_normalized,
        state_normalized, snapshot_date
    order by started_at_utc desc, snapshot_id asc
) = 1
