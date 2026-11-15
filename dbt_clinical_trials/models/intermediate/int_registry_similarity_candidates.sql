-- Bounded candidate pairs for mart_registry_trial_similarity.
-- Grain: one row per (preset_id, nct_id_a, nct_id_b), in both orientations.
{{ config(materialized='table') }}

{% set max_token_df = var('similarity_max_token_df') %}

with tokens as (
    select
        m.preset_id,
        m.nct_id,
        m.condition_group || '|' || features.phase_normalized as token
    from {{ ref('int_registry_preset_condition_mapping') }} as m
    inner join {{ ref('int_registry_trial_comparability_features') }} as features
        on m.preset_id = features.preset_id
        and m.snapshot_id = features.snapshot_id
        and m.nct_id = features.nct_id
),

token_frequency as (
    select
        preset_id,
        token,
        count(distinct nct_id) as trial_count
    from tokens
    group by 1, 2
),

selective_tokens as (
    select tokens.preset_id, tokens.nct_id, tokens.token
    from tokens
    inner join token_frequency
        on tokens.preset_id = token_frequency.preset_id
        and tokens.token = token_frequency.token
    where token_frequency.trial_count < {{ max_token_df }}
),

unordered_pairs as (
    select distinct
        a.preset_id,
        a.nct_id as nct_low,
        b.nct_id as nct_high
    from selective_tokens a
    inner join selective_tokens b
        on a.preset_id = b.preset_id
        and a.token = b.token
        and a.nct_id < b.nct_id
)

select
    {{ generate_surrogate_key(['preset_id', 'nct_low', 'nct_high']) }} as candidate_pair_key,
    preset_id,
    nct_low as nct_id_a,
    nct_high as nct_id_b
from unordered_pairs

union all

select
    {{ generate_surrogate_key(['preset_id', 'nct_high', 'nct_low']) }} as candidate_pair_key,
    preset_id,
    nct_high as nct_id_a,
    nct_low as nct_id_b
from unordered_pairs
