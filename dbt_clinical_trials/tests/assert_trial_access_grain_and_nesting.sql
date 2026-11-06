-- Access marts: one row per profile x node x county; a wider radius never reaches
-- fewer sites; each stratum type partitions the same people as the overall row.
with duplicates as (
    select indication_profile_id, mesh_id, county_fips
    from {{ ref('mart_trial_access_by_geo') }}
    group by 1, 2, 3
    having count(*) > 1
),

not_nested as (
    select indication_profile_id, mesh_id, county_fips
    from {{ ref('mart_trial_access_by_geo') }}
    where sites_within_25mi > sites_within_50mi
        or sites_within_50mi > sites_within_100mi
        or (within_100mi and nearest_site_miles > 100)
),

not_partitioned as (
    select s.indication_profile_id, s.mesh_id, s.stratum_type
    from {{ ref('mart_trial_access_by_stratum') }} s
    inner join {{ ref('mart_trial_access_by_stratum') }} o
        on s.indication_profile_id = o.indication_profile_id
        and s.mesh_id = o.mesh_id
        and o.stratum_type = 'overall'
    where s.stratum_type <> 'overall'
    group by s.indication_profile_id, s.mesh_id, s.stratum_type, o.population, o.population_within_50mi
    having sum(s.population) is distinct from o.population
        or sum(s.population_within_50mi) is distinct from o.population_within_50mi
)

select indication_profile_id, mesh_id from duplicates
union all
select indication_profile_id, mesh_id from not_nested
union all
select indication_profile_id, mesh_id from not_partitioned
