-- Candidate trial pairs for mart_trial_similarity: the bounded discovery
-- stage. Grain: one row per (indication_profile_id, nct_id_a, nct_id_b), in
-- both orientations — the mart's per-trial top-25 rank reads pairs in
-- (nct_id_a, nct_id_b) order, so every unordered pair is emitted twice.
--
-- Why candidates, not all pairs: the discovery this replaces was a bare
-- self-join of int_trial_comparability_features within a profile, which
-- materialized and scored N^2 pairs; at the twelve-profile registry that
-- measured 28 GB of DuckDB temporary storage without completing
-- (docs/DEPLOY_FLY.md, "Adding indication profiles", 2026-10-04). Candidates
-- are pairs sharing at least one condition-group token: the inverted index
-- over bridge_trial_condition turns discovery into a join on
-- (indication_profile_id, token), whose cost is the sum of per-token
-- frequency squares instead of N^2. Scoring is unchanged — the mart still
-- scores every factor on every candidate; only discovery narrowed, and
-- tests/test_similarity_candidates.py pins the mart's output to the old
-- logic restricted to these candidates.
--
-- Deliberate semantic narrowing: a trial sharing no surviving token with any
-- other trial gets zero candidates and the mart emits zero rows for it,
-- instead of the unbounded join's 25 minimum-score rows. Tokens whose
-- per-profile trial count reaches similarity_max_token_df are dropped: a
-- registry-scale profile's dominant group (the taxonomy catch-all) would
-- otherwise push the candidate join back toward N^2.
--
-- A token is (condition_group, phase_normalized), not condition_group alone:
-- the ten profiles added 2026-10 have two to four populated groups each, so
-- at full registry scale every group in oncology_colorectal and
-- type2_diabetes reached the cap and those profiles got zero pairs
-- (2026-10-19 twelve-profile build, docs/DEPLOY_FLY.md). Phase splits each
-- group five to eight ways, and same_condition + same_phase are the two
-- heaviest-weighted factors a candidate pair can match on together.
--
-- Materialized as a table, not the project's intermediate default view: the
-- token self-join is the expensive CTE and the mart consumes it twice (once
-- per side of the scoring join); a view would recompute it.
{{ config(materialized='table') }}

{% set max_token_df = var('similarity_max_token_df') %}

with tokens as (
    select
        bridge.indication_profile_id,
        bridge.nct_id,
        bridge.condition_group || '|' || features.phase_normalized as token
    from {{ ref('bridge_trial_condition') }} as bridge
    inner join {{ ref('int_trial_comparability_features') }} as features
        on bridge.indication_profile_id = features.indication_profile_id
        and bridge.nct_id = features.nct_id
),

token_frequency as (
    select
        indication_profile_id,
        token,
        count(distinct nct_id) as trial_count
    from tokens
    group by 1, 2
),

selective_tokens as (
    select tokens.indication_profile_id, tokens.nct_id, tokens.token
    from tokens
    inner join token_frequency
        on tokens.indication_profile_id = token_frequency.indication_profile_id
        and tokens.token = token_frequency.token
    where token_frequency.trial_count < {{ max_token_df }}
),

unordered_pairs as (
    select distinct
        a.indication_profile_id,
        a.nct_id as nct_low,
        b.nct_id as nct_high
    from selective_tokens a
    inner join selective_tokens b
        on a.indication_profile_id = b.indication_profile_id
        and a.token = b.token
        and a.nct_id < b.nct_id
)

select
    {{ generate_surrogate_key([
        'indication_profile_id', 'nct_low', 'nct_high',
    ]) }} as candidate_pair_key,
    indication_profile_id,
    nct_low as nct_id_a,
    nct_high as nct_id_b
from unordered_pairs

union all

select
    {{ generate_surrogate_key([
        'indication_profile_id', 'nct_high', 'nct_low',
    ]) }} as candidate_pair_key,
    indication_profile_id,
    nct_high as nct_id_a,
    nct_low as nct_id_b
from unordered_pairs
