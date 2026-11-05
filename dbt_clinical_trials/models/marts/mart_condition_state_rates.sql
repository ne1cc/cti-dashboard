-- Population- and burden-adjusted recruiting access (Issue #71). Grain: profile
-- x MeSH condition node x U.S. state, including states with zero recruiting
-- trials (a zero is a finding for access, so the grid is dense).
--
-- Two denominators, never mixed:
--   * recruiting_trials_per_100k: ACS 5-year resident population (dim_population,
--     dimension 'total'). Available for every state.
--   * recruiting_trials_per_1k_est_cases: estimated cases from the condition's
--     burden source (condition_burden_map). Only where denominator_available.
-- Where no burden denominator exists the burden columns stay null and
-- denominator_label says 'no burden denominator available'; the per-100k rate is
-- population-adjusted access, not need, and must not be read as a burden rate.
-- Unmapped MeSH nodes get the same treatment.
with nodes as (
    select distinct indication_profile_id, node_id, condition_normalized as condition_label
    from {{ ref('dim_condition') }}
    where mesh_id is not null
),

states as (
    select state_code, population, table_id, vintage, source_name
    from {{ ref('dim_population') }}
    where geo_level = 'state' and dimension = 'total' and state_code is not null
),

recruiting as (
    select
        b.indication_profile_id,
        b.mesh_id as node_id,
        g.state_normalized as state_code,
        count(distinct b.nct_id) as recruiting_trial_count
    from {{ ref('bridge_trial_condition') }} b
    inner join {{ ref('int_current_trial_status') }} c
        on b.nct_id = c.nct_id and b.indication_profile_id = c.indication_profile_id
    inner join {{ ref('int_geography_normalized') }} g
        on c.ingestion_run_id = g.ingestion_run_id
        and c.indication_profile_id = g.indication_profile_id
        and c.nct_id = g.nct_id
    where b.mesh_id is not null and c.overall_status = 'RECRUITING'
    group by 1, 2, 3
),

-- One estimated-case figure per burden source/measure/state. PLACES is county
-- level: prevalence x adults 18+ summed over counties with an estimate, with the
-- counts alongside so partial coverage is visible.
burden as (
    select
        'seer_state_cancer_profiles' as burden_source_id,
        'LUNG_BRONCHUS_INCIDENCE' as burden_measure_id,
        sp.state_code,
        cast(s.average_annual_count as double) as estimated_cases,
        1 as county_count,
        1 as county_estimate_count
    from {{ ref('burden_seer_lung_state') }} s
    inner join {{ ref('state_population') }} sp on s.fips_code = sp.fips_code

    union all

    select
        'cdc_places_county' as burden_source_id,
        measure_id as burden_measure_id,
        state_code,
        sum(prevalence_pct / 100.0 * population_18plus) as estimated_cases,
        count(*) as county_count,
        count(prevalence_pct) as county_estimate_count
    from {{ ref('burden_places_county') }}
    group by measure_id, state_code
),

grid as (
    select n.*, s.state_code, s.population, s.table_id, s.vintage, s.source_name
    from nodes n
    cross join states s
)

select
    {{ generate_surrogate_key(['g.indication_profile_id', 'g.node_id', 'g.state_code']) }}
        as rate_key,
    g.indication_profile_id,
    g.node_id,
    g.condition_label,
    g.state_code,
    coalesce(r.recruiting_trial_count, 0) as recruiting_trial_count,
    g.population,
    g.source_name || ' ' || g.vintage || ' (table ' || g.table_id || ')' as population_source,
    g.vintage as population_vintage,
    round(100000.0 * coalesce(r.recruiting_trial_count, 0) / g.population, 4)
        as recruiting_trials_per_100k,
    m.burden_source_id,
    m.burden_source_name,
    m.burden_measure,
    m.source_vintage as burden_vintage,
    m.burden_unit,
    coalesce(m.denominator_available and b.estimated_cases > 0, false)
        as denominator_available,
    case when coalesce(m.denominator_available, false) then b.estimated_cases end
        as burden_estimated_cases,
    case when coalesce(m.denominator_available, false) then b.county_count end
        as burden_county_count,
    case when coalesce(m.denominator_available, false) then b.county_estimate_count end
        as burden_county_estimate_count,
    case
        when coalesce(m.denominator_available, false) and b.estimated_cases > 0
            then round(
                1000.0 * coalesce(r.recruiting_trial_count, 0) / b.estimated_cases, 4
            )
    end as recruiting_trials_per_1k_est_cases,
    case
        when coalesce(m.denominator_available, false) and b.estimated_cases > 0
            then 'Per 1,000 estimated cases: ' || m.burden_measure || ' ('
                || m.burden_source_name || ', ' || m.source_vintage || ')'
        else 'no burden denominator available'
    end as denominator_label,
    case
        when m.mesh_id is null then 'No burden source is mapped to this condition.'
        when not m.denominator_available then m.caveat
        when b.estimated_cases is null then 'The burden source has no estimate for this state.'
    end as denominator_note
from grid g
left join recruiting r
    on g.indication_profile_id = r.indication_profile_id
    and g.node_id = r.node_id
    and g.state_code = r.state_code
left join {{ ref('condition_burden_map') }} m
    on g.node_id = m.mesh_id
left join burden b
    on m.burden_source_id = b.burden_source_id
    and m.burden_measure_id = b.burden_measure_id
    and g.state_code = b.state_code
