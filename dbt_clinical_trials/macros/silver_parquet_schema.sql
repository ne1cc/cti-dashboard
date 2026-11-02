{#-
    Schema-aware reads over the silver Parquet tree (Issue #52 review round 2,
    finding 1). Retained historical silver predates this PR's two additions: its
    condition files carry no mapping_source column and no run ever produced a
    silver_trial_condition_mesh file. union_by_name NULL-fills a column only
    when at least one input file defines it (absent everywhere → Binder Error),
    and read_parquet raises on a glob that matches no files. Staging therefore
    probes the inputs once at compile time and downgrades exactly those two
    paths — nothing else is skipped, so genuinely missing data still fails the
    build loudly.

    Path convention mirrors the silver source meta in models/staging/_sources.yml
    (data/silver/<entity>/*.parquet). The probe queries run on the DuckDB
    connection, so relative globs resolve the same way the sources do: through
    file_search_path where profiles.yml sets it, otherwise the dbt process cwd.
    During `dbt parse` (execute=false) no connection exists, so both probes
    default to the current-schema read path.
-#}
{% macro silver_entity_has_parquet_files(entity) %}
    {%- if execute -%}
        {%- set found = run_query(
            "select count(*) as n from glob('data/silver/" ~ entity ~ "/*.parquet')"
        ) -%}
        {{ return(found.columns[0].values()[0] | int > 0) }}
    {%- else -%}
        {{ return(true) }}
    {%- endif -%}
{% endmacro %}


{% macro silver_entity_has_parquet_column(entity, column) %}
    {%- if execute -%}
        {#- An entity directory with no files still raises here (IOException):
            that is deliberate — only the legacy-column case may downgrade. -#}
        {%- set found = run_query(
            "select count(*) as n from parquet_schema('data/silver/"
            ~ entity ~ "/*.parquet') where name = '" ~ column ~ "'"
        ) -%}
        {{ return(found.columns[0].values()[0] | int > 0) }}
    {%- else -%}
        {{ return(true) }}
    {%- endif -%}
{% endmacro %}


{% macro validate_mesh_extraction_integrity() %}
    {%- if execute -%}
        {#- Identify extraction state per run (Issue #52 round 3, finding 2).
            Every new run writes a silver_trial_condition_mesh Parquet file, even
            with zero rows. If any run whose condition file carries mapping_source
            lacks a corresponding mesh Parquet file, fail loudly instead of
            silently inferring MeSH absence. Retained historical runs (old-only)
            carry no mapping_source and legitimately produce no mesh file. -#}
        {%- set check_sql -%}
        with new_schema_runs as (
            select distinct regexp_extract(file_name, 'run_id=([^.]+)\.parquet', 1) as run_id
            from parquet_schema('data/silver/silver_trial_conditions/*.parquet')
            where name = 'mapping_source'
        ),
        mesh_runs as (
            select distinct regexp_extract(file, 'run_id=([^.]+)\.parquet', 1) as run_id
            from glob('data/silver/silver_trial_condition_mesh/*.parquet')
        )
        select n.run_id
        from new_schema_runs n
        left join mesh_runs m on n.run_id = m.run_id
        where m.run_id is null
        {%- endset -%}
        {%- set missing = run_query(check_sql) -%}
        {%- if missing and missing.rows | length > 0 -%}
            {%- set bad_runs = [] -%}
            {%- for row in missing.rows -%}
                {%- do bad_runs.append(row[0]) -%}
            {%- endfor -%}
            {{ exceptions.raise_compiler_error(
                "Missing silver_trial_condition_mesh file for new-schema run(s) carrying mapping_source: "
                ~ bad_runs | join(', ')
            ) }}
        {%- endif -%}
    {%- endif -%}
{% endmacro %}
