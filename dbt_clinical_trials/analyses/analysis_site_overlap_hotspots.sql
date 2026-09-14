-- Facilities listed by the most recruiting trials (best-effort facility
-- identity; a listing signal, not a site-capacity claim).
select
    indication_profile_id,
    facility_name,
    city,
    state_normalized,
    recruiting_trial_count,
    listed_trial_count,
    sponsor_count,
    phase_mix
from {{ ref('mart_site_overlap') }}
where repeated_site_participation_flag
order by recruiting_trial_count desc, listed_trial_count desc
limit 25
