-- Geographic access to open trial sites by MeSH condition node at canonical
-- registry grain. Grain: mesh_id x county_fips.
with current_trials as (
    select snapshot_id, nct_id
    from {{ ref('int_registry_current_trial_status') }}
    where overall_status in ('RECRUITING', 'NOT_YET_RECRUITING')
        and active_in_latest_snapshot_flag
),

gz_zcta as ({{ gazetteer_zcta() }}),

gz_place as ({{ gazetteer_place() }}),

open_listings as (
    select
        c.nct_id,
        c.snapshot_id,
        l.location_ordinal,
        {{ generate_surrogate_key([
            'l.facility_normalized', 'l.city_normalized', 'l.state_normalized',
        ]) }} as site_key,
        l.latitude,
        l.longitude,
        l.zip_code,
        l.city_normalized,
        l.state_normalized
    from current_trials c
    inner join {{ ref('stg_registry_trial_locations') }} l
        on c.snapshot_id = l.snapshot_id
        and c.nct_id = l.nct_id
    where l.usable_geography_flag
        and l.location_status in ('RECRUITING', 'NOT_YET_RECRUITING')
),

listing_coords as (
    select
        o.nct_id,
        o.site_key,
        case
            when o.latitude is not null and o.longitude is not null then o.latitude
            when z.zcta is not null then z.latitude
            else p.latitude
        end as latitude,
        case
            when o.latitude is not null and o.longitude is not null then o.longitude
            when z.zcta is not null then z.longitude
            else p.longitude
        end as longitude,
        case
            when o.latitude is not null and o.longitude is not null then 'exact'
            when z.zcta is not null then 'zip'
            when p.state_usps is not null then 'city'
        end as coordinate_basis
    from open_listings o
    left join gz_zcta z on left(o.zip_code, 5) = z.zcta
    left join gz_place p
        on p.state_usps = o.state_normalized
        and (p.city_name = o.city_normalized or p.city_name_stripped = o.city_normalized)
    qualify row_number() over (
        partition by o.snapshot_id, o.nct_id, o.location_ordinal
        order by case when z.zcta is not null then 0 else 1 end, p.latitude, p.longitude
    ) = 1
),

sites as (
    select site_key, latitude, longitude, coordinate_basis
    from listing_coords
    qualify row_number() over (
        partition by site_key
        order by
            case coordinate_basis when 'exact' then 0 when 'zip' then 1 when 'city' then 2 else 3 end,
            latitude, longitude
    ) = 1
),

node_sites as (
    select distinct b.mesh_id, b.registry_condition_key, lc.site_key
    from listing_coords lc
    inner join {{ ref('bridge_registry_trial_condition') }} b
        on lc.nct_id = b.nct_id
    where b.mesh_id is not null
),

node_coverage as (
    select
        n.mesh_id,
        n.registry_condition_key,
        count(*) as sites_total,
        count(*) filter (where s.coordinate_basis = 'exact') as sites_exact,
        count(*) filter (where s.coordinate_basis = 'zip') as sites_zip_approx,
        count(*) filter (where s.coordinate_basis = 'city') as sites_city_approx,
        count(*) filter (where s.coordinate_basis is null) as sites_excluded
    from node_sites n
    inner join sites s using (site_key)
    group by 1, 2
),

counties as (
    select
        c.county_fips,
        c.county_name,
        s.state_code,
        c.latitude,
        c.longitude,
        c.ruca_group,
        c.ruca_group_pop_share,
        case when c.svi_rpl_themes is not null
            then least(4, cast(floor(c.svi_rpl_themes / 0.25) as integer) + 1)
        end as svi_quartile,
        p.population,
        p.vintage as population_vintage
    from {{ ref('county_access_context') }} c
    left join {{ ref('state_population') }} s on c.state_fips = s.fips_code
    left join {{ ref('dim_population') }} p
        on p.geo_level = 'county' and p.geo_id = c.county_fips
        and p.sex = 'all' and p.age_band = 'all' and p.race_ethnicity = 'all'
),

site_county_distance as (
    select * from (
        select
            s.site_key,
            c.county_fips,
            2 * 3958.8 * asin(sqrt(greatest(0.0, least(1.0,
                power(sin(radians(s.latitude - c.latitude) / 2.0), 2)
                + cos(radians(c.latitude)) * cos(radians(s.latitude))
                * power(sin(radians(s.longitude - c.longitude) / 2.0), 2)
            )))) as miles
        from sites s
        inner join counties c
            on s.coordinate_basis is not null
            and abs(s.latitude - c.latitude) <= 100.0 / 69.0
            and abs(s.longitude - c.longitude)
                <= 100.0 / (69.0 * greatest(0.05, cos(radians(least(abs(c.latitude) + 1.5, 89.0)))))
    )
    where miles <= 100.0
),

node_county as (
    select
        n.mesh_id,
        d.county_fips,
        min(d.miles) as nearest_site_miles,
        count(*) filter (where d.miles <= 25) as sites_within_25mi,
        count(*) filter (where d.miles <= 50) as sites_within_50mi,
        count(*) as sites_within_100mi
    from node_sites n
    inner join site_county_distance d using (site_key)
    group by 1, 2
),

node_labels as (
    select mesh_id, condition_display_name as condition_label
    from {{ ref('dim_registry_condition') }}
    where mesh_id is not null
)

select
    v.mesh_id,
    v.registry_condition_key,
    l.condition_label,
    c.county_fips,
    c.county_name,
    c.state_code,
    c.population,
    c.population_vintage,
    c.svi_quartile,
    c.ruca_group,
    c.ruca_group_pop_share,
    round(d.nearest_site_miles, 1) as nearest_site_miles,
    coalesce(d.sites_within_25mi, 0) as sites_within_25mi,
    coalesce(d.sites_within_50mi, 0) as sites_within_50mi,
    coalesce(d.sites_within_100mi, 0) as sites_within_100mi,
    coalesce(d.sites_within_25mi, 0) > 0 as within_25mi,
    coalesce(d.sites_within_50mi, 0) > 0 as within_50mi,
    coalesce(d.sites_within_100mi, 0) > 0 as within_100mi,
    v.sites_total,
    v.sites_exact,
    v.sites_zip_approx,
    v.sites_city_approx,
    v.sites_excluded,
    round(1.0 * v.sites_exact / nullif(v.sites_total - v.sites_excluded, 0), 4)
        as exact_coordinate_share,
    case
        when v.sites_total - v.sites_excluded = 0 then 'none'
        when 1.0 * v.sites_exact / (v.sites_total - v.sites_excluded) >= 0.9
            and 1.0 * v.sites_excluded / v.sites_total <= 0.05 then 'high'
        when 1.0 * v.sites_exact / (v.sites_total - v.sites_excluded) >= 0.7
            and 1.0 * v.sites_excluded / v.sites_total <= 0.15 then 'medium'
        else 'low'
    end as coverage_confidence,
    'straight-line haversine miles, county centroid to site; open site = site and trial '
        || 'status RECRUITING or NOT_YET_RECRUITING; see docs/access_methodology.md'
        as access_basis
from node_coverage v
cross join counties c
left join node_county d
    on v.mesh_id = d.mesh_id
    and c.county_fips = d.county_fips
left join node_labels l
    on v.mesh_id = l.mesh_id
