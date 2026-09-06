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
    -- sponsor_key stays profile-free for the same reason site_key does: the
    -- organization is the same body whichever query scope listed it, and
    -- dim_sponsor scopes its rows per profile rather than re-hashing it.
    {{ generate_surrogate_key(['s.sponsor_normalized']) }} as sponsor_key,
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
