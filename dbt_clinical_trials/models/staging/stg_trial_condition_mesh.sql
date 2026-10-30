-- One row per NCT ID + MeSH browse identity + ingestion run (Issue #52).
-- Deduplication (direct wins over ancestor for the same mesh id) happened in
-- silver; the (run, profile, nct_id, mesh_id) grain is unique here.
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    mesh_id,
    mesh_term,
    cast(is_ancestor as boolean) as is_ancestor,
    branch,
    source_json_hash
from {{ source('silver', 'silver_trial_condition_mesh') }}
where nct_id is not null
