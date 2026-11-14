-- Baseline characteristics (who actually enrolled) from
-- resultsSection.baselineCharacteristicsModule at canonical registry grain,
-- with sponsor-reported labels mapped onto OMB race/ethnicity categories
-- via the omb_race_ethnicity_mapping seed.
with raw as (
    select
        {{ generate_surrogate_key(['nct_id']) }} as registry_trial_key,
        ingestion_run_id,
        snapshot_id,
        retrieved_at_utc,
        raw_page_reference,
        cast(raw_study_ordinal as bigint) as raw_study_ordinal,
        nct_id,
        source_json_hash,
        cast(measure_index as integer) as measure_index,
        measure_title,
        param_type,
        unit_of_measure,
        cast(class_index as integer) as class_index,
        class_title,
        cast(category_index as integer) as category_index,
        category_title,
        group_id,
        group_title,
        try_cast(group_denominator as integer) as group_denominator,
        try_cast(participant_count as integer) as participant_count,
        value_raw,
        value_spread
    from {{ source('silver', 'registry_trial_baseline_demographics') }}
    where nct_id is not null
),

lowered as (
    select *, lower(coalesce(measure_title, '')) as lower_title
    from raw
),

classified as (
    -- lower_title flows through the CTEs and is dropped by the final select.
    select
        *,
        case
            when regexp_matches(lower_title, '\brace\b')
                and regexp_matches(lower_title, '\bethnic')
                and regexp_matches(lower_title, 'not collected')
                then 'race_ethnicity_not_collected'
            when regexp_matches(lower_title, '\brace\b')
                and regexp_matches(lower_title, '\bethnic')
                then 'race_ethnicity_combined'
            when regexp_matches(lower_title, '\bethnic') then 'ethnicity'
            when regexp_matches(lower_title, '\brace\b') then 'race'
            when regexp_matches(lower_title, '\b(sex|gender)\b') then 'sex'
            when regexp_matches(lower_title, '\bage\b') then 'age'
            else 'other'
        end as dimension,
        -- Lookup label: reported label with an explicit dimension prefix removed
        -- ("Race: White" -> "WHITE"); upper + collapsed spaces so the reviewed
        -- seed labels match sponsor casing and spacing variants.
        regexp_replace(
            regexp_replace(
                upper(trim(coalesce(category_title, class_title))),
                '\s+', ' ', 'g'
            ),
            '^(RACE|ETHNICITY):\s*', '', 'g'
        ) as lookup_label,
        case
            when upper(trim(coalesce(category_title, class_title))) like 'RACE:%'
                then 'race'
            when upper(trim(coalesce(category_title, class_title))) like 'ETHNICITY:%'
                then 'ethnicity'
            else null
        end as prefix_dimension
    from lowered
),

labeled as (
    select
        *,
        coalesce(
            case
                when dimension in ('race', 'ethnicity') then dimension
                else null
            end,
            prefix_dimension,
            case
                when dimension = 'race_ethnicity_combined'
                    and upper(trim(class_title)) in ('RACE', 'ETHNICITY')
                    then lower(trim(class_title))
                else null
            end
        ) as mapping_dimension
    from classified
),

mapped as (
    select
        l.*,
        m.omb_category,
        m.mapping_confidence,
        cast(m.manually_reviewed as boolean) as mapping_manually_reviewed,
        coalesce(l.mapping_dimension, m.applies_to) as resolved_dimension,
        -- Coverage counts the mapping's reach: a race/ethnicity row that kept
        -- only its reported label has no OMB category. Sex/age/other rows are
        -- not mapping targets and are never flagged.
        (m.omb_category is null
            and l.dimension in ('race', 'ethnicity', 'race_ethnicity_combined'))
            as mapping_unmapped_flag,
        (l.dimension = 'race_ethnicity_combined'
            and coalesce(l.mapping_dimension, m.applies_to) is null)
            as is_ambiguous_combined_flag,
        {{ safe_divide('l.participant_count', 'l.group_denominator') }}
            as percent_of_group_denominator
    from labeled l
    left join {{ ref('omb_race_ethnicity_mapping') }} m
        on regexp_replace(upper(trim(m.source_label_normalized)), '\s+', ' ', 'g')
            = l.lookup_label
        and l.dimension in ('race', 'ethnicity', 'race_ethnicity_combined')
        and (
            -- Measure- or prefix-resolved dimension: the seed row must agree
            -- ("both" rows only apply where the measure itself is
            -- single-dimension).
            (
                l.mapping_dimension is not null
                and (
                    m.applies_to = l.mapping_dimension
                    or (
                        m.applies_to = 'both'
                        and l.mapping_dimension in ('race', 'ethnicity')
                    )
                )
            )
            -- A combined table without a per-label dimension hint may still map
            -- labels the reviewed seed itself scopes to one dimension
            -- ("White" -> race); dimension-neutral ("both") labels stay unmapped.
            or (l.mapping_dimension is null and m.applies_to != 'both')
        )
)

select
    registry_trial_key,
    ingestion_run_id,
    snapshot_id,
    nct_id,
    source_json_hash,
    raw_page_reference,
    raw_study_ordinal,
    retrieved_at_utc,
    measure_index,
    measure_title,
    param_type,
    unit_of_measure,
    class_index,
    class_title,
    category_index,
    category_title,
    group_id,
    group_title,
    group_denominator,
    participant_count,
    percent_of_group_denominator,
    value_raw,
    value_spread,
    dimension,
    resolved_dimension,
    omb_category,
    mapping_confidence,
    mapping_manually_reviewed,
    mapping_unmapped_flag,
    is_ambiguous_combined_flag
from mapped
