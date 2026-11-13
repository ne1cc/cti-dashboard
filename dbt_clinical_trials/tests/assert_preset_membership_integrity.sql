-- Assert preset membership integrity across bridge_preset_trial and registry dimensions:
-- 1. Uniqueness of (preset_id, nct_id, snapshot_date) in bridge_preset_trial.
-- 2. At most one current_record_flag per (preset_id, nct_id).
-- 3. Mutual exclusivity: no trial is both in an assigned preset and unassigned in the same snapshot.
-- 4. Registry completeness: every trial in dim_registry_trial has a current record in bridge_preset_trial.
-- 5. Foreign key validity: registry_trial_key joins to dim_registry_trial.

select
    'duplicate trial in preset snapshot' as issue_type,
    preset_id,
    nct_id,
    count(*) as violation_count
from {{ ref('bridge_preset_trial') }}
group by preset_id, nct_id, snapshot_date
having count(*) > 1

union all

select
    'multiple current records for trial in preset' as issue_type,
    preset_id,
    nct_id,
    count(*) as violation_count
from {{ ref('bridge_preset_trial') }}
where current_record_flag
group by preset_id, nct_id
having count(*) > 1

union all

select
    'trial in both assigned preset and unassigned scope' as issue_type,
    'unassigned' as preset_id,
    u.nct_id,
    count(*) as violation_count
from {{ ref('bridge_preset_trial') }} u
inner join {{ ref('bridge_preset_trial') }} a
    on u.nct_id = a.nct_id
    and u.snapshot_date = a.snapshot_date
    and u.is_unassigned
    and not a.is_unassigned
group by u.nct_id

union all

select
    'current trial missing from bridge_preset_trial' as issue_type,
    'none' as preset_id,
    d.nct_id,
    1 as violation_count
from {{ ref('dim_registry_trial') }} d
left join {{ ref('bridge_preset_trial') }} b
    on d.nct_id = b.nct_id
    and b.current_record_flag
where b.nct_id is null

union all

select
    'orphan registry_trial_key in bridge_preset_trial' as issue_type,
    b.preset_id,
    b.nct_id,
    count(*) as violation_count
from {{ ref('bridge_preset_trial') }} b
left join {{ ref('dim_registry_trial') }} d
    on b.nct_id = d.nct_id
    and b.registry_trial_key = d.registry_trial_key
where d.nct_id is null
group by b.preset_id, b.nct_id
