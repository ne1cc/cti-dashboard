-- Grain: one row per profile x trial x sponsor/collaborator x role (from each
-- trial's latest snapshot in that profile, matching dim_trial currency).
with current_trials as (
    select nct_id, indication_profile_id, ingestion_run_id
    from {{ ref('int_current_trial_status') }}
)

select distinct
    {{ generate_surrogate_key([
        's.nct_id', 's.indication_profile_id', 's.sponsor_normalized',
        's.sponsor_role',
    ]) }} as trial_sponsor_key,
    {{ generate_surrogate_key(['s.nct_id', 's.indication_profile_id']) }}
        as trial_key,
    -- sponsor_key hashes the same two inputs dim_sponsor does, because
    -- dim_sponsor's rows are per profile: its trial_count and
    -- lead_sponsor_trial_count describe one profile's listings, so a key built
    -- from the sponsor alone could not address one of those rows. The
    -- organization is still one body across query scopes; this key addresses
    -- its per-profile *row*, which is what the bridge points at.
    {{ generate_surrogate_key([
        's.sponsor_normalized', 's.indication_profile_id',
    ]) }} as sponsor_key,
    s.nct_id,
    s.indication_profile_id,
    s.sponsor_name,
    s.sponsor_normalized,
    s.sponsor_role,
    s.sponsor_class,
    (s.sponsor_role = 'lead_sponsor') as lead_sponsor_flag
from {{ ref('stg_trial_sponsors') }} s
inner join current_trials c
    on s.ingestion_run_id = c.ingestion_run_id
    and s.indication_profile_id = c.indication_profile_id
    and s.nct_id = c.nct_id
where s.sponsor_normalized is not null
