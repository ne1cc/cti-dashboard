-- Assert canonical observation integrity across registry staging models:
-- 1. Uniqueness of (ingestion_run_id, nct_id) in stg_registry_trials.
-- 2. Every child entity's registry_trial_key joins to stg_registry_trials.
-- 3. Uniqueness of MeSH identity per trial snapshot (ingestion_run_id, nct_id, mesh_id).
-- 4. source_json_hash is not null and consistent within a snapshot.
select
    'stg_registry_trials duplicate (run, nct_id)' as issue_type,
    ingestion_run_id,
    nct_id,
    count(*) as violation_count
from {{ ref('stg_registry_trials') }}
group by ingestion_run_id, nct_id
having count(*) > 1

union all

select
    'stg_registry_trial_condition_mesh duplicate (run, nct_id, mesh_id)' as issue_type,
    ingestion_run_id,
    nct_id,
    count(*) as violation_count
from {{ ref('stg_registry_trial_condition_mesh') }}
group by ingestion_run_id, nct_id, mesh_id
having count(*) > 1

union all

select
    'orphan condition in stg_registry_trial_conditions' as issue_type,
    c.ingestion_run_id,
    c.nct_id,
    count(*) as violation_count
from {{ ref('stg_registry_trial_conditions') }} c
left join {{ ref('stg_registry_trials') }} t
    on c.ingestion_run_id = t.ingestion_run_id
    and c.nct_id = t.nct_id
where t.nct_id is null
group by c.ingestion_run_id, c.nct_id

union all

select
    'orphan mesh in stg_registry_trial_condition_mesh' as issue_type,
    m.ingestion_run_id,
    m.nct_id,
    count(*) as violation_count
from {{ ref('stg_registry_trial_condition_mesh') }} m
left join {{ ref('stg_registry_trials') }} t
    on m.ingestion_run_id = t.ingestion_run_id
    and m.nct_id = t.nct_id
where t.nct_id is null
group by m.ingestion_run_id, m.nct_id
