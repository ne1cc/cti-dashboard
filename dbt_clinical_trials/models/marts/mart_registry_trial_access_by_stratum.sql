-- Access shares by rural-urban group and social vulnerability quartile at
-- canonical registry grain. Grain: mesh_id x stratum_type x stratum_value.
with strata as (
    select *, 'overall' as stratum_type, 'all' as stratum_value
    from {{ ref('mart_registry_trial_access_by_geo') }}
    union all
    select *, 'ruca_group', coalesce(ruca_group, 'unclassified')
    from {{ ref('mart_registry_trial_access_by_geo') }}
    union all
    select *, 'svi_quartile', coalesce('Q' || svi_quartile, 'unclassified')
    from {{ ref('mart_registry_trial_access_by_geo') }}
)

select
    mesh_id,
    any_value(registry_condition_key) as registry_condition_key,
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
group by mesh_id, stratum_type, stratum_value
