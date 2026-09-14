-- Deterministic trial-pair comparability. Grain: one row per
-- (indication_profile_id, nct_id_a, nct_id_b) pair, where nct_id_b is one of
-- nct_id_a's top-25 most comparable trials within that profile's query scope.
-- A structural/design comparability signal --
-- NOT a claim of clinical equivalence, and NOT itself a competition or
-- recruitment signal. Weights live in the similarity_score_weights seed
-- (mirrored in config/similarity_weights.yml). The factor list lives in
-- the similarity_components macro.
with weights as (
    select
        {% for c in similarity_components() %}
        max(case when component = '{{ c }}' then weight end) as w_{{ c }}{{ "," if not loop.last }}
        {% endfor %}
    from {{ ref('similarity_score_weights') }}
),

pairs as (
    select
        a.indication_profile_id as indication_profile_id,
        a.nct_id as nct_id_a,
        b.nct_id as nct_id_b,
        a.phase_normalized as a_phase, b.phase_normalized as b_phase,
        a.enrollment_band as a_enrollment_band, b.enrollment_band as b_enrollment_band,
        case when len(list_intersect(a.condition_groups, b.condition_groups)) > 0
            then 1 else 0 end as same_condition,
        case when a.phase_normalized = b.phase_normalized
            then 1 else 0 end as same_phase,
        case when len(list_intersect(a.states, b.states)) > 0
            then 1 else 0 end as geography_overlap,
        case when len(list_intersect(a.intervention_types, b.intervention_types)) > 0
            then 1 else 0 end as intervention_type_overlap,
        case when a.study_type = b.study_type
            and a.allocation = b.allocation
            and a.primary_purpose = b.primary_purpose
            then 1 else 0 end as study_design_match,
        case when (a.sex = b.sex or a.sex = 'ALL' or b.sex = 'ALL')
            and a.healthy_volunteers = b.healthy_volunteers
            and (a.minimum_age_years is null or b.maximum_age_years is null
                 or a.minimum_age_years <= b.maximum_age_years)
            and (b.minimum_age_years is null or a.maximum_age_years is null
                 or b.minimum_age_years <= a.maximum_age_years)
            then 1 else 0 end as eligibility_compatible,
        case when a.enrollment_band = b.enrollment_band
            then 1 else 0 end as enrollment_band_match
    from {{ ref('int_trial_comparability_features') }} a
    inner join {{ ref('int_trial_comparability_features') }} b
        on a.nct_id != b.nct_id
        and a.indication_profile_id = b.indication_profile_id
),

scored as (
    select
        p.*,
        {% for c in similarity_components() %}
        w.w_{{ c }} as weight_{{ c }},
        round(w.w_{{ c }} * p.{{ c }}, 4) as weighted_{{ c }},
        {% endfor %}
        round(
            {% for c in similarity_components() %}
            w.w_{{ c }} * p.{{ c }}
            {%- if not loop.last %} +{% endif %}
            {% endfor %}
        , 4) as similarity_score
    from pairs p
    cross join weights w
)

select
    {{ generate_surrogate_key([
        'indication_profile_id', 'nct_id_a', 'nct_id_b',
    ]) }} as trial_similarity_key,
    indication_profile_id,
    nct_id_a,
    nct_id_b,
    similarity_score,
    row_number() over (
        partition by indication_profile_id, nct_id_a
        order by similarity_score desc, nct_id_b
    ) as similarity_rank,
    {% for c in similarity_components() %}
    {{ c }}, weight_{{ c }}, weighted_{{ c }},
    {% endfor %}
    concat_ws(
        '; ',
        case when same_condition = 1 then 'shared condition mapping' end,
        case when same_phase = 1 then 'same phase (' || a_phase || ')' end,
        case when geography_overlap = 1 then 'overlapping U.S. states' end,
        case when intervention_type_overlap = 1 then 'shared intervention type' end,
        case when study_design_match = 1 then 'matching study type, allocation, and primary purpose' end,
        case when eligibility_compatible = 1 then 'compatible eligibility criteria' end,
        case when enrollment_band_match = 1 then 'similar enrollment size (' || a_enrollment_band || ')' end
    ) as similarity_explanation
from scored
qualify similarity_rank <= 25
