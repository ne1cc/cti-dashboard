-- One row per NCT ID + MeSH browse identity + ingestion run at canonical registry grain.
{{ validate_registry_mesh_extraction_integrity() }}
select
    {{ generate_surrogate_key(['nct_id']) }} as registry_trial_key,
    ingestion_run_id,
    nct_id,
    mesh_id,
    mesh_term,
    cast(is_ancestor as boolean) as is_ancestor,
    branch,
    source_json_hash
from {{ source('silver', 'registry_trial_condition_mesh') }}
where nct_id is not null
