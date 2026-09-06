-- Every trial must have at most one current record *per profile* in
-- fct_trial_snapshot. Grouped on the full grain: without
-- indication_profile_id this test would fail the moment two profiles share a
-- trial, and passing it would have meant the collapse was still happening.
select indication_profile_id, nct_id, count(*) as current_record_count
from {{ ref('fct_trial_snapshot') }}
where current_record_flag
group by indication_profile_id, nct_id
having count(*) > 1
