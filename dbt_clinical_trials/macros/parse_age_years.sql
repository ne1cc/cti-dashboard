{#- ClinicalTrials.gov ages are free text like "18 Years", "6 Months", "N/A".
    Parses supported registry age units into fractional years.
    Returns null for missing, unbounded, N/A, or unparseable input.
    Non-numeric or absent input must be treated as "no restriction" / unstated, not zero. -#}
{% macro parse_age_years(column) %}
    case
        when {{ column }} is null or trim({{ column }}) = '' or upper(trim({{ column }})) in ('N/A', 'NA', 'NOT APPLICABLE') then null
        when regexp_matches({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*years?\s*$') then
            try_cast(regexp_extract({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*years?\s*$', 1) as double)
        when regexp_matches({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*months?\s*$') then
            try_cast(regexp_extract({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*months?\s*$', 1) as double) / 12.0
        when regexp_matches({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*weeks?\s*$') then
            try_cast(regexp_extract({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*weeks?\s*$', 1) as double) / 52.0
        when regexp_matches({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*days?\s*$') then
            try_cast(regexp_extract({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*days?\s*$', 1) as double) / 365.0
        when regexp_matches({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*hours?\s*$') then
            try_cast(regexp_extract({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*hours?\s*$', 1) as double) / 8760.0
        when regexp_matches({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*minutes?\s*$') then
            try_cast(regexp_extract({{ column }}, '(?i)^\s*(\d+(?:\.\d+)?)\s*minutes?\s*$', 1) as double) / 525600.0
        else null
    end
{% endmacro %}
