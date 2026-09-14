-- Grain: one row per profile x trial x listed U.S. facility x snapshot date.
-- Facility identity is best-effort (normalized name + city + state);
-- this is a listing signal from public records, not verified site capacity.
select
    {{ generate_surrogate_key([
        'nct_id', 'indication_profile_id', 'facility_normalized',
        'city_normalized', 'state_normalized', 'snapshot_date',
    ]) }} as trial_site_key,
    {{ generate_surrogate_key(['nct_id', 'indication_profile_id']) }} as trial_key,
    -- site_key deliberately stays profile-free: a facility is a physical place
    -- both profiles may list. Narrowing a facility question to one profile is
    -- the consumer's filter (indication_profile_id), not something to fold into
    -- the hash. No consumer filters that way yet: mart_site_overlap groups
    -- facilities by snapshot_date, facility, city and state with no profile
    -- column and never references site_key. Task 11 owns the profile view.
    {{ generate_surrogate_key([
        'facility_normalized', 'city_normalized', 'state_normalized',
    ]) }} as site_key,
    nct_id,
    indication_profile_id,
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
from {{ ref('int_trial_site_activity') }}
qualify row_number() over (
    partition by
        indication_profile_id, nct_id, facility_normalized,
        city_normalized, state_normalized, snapshot_date
    order by ingestion_run_id desc
) = 1
