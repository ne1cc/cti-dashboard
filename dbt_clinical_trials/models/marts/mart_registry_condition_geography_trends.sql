-- Monthly condition x geography trend view, per preset.
-- Grain: preset_id x month (snapshot month) x condition_group x state.
with activity as (
    select * from {{ ref('int_registry_condition_geography_activity') }}
),

state_pop as (
    select * from {{ ref('state_population') }}
),

snapshot_calendar as (
    select distinct
        b.preset_id,
        f.snapshot_date,
        date_trunc('month', f.snapshot_date) as snapshot_month
    from {{ ref('fct_registry_trial_snapshot') }} f
    inner join {{ ref('bridge_preset_trial') }} b
        on f.nct_id = b.nct_id
        and f.snapshot_date = b.snapshot_date
        and f.snapshot_id = b.snapshot_id
    where not b.is_unassigned
),

month_history as (
    select
        preset_id,
        snapshot_month as activity_month,
        datediff('day', min(first_observed), max(latest_observed))
            as observed_history_days
    from (
        select
            preset_id,
            snapshot_month,
            min(snapshot_date) over running_history as first_observed,
            max(snapshot_date) over running_history as latest_observed
        from snapshot_calendar
        window running_history as (
            partition by preset_id order by snapshot_month
        )
    )
    group by 1, 2
),

monthly as (
    select
        preset_id,
        date_trunc('month', snapshot_date) as activity_month,
        condition_group,
        state_normalized,
        max(snapshot_date) as latest_snapshot_date,
        count(distinct nct_id) as trial_count,
        count(distinct nct_id) filter (overall_status = 'RECRUITING')
            as recruiting_trial_count,
        count(distinct nct_id) filter (
            study_first_post_date is not null
            and date_trunc('month', study_first_post_date)
                = date_trunc('month', snapshot_date)
        ) as newly_registered_in_month,
        count(distinct lead_sponsor_normalized) as sponsor_count
    from activity
    group by 1, 2, 3, 4
),

windowed as (
    select
        *,
        avg(recruiting_trial_count) over trailing_3m
            as recruiting_trial_count_3m_avg,
        first_value(recruiting_trial_count) over trailing_3m
            as recruiting_count_3m_baseline,
        min(activity_month) over trailing_3m as window_first_month,
        first_value(latest_snapshot_date) over trailing_3m as baseline_snapshot_date
    from monthly
    window trailing_3m as (
        partition by preset_id, condition_group, state_normalized
        order by activity_month
        range between interval 3 months preceding and current row
    )
)

select
    w.preset_id,
    w.activity_month,
    w.condition_group,
    {{ generate_surrogate_key(["'group:' || w.condition_group"]) }} as registry_condition_key,
    w.state_normalized,
    w.trial_count,
    w.recruiting_trial_count,
    w.newly_registered_in_month,
    w.sponsor_count,
    w.recruiting_trial_count_3m_avg,
    w.recruiting_count_3m_baseline,
    h.observed_history_days,
    case
        when datediff('month', w.window_first_month, w.activity_month) >= 3
            and w.baseline_snapshot_date <= w.latest_snapshot_date - interval 3 month
            and h.observed_history_days >= 90
            then {{ safe_divide(
                'w.recruiting_trial_count - w.recruiting_count_3m_baseline',
                'w.recruiting_count_3m_baseline',
            ) }}
        else null
    end as recruiting_growth_3m,
    sp.population,
    case when sp.population is not null then 'U.S. Census Bureau' else null end as population_source,
    case when sp.population is not null then sp.vintage else null end as population_vintage,
    case
        when sp.population is not null and sp.population > 0 then
            round(100000.0 * cast(w.recruiting_trial_count as double) / cast(sp.population as double), 4)
        else null
    end as recruiting_trials_per_100k
from windowed w
inner join month_history h
    on h.preset_id = w.preset_id
    and h.activity_month = w.activity_month
left join state_pop sp
    on w.state_normalized = sp.state_code
