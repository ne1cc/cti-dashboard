-- Baseline-demographics coverage: per indication profile, condition group, and
-- completion year, the share of COMPLETED trials with posted results whose
-- baseline characteristics (resultsSection.baselineCharacteristicsModule)
-- report sex, age, race, or ethnicity. Companion transparency columns expose
-- how much of that reporting the OMB mapping could not classify: trials with
-- ambiguous combined race/ethnicity tables (flagged, never guessed — issue #74).
--
-- Denominator: trials in their profile's latest snapshot (int_current_trial_status),
-- COMPLETED, with posted results, joined to their condition groups. Trials with
-- no condition group have no place in a per-condition coverage mart and drop out.
-- Completion year may be null when the registry date is missing or unparseable;
-- those trials stay in the mart on a null-year row instead of being hidden.
with current_trials as (
    select nct_id, indication_profile_id, ingestion_run_id
    from {{ ref('int_current_trial_status') }}
),

trial_scope as (
    select
        t.nct_id,
        t.indication_profile_id,
        t.completion_date,
        m.condition_group
    from {{ ref('stg_trials') }} t
    inner join {{ ref('int_trial_condition_mapping') }} m
        on t.ingestion_run_id = m.ingestion_run_id
        and t.indication_profile_id = m.indication_profile_id
        and t.nct_id = m.nct_id
    inner join current_trials c
        on t.ingestion_run_id = c.ingestion_run_id
        and t.indication_profile_id = c.indication_profile_id
        and t.nct_id = c.nct_id
    where t.overall_status = 'COMPLETED'
      and t.has_results_flag
),

denominator as (
    select
        indication_profile_id,
        condition_group,
        year(completion_date) as completion_year,
        nct_id
    from trial_scope
),

demographics as (
    select distinct
        nct_id,
        indication_profile_id,
        dimension,
        resolved_dimension,
        is_ambiguous_combined_flag
    from {{ ref('stg_trial_baseline_demographics') }}
),

per_trial as (
    select
        b.indication_profile_id,
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
        and d.indication_profile_id = b.indication_profile_id
    group by 1, 2, 3, 4
)

select
    indication_profile_id,
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
        as ambiguous_combined_share
from per_trial
group by 1, 2, 3
