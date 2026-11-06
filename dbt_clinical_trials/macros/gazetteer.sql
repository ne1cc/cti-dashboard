{#- Census Gazetteer centroid relations for the dbt build (Issue #72).
    Reads the same files `make gazetteer` writes for the read API
    (src/api/gazetteer.py, CTI_GAZETTEER_DIR, default data/reference). When a file is
    missing the macro returns an empty relation with the same columns, so the build
    still succeeds and the mart reports every ZIP/city-only site as excluded. Column
    names are the Census originals, as in src/api/gazetteer.py. -#}
{% macro _gazetteer_source(filename) %}
    {%- set path = env_var('CTI_GAZETTEER_DIR', 'data/reference') ~ '/' ~ filename -%}
    {%- set found = 0 -%}
    {%- if execute -%}
        {%- set found = run_query("select count(*) from glob('" ~ path | replace("'", "''") ~ "')").columns[0].values()[0] -%}
    {%- endif -%}
    {%- if found > 0 -%}
        read_csv('{{ path | replace("'", "''") }}', header = true, all_varchar = true)
    {%- else -%}
        (select null::varchar as "USPS", null::varchar as "GEOID", null::varchar as "NAME",
                null::varchar as "INTPTLAT", null::varchar as "INTPTLONG" where false)
    {%- endif -%}
{% endmacro %}

{% macro gazetteer_zcta() %}
    select
        "GEOID" as zcta,
        cast("INTPTLAT" as double) as latitude,
        cast("INTPTLONG" as double) as longitude
    from {{ _gazetteer_source('gazetteer_zcta.csv') }}
{% endmacro %}

{# Place names carry a trailing type word in some releases ("Seattle city"); registry
   cities never do, so expose the full and the suffix-stripped form. #}
{% macro gazetteer_place() %}
    select
        "USPS" as state_usps,
        {{ normalize_text('"NAME"') }} as city_name,
        regexp_replace(
            {{ normalize_text('"NAME"') }},
            ' (city|town|township|village|borough|cdp|municipality|plantation|balance|territory)$',
            ''
        ) as city_name_stripped,
        cast("INTPTLAT" as double) as latitude,
        cast("INTPTLONG" as double) as longitude
    from {{ _gazetteer_source('gazetteer_place.csv') }}
{% endmacro %}
