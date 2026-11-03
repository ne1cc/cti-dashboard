-- One row per NCT ID + raw condition + ingestion run, with the
-- config-driven taxonomy overlay mapping applied upstream in silver.
-- mapping_source records how the group was assigned: yaml_overlay (a
-- taxonomy rule matched) or text_fallback (Issue #52 — MeSH browse terms
-- carry the identity for unmatched trials).
{#- Retained pre-upgrade runs have no mapping_source column in their Parquet
    (union_by_name only NULL-fills when at least one file defines it), so when
    no input file carries the column the same rule is applied directly to the
    legacy confidence/group columns instead of referencing the absent name.
    Mixed trees keep the coalesce: per-file NULLs still infer truthfully. -#}
{%- set has_mapping_source = silver_entity_has_parquet_column(
        'silver_trial_conditions', 'mapping_source') %}
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    condition_raw,
    condition_normalized,
    condition_group,
    cast(dementia_relevance_flag as boolean) as dementia_relevance_flag,
    mapping_confidence,
    {%- if has_mapping_source %}
    coalesce(
        mapping_source,
        case
            when mapping_confidence != 'low' and condition_group != 'unmapped' then 'yaml_overlay'
            else 'text_fallback'
        end
    ) as mapping_source,
    {%- else %}
    case
        when mapping_confidence != 'low' and condition_group != 'unmapped' then 'yaml_overlay'
        else 'text_fallback'
    end as mapping_source,
    {%- endif %}
    source_json_hash
from {{ source('silver', 'silver_trial_conditions') }}
where nct_id is not null
