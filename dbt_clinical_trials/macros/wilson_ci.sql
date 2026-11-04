{% macro wilson_ci_lower(numerator, denominator, z=1.96) %}
    case
        when {{ denominator }} is null or {{ denominator }} = 0 then null
        else greatest(
            0.0,
            (cast({{ numerator }} as double) + ({{ z }} * {{ z }} / 2.0) - {{ z }} * sqrt((cast({{ numerator }} as double) * (cast({{ denominator }} as double) - cast({{ numerator }} as double)) / cast({{ denominator }} as double)) + ({{ z }} * {{ z }} / 4.0))) / (cast({{ denominator }} as double) + ({{ z }} * {{ z }}))
        )
    end
{% endmacro %}

{% macro wilson_ci_upper(numerator, denominator, z=1.96) %}
    case
        when {{ denominator }} is null or {{ denominator }} = 0 then null
        else least(
            1.0,
            (cast({{ numerator }} as double) + ({{ z }} * {{ z }} / 2.0) + {{ z }} * sqrt((cast({{ numerator }} as double) * (cast({{ denominator }} as double) - cast({{ numerator }} as double)) / cast({{ denominator }} as double)) + ({{ z }} * {{ z }} / 4.0))) / (cast({{ denominator }} as double) + ({{ z }} * {{ z }}))
        )
    end
{% endmacro %}
