-- registry_trial_key / registry_condition_key hash the registry identity only
-- (nct_id, node_id), so they repeat once per profile that lists the record.
-- Each model must still be unique at its own grain once the profile is added
-- back; a repeat means a join fanned out. Returns one row per violating key.
select 'dim_trial' as model_name, registry_trial_key as key_value, count(*) as row_count
from {{ ref('dim_trial') }}
group by registry_trial_key, indication_profile_id
having count(*) > 1

union all

select 'dim_condition', registry_condition_key, count(*)
from {{ ref('dim_condition') }}
group by registry_condition_key, indication_profile_id
having count(*) > 1

union all

select 'bridge_trial_condition', registry_trial_key || '/' || registry_condition_key, count(*)
from {{ ref('bridge_trial_condition') }}
group by registry_trial_key, registry_condition_key, indication_profile_id
having count(*) > 1
