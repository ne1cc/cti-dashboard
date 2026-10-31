-- One row per NCT ID + MeSH browse identity + ingestion run (Issue #52).
-- Deduplication (direct wins over ancestor for the same mesh id) happened in
-- silver; the (run, profile, nct_id, mesh_id) grain is unique here.
{#- Retained historical silver has no silver_trial_condition_mesh files at all
    (the entity did not exist before Issue #52, and runs whose bronze was
    pruned can never produce one). read_parquet raises on a file-less glob, so
    an absent entity reads as a typed empty relation — same columns and types
    as a real empty run file — instead of failing the whole upgrade. A present
    entity is always read in full; only the file-less case downgrades. -#}
{%- if not silver_entity_has_parquet_files('silver_trial_condition_mesh') %}
select
    cast(null as varchar) as ingestion_run_id,
    cast(null as varchar) as indication_profile_id,
    cast(null as varchar) as nct_id,
    cast(null as varchar) as mesh_id,
    cast(null as varchar) as mesh_term,
    cast(null as boolean) as is_ancestor,
    cast(null as varchar) as branch,
    cast(null as varchar) as source_json_hash
where false
{% else %}
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
{%- endif %}
