-- Population-adjusted access by MeSH condition node and U.S. state at canonical
-- registry grain. Grain: mesh_id x state_code.
-- Presets filter by MeSH nodes or bridge_preset_trial at read time.
with current_trials as (
    select snapshot_id, nct_id
    from {{ ref('int_registry_current_trial_status') }}
    where overall_status = 'RECRUITING' and active_in_latest_snapshot_flag
),

trial_states as (
    select distinct
        c.nct_id,
        g.state_normalized as state_code
    from current_trials c
    inner join {{ ref('int_registry_geography_normalized') }} g
        on c.snapshot_id = g.snapshot_id
        and c.nct_id = g.nct_id
),

node_states as (
    select
        b.mesh_id,
        b.registry_condition_key,
        s.state_code,
        count(distinct b.nct_id) as recruiting_trial_count
    from {{ ref('bridge_registry_trial_condition') }} b
    inner join trial_states s
        on b.nct_id = s.nct_id
    where b.mesh_id is not null
    group by 1, 2, 3
),

node_labels as (
    select mesh_id, condition_display_name as condition_label
    from {{ ref('dim_registry_condition') }}
    where mesh_id is not null
),

state_pop as (
    select state_code, population, vintage
    from {{ ref('dim_population') }}
    where geo_level = 'state' and sex = 'all' and age_band = 'all' and race_ethnicity = 'all'
),

state_burden as (
    select
        'scp_lung_state' as burden_source_id,
        s.state_code,
        cast(b.annual_cases as double) as estimated_cases
    from {{ ref('burden_scp_lung_state') }} b
    inner join {{ ref('state_population') }} s on b.state_fips = s.fips_code
    where b.annual_cases is not null

    union all

    select
        'places_diabetes_county' as burden_source_id,
        state_code,
        sum(prevalence_pct / 100.0 * adult_population) as estimated_cases
    from {{ ref('burden_places_county') }}
    where measure_id = 'DIABETES'
    group by state_code
)

select
    n.mesh_id,
    n.registry_condition_key,
    l.condition_label,
    n.state_code,
    n.recruiting_trial_count,
    p.population,
    'U.S. Census Bureau ACS 5-year, table B01001' as population_source,
    p.vintage as population_vintage,
    round(100000.0 * n.recruiting_trial_count / nullif(p.population, 0), 4)
        as recruiting_trials_per_100k,
    m.burden_source,
    m.burden_measure,
    m.burden_vintage,
    coalesce(m.denominator_available and b.estimated_cases is not null, false)
        as burden_denominator_available,
    case when m.denominator_available then round(b.estimated_cases, 0) end
        as estimated_cases,
    case
        when m.denominator_available and b.estimated_cases > 0 then
            round(1000.0 * n.recruiting_trial_count / b.estimated_cases, 4)
    end as recruiting_trials_per_1k_est_cases,
    case
        when m.denominator_available and b.estimated_cases is not null then
            'per 1,000 estimated cases (' || m.burden_measure || '; '
            || m.burden_source || ', ' || m.burden_vintage || ') and per 100,000 residents ('
            || p.vintage || ')'
        else 'per 100,000 residents (' || coalesce(p.vintage, 'no population estimate')
            || '); no burden denominator available'
    end as denominator_label
from node_states n
left join node_labels l
    on n.mesh_id = l.mesh_id
left join state_pop p on n.state_code = p.state_code
left join {{ ref('condition_burden_map') }} m on n.mesh_id = m.mesh_id
left join state_burden b
    on m.burden_source_id = b.burden_source_id
    and n.state_code = b.state_code
