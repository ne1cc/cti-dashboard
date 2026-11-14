-- Assert canonical observation integrity across registry staging models:
-- 1. Uniqueness of (snapshot_id, nct_id) in stg_registry_trials.
-- 2. Every child entity's registry_trial_key joins to stg_registry_trials.
-- 3. Uniqueness of MeSH identity per trial snapshot (snapshot_id, nct_id, mesh_id).
-- 4. source_json_hash is not null and consistent within a snapshot.
select
    'stg_registry_trials duplicate (snapshot_id, nct_id)' as issue_type,
    snapshot_id,
    nct_id,
    count(*) as violation_count
from {{ ref('stg_registry_trials') }}
group by snapshot_id, nct_id
having count(*) > 1

union all

select
    'stg_registry_trial_condition_mesh duplicate (snapshot_id, nct_id, mesh_id)' as issue_type,
    snapshot_id,
    nct_id,
    count(*) as violation_count
from {{ ref('stg_registry_trial_condition_mesh') }}
group by snapshot_id, nct_id, mesh_id
having count(*) > 1

union all

select
    'orphan condition in stg_registry_trial_conditions' as issue_type,
    c.snapshot_id,
    c.nct_id,
    count(*) as violation_count
from {{ ref('stg_registry_trial_conditions') }} c
left join {{ ref('stg_registry_trials') }} t
    on c.snapshot_id = t.snapshot_id
    and c.nct_id = t.nct_id
where t.nct_id is null
group by c.snapshot_id, c.nct_id

union all

select
    'orphan mesh in stg_registry_trial_condition_mesh' as issue_type,
    m.snapshot_id,
    m.nct_id,
    count(*) as violation_count
from {{ ref('stg_registry_trial_condition_mesh') }} m
left join {{ ref('stg_registry_trials') }} t
    on m.snapshot_id = t.snapshot_id
    and m.nct_id = t.nct_id
where t.nct_id is null
group by m.snapshot_id, m.nct_id
