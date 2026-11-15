-- Singular data-quality invariants for Phase 4a canonical registry/preset models.
-- Each branch returns zero rows when invariants hold; any returned row fails dbt test.

-- 1. At most one current snapshot record per nct_id in fct_registry_trial_snapshot
select
    'fct_registry_trial_snapshot_multiple_current' as violation_type,
    nct_id as entity_id
from {{ ref('fct_registry_trial_snapshot') }}
where current_record_flag
group by nct_id
having count(*) > 1

union all

-- 2. Every site observation references an existing canonical trial snapshot
select
    'fct_registry_trial_site_orphan_snapshot' as violation_type,
    s.site_key as entity_id
from {{ ref('fct_registry_trial_site') }} s
left join {{ ref('fct_registry_trial_snapshot') }} f
    on s.registry_trial_key = f.registry_trial_key
    and s.snapshot_date = f.snapshot_date
where f.registry_trial_key is null

union all

-- 3. No self-similarity pairs in mart_registry_trial_similarity
select
    'mart_registry_trial_similarity_self_pair' as violation_type,
    preset_id || ':' || nct_id_a as entity_id
from {{ ref('mart_registry_trial_similarity') }}
where nct_id_a = nct_id_b

union all

-- 4. Similarity score stays within [0.0, 1.0]
select
    'mart_registry_trial_similarity_score_bounds' as violation_type,
    trial_similarity_key as entity_id
from {{ ref('mart_registry_trial_similarity') }}
where similarity_score < 0.0 or similarity_score > 1.0

union all

-- 5. Similarity rank per (preset_id, nct_id_a) is a dense 1..N sequence without gaps or ties
select
    'mart_registry_trial_similarity_rank_sequence' as violation_type,
    preset_id || ':' || nct_id_a as entity_id
from {{ ref('mart_registry_trial_similarity') }}
group by preset_id, nct_id_a
having
    min(similarity_rank) <> 1
    or max(similarity_rank) <> count(*)
    or count(distinct similarity_rank) <> count(*)

union all

-- 6. sponsor_hhi requires at least 2 distinct sponsors
select
    'mart_registry_priority_queue_hhi_min_sponsors' as violation_type,
    priority_queue_key as entity_id
from {{ ref('mart_registry_feasibility_priority_queue') }}
where distinct_sponsors < 2 and sponsor_hhi is not null

union all

-- 7. Condition geography trends 3m growth requires a 3-month window span
select
    'mart_registry_trends_growth_full_window' as violation_type,
    preset_id || ':' || cast(activity_month as varchar) || ':' || condition_group || ':' || state_normalized as entity_id
from (
    select
        preset_id,
        activity_month,
        condition_group,
        state_normalized,
        recruiting_growth_3m,
        min(activity_month) over (
            partition by preset_id, condition_group, state_normalized
            order by activity_month
            range between interval 3 months preceding and current row
        ) as window_first_month
    from {{ ref('mart_registry_condition_geography_trends') }}
) w
where recruiting_growth_3m is not null
  and datediff('month', window_first_month, activity_month) < 3

union all

-- 8. Trial access by geo site counts are non-decreasing: 25mi <= 50mi <= 100mi
select
    'mart_registry_trial_access_geo_radius_nesting' as violation_type,
    county_fips || ':' || mesh_id as entity_id
from {{ ref('mart_registry_trial_access_by_geo') }}
where sites_within_25mi > sites_within_50mi
   or sites_within_50mi > sites_within_100mi

union all

-- 9. Trial access by stratum population radii are non-decreasing: 25mi <= 50mi <= 100mi
select
    'mart_registry_trial_access_stratum_radius_nesting' as violation_type,
    registry_condition_key || ':' || stratum_type || ':' || stratum_value || ':' || access_basis as entity_id
from {{ ref('mart_registry_trial_access_by_stratum') }}
where population_within_25mi > population_within_50mi
   or population_within_50mi > population_within_100mi
