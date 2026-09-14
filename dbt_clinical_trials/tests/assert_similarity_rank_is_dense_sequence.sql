-- Per profile per nct_id_a, similarity_rank must be a dense 1..N sequence (no
-- gaps, no duplicates), capped at 25 by the mart's own qualify filter.
with per_trial as (
    select
        indication_profile_id,
        nct_id_a,
        count(*) as row_count,
        count(distinct similarity_rank) as distinct_ranks,
        max(similarity_rank) as max_rank,
        min(similarity_rank) as min_rank
    from {{ ref('mart_trial_similarity') }}
    group by indication_profile_id, nct_id_a
)
select *
from per_trial
where row_count != distinct_ranks
   or max_rank != row_count
   or min_rank != 1
