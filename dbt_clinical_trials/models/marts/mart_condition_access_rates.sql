-- Population-adjusted access by condition (MeSH node) and state, with an
-- estimated-case rate only where a confirmed burden source exists.
-- Grain: profile x mesh_id x state. Counts are recruiting listings (trials with
-- at least one usable U.S. site in the state) in each trial's current record,
-- so a multi-state trial counts once per state; states with no listing for the
-- condition do not appear.
--
-- Two rates, never mixed:
--   recruiting_trials_per_100k          per 100,000 residents (ACS total population)
--   recruiting_trials_per_1k_est_cases  per 1,000 estimated cases, only when
--                                       burden_denominator_available is true
-- Raw counts are never presented as need: when the burden denominator is
-- missing, the estimated-case columns are null and denominator_note says so.
-- Incident-case burdens (NSCLC) and prevalent-case burdens (diabetes) differ in
-- kind; compare rates only within one condition.
with current_trials as (
    select ingestion_run_id, indication_profile_id, nct_id
    from {{ ref('int_current_trial_status') }}
    where overall_status = 'RECRUITING' and active_in_latest_snapshot_flag
),

trial_states as (
    select distinct
        c.indication_profile_id,
        c.nct_id,
        g.state_normalized as state_code
    from current_trials c
    inner join {{ ref('int_geography_normalized') }} g
        on c.ingestion_run_id = g.ingestion_run_id
        and c.indication_profile_id = g.indication_profile_id
        and c.nct_id = g.nct_id
),

node_states as (
    select
        b.indication_profile_id,
        b.mesh_id,
        s.state_code,
        count(distinct b.nct_id) as recruiting_trial_count
    from {{ ref('bridge_trial_condition') }} b
    inner join trial_states s
        on b.indication_profile_id = s.indication_profile_id
        and b.nct_id = s.nct_id
    where b.mesh_id is not null
    group by 1, 2, 3
),

node_labels as (
    select indication_profile_id, mesh_id, condition_normalized as condition_label
    from {{ ref('dim_condition') }}
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

    -- PLACES: crude prevalence x adult population, summed over every county of
    -- the state; the committed extract holds complete states only.
    select
        'places_diabetes_county' as burden_source_id,
        state_code,
        sum(prevalence_pct / 100.0 * adult_population) as estimated_cases
    from {{ ref('burden_places_county') }}
    where measure_id = 'DIABETES'
    group by state_code
)

select
    n.indication_profile_id,
    n.mesh_id,
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
    -- Row-level: the map says a source is confirmed AND this state has a value.
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
    on n.indication_profile_id = l.indication_profile_id and n.mesh_id = l.mesh_id
left join state_pop p on n.state_code = p.state_code
left join {{ ref('condition_burden_map') }} m on n.mesh_id = m.mesh_id
left join state_burden b
    on m.burden_source_id = b.burden_source_id
    and n.state_code = b.state_code
