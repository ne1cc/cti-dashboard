-- Every eligible row is included or has exactly one base state-count exclusion.
-- Filter-specific geography membership is evaluated downstream.
select study_audit_key
from {{ ref('mart_study_snapshot_audit') }}
where state_count_eligible_flag is null
   or state_count_eligible_flag != (confirmed_recruiting_flag and geography_category = 'usable')
   or (state_count_eligible_flag and state_count_exclusion_reason is not null)
   or (not state_count_eligible_flag and state_count_exclusion_reason is null)
   or coalesce(state_count_exclusion_reason, 'included') !=
       case when not confirmed_recruiting_flag then 'not_confirmed_recruiting'
            when geography_category = 'missing' then 'missing_geography'
            when geography_category = 'unsupported' then 'unsupported_geography'
            else 'included' end
