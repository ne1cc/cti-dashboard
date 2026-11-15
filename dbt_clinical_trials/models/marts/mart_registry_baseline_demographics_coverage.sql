-- Baseline-demographics coverage per preset, condition group, and completion year.
-- Grain: preset_id x condition_group x completion_year.
with latest_authoritative_snapshot as (
    select max(h.snapshot_date) as snapshot_date
    from {{ ref('bridge_preset_trial') }} b
    inner join {{ ref('int_registry_trial_status_history') }} h
        on b.nct_id = h.nct_id
        and b.snapshot_date = h.snapshot_date
        and b.snapshot_id = h.snapshot_id
),

trial_scope as (
    select
        b.preset_id,
        t.nct_id,
        t.snapshot_id,
        t.completion_date,
        m.condition_group
    from {{ ref('bridge_preset_trial') }} b
    inner join latest_authoritative_snapshot l
        on b.snapshot_date = l.snapshot_date
    inner join {{ ref('int_registry_trial_status_history') }} h
        on b.nct_id = h.nct_id
        and b.snapshot_date = h.snapshot_date
        and b.snapshot_id = h.snapshot_id
    inner join {{ ref('stg_registry_trials') }} t
        on h.snapshot_id = t.snapshot_id
        and h.nct_id = t.nct_id
    inner join {{ ref('int_registry_preset_condition_mapping') }} m
        on b.preset_id = m.preset_id
        and h.snapshot_id = m.snapshot_id
        and h.nct_id = m.nct_id
    where not b.is_unassigned
      and t.overall_status = 'COMPLETED'
      and t.has_results_flag
),

denominator as (
    select
        preset_id,
        condition_group,
        snapshot_id,
        year(completion_date) as completion_year,
        nct_id
    from trial_scope
),

demographics as (
    select distinct
        nct_id,
        snapshot_id,
        dimension,
        resolved_dimension,
        is_ambiguous_combined_flag
    from {{ ref('stg_registry_trial_baseline_demographics') }}
),

per_trial as (
    select
        b.preset_id,
        b.condition_group,
        b.completion_year,
        b.nct_id,
        bool_or(d.dimension = 'sex') as reports_sex_flag,
        bool_or(d.dimension = 'age') as reports_age_flag,
        bool_or(d.resolved_dimension = 'race') as reports_race_flag,
        bool_or(d.resolved_dimension = 'ethnicity') as reports_ethnicity_flag,
        bool_or(d.is_ambiguous_combined_flag) as ambiguous_combined_flag
    from denominator b
    left join demographics d
        on d.nct_id = b.nct_id
        and d.snapshot_id = b.snapshot_id
    group by 1, 2, 3, 4
)

select
    preset_id,
    condition_group,
    completion_year,
    count(*) as trials_with_results_count,
    count(*) filter (reports_sex_flag) as reports_sex_count,
    count(*) filter (reports_age_flag) as reports_age_count,
    count(*) filter (reports_race_flag) as reports_race_count,
    count(*) filter (reports_ethnicity_flag) as reports_ethnicity_count,
    count(*) filter (ambiguous_combined_flag) as ambiguous_combined_trials_count,
    {{ safe_divide("count(*) filter (reports_sex_flag)", 'count(*)') }} as reports_sex_share,
    {{ safe_divide("count(*) filter (reports_age_flag)", 'count(*)') }} as reports_age_share,
    {{ safe_divide("count(*) filter (reports_race_flag)", 'count(*)') }} as reports_race_share,
    {{ safe_divide("count(*) filter (reports_ethnicity_flag)", 'count(*)') }}
        as reports_ethnicity_share,
    {{ safe_divide("count(*) filter (ambiguous_combined_flag)", 'count(*)') }}
        as ambiguous_combined_share,
    {{ generate_surrogate_key(["'group:' || condition_group"]) }} as registry_condition_key
from per_trial
group by 1, 2, 3
