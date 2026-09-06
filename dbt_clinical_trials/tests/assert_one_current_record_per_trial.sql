-- At most one current record per (profile, trial) in fct_trial_snapshot.
-- Detects duplicate current records only. It cannot detect the profile
-- collapse it was written around: `where current_record_flag` filters before
-- grouping, so a profile left with zero current rows produces no group at all
-- and `having count(*) > 1` never fires. Proving per-profile currency needs an
-- exactly-one assertion over enumerated (profile, nct_id) pairs and a fixture
-- whose two profiles land on different snapshot dates — both owned by Task 12.
select indication_profile_id, nct_id, count(*) as current_record_count
from {{ ref('fct_trial_snapshot') }}
where current_record_flag
group by indication_profile_id, nct_id
having count(*) > 1
