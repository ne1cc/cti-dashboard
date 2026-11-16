-- Every canonical study snapshot audit row is either state-count eligible or
-- has exactly one deterministic base state-count exclusion reason.
select study_audit_key
from {{ ref('mart_registry_study_snapshot_audit') }}
where state_count_eligible_flag is null
   or state_count_eligible_flag != (confirmed_recruiting_flag and geography_category = 'usable')
   or (state_count_eligible_flag and state_count_exclusion_reason is not null)
   or (not state_count_eligible_flag and state_count_exclusion_reason is null)
   or coalesce(state_count_exclusion_reason, 'included') !=
       case
           when not confirmed_recruiting_flag then 'not_confirmed_recruiting'
           when geography_category = 'missing' then 'missing_geography'
           when geography_category = 'unsupported' then 'unsupported_geography'
           else 'included'
       end
