-- The silver flattener deduplicates MeSH rows (direct wins over ancestor for
-- the same id), so one trial snapshot can never carry the same mesh id twice.
-- Failure rows are the (run, profile, nct_id, mesh_id) combinations that do.
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    mesh_id,
    count(*) as row_count
from {{ ref('stg_trial_condition_mesh') }}
group by 1, 2, 3, 4
having count(*) > 1
