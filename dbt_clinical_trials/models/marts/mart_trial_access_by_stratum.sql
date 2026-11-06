-- Access shares by rural-urban group and social vulnerability quartile (Issue #72).
-- Grain: profile x mesh_id x stratum_type x stratum_value, where stratum_type is
-- 'overall', 'ruca_group' (USDA ERS RUCA 2020, county's dominant group) or
-- 'svi_quartile' (CDC/ATSDR SVI 2022 overall percentile, national county rank,
-- Q1 least to Q4 most vulnerable). Each stratum_type partitions the same
-- counties, so its populations add up to the 'overall' row. Counties missing a
-- classification fall in 'unclassified'. Shares are population-weighted by
-- county ACS residents and inherit every limit of mart_trial_access_by_geo
-- (straight-line distance, centroid error); see docs/access_methodology.md.
with strata as (
    select *, 'overall' as stratum_type, 'all' as stratum_value
    from {{ ref('mart_trial_access_by_geo') }}
    union all
    select *, 'ruca_group', coalesce(ruca_group, 'unclassified')
    from {{ ref('mart_trial_access_by_geo') }}
    union all
    select *, 'svi_quartile', coalesce('Q' || svi_quartile, 'unclassified')
    from {{ ref('mart_trial_access_by_geo') }}
)

select
    indication_profile_id,
    mesh_id,
    any_value(condition_label) as condition_label,
    stratum_type,
    stratum_value,
    count(*) as county_count,
    cast(sum(population) as bigint) as population,
    cast(coalesce(sum(population) filter (where within_25mi), 0) as bigint) as population_within_25mi,
    cast(coalesce(sum(population) filter (where within_50mi), 0) as bigint) as population_within_50mi,
    cast(coalesce(sum(population) filter (where within_100mi), 0) as bigint) as population_within_100mi,
    round(coalesce(sum(population) filter (where within_25mi), 0) / nullif(sum(population), 0), 4)
        as share_within_25mi,
    round(coalesce(sum(population) filter (where within_50mi), 0) / nullif(sum(population), 0), 4)
        as share_within_50mi,
    round(coalesce(sum(population) filter (where within_100mi), 0) / nullif(sum(population), 0), 4)
        as share_within_100mi,
    any_value(sites_total) as sites_total,
    any_value(sites_exact) as sites_exact,
    any_value(sites_zip_approx) as sites_zip_approx,
    any_value(sites_city_approx) as sites_city_approx,
    any_value(sites_excluded) as sites_excluded,
    any_value(coverage_confidence) as coverage_confidence,
    any_value(access_basis) as access_basis
from strata
group by indication_profile_id, mesh_id, stratum_type, stratum_value
