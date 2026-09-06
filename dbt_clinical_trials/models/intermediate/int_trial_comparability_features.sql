-- One row per profile per NCT ID (current record from that profile's latest
-- snapshot): the fields needed to score pairwise trial comparability against
-- another trial. Presentation-neutral -- assembles inputs only;
-- mart_trial_similarity does the actual pairwise scoring.
with current_trials as (
    select ingestion_run_id, indication_profile_id, nct_id
    from {{ ref('int_current_trial_status') }}
),

trial_fields as (
    select
        t.nct_id,
        t.indication_profile_id,
        t.phase_normalized,
        t.study_type,
        t.allocation,
        t.primary_purpose,
        t.enrollment_count,
        case
            when t.enrollment_count is null then null
            when t.enrollment_count < 50 then 'small'
            when t.enrollment_count <= 200 then 'medium'
            else 'large'
        end as enrollment_band,
        t.healthy_volunteers,
        t.sex,
        {{ parse_age_years('t.minimum_age') }} as minimum_age_years,
        {{ parse_age_years('t.maximum_age') }} as maximum_age_years,
        t.start_date,
        t.completion_date
    from {{ ref('stg_trials') }} t
    inner join current_trials c
        on t.ingestion_run_id = c.ingestion_run_id
        and t.indication_profile_id = c.indication_profile_id
        and t.nct_id = c.nct_id
),

-- Grouped by profile as well as trial: the ADRD and NSCLC taxonomies map the
-- same condition_raw to different condition_group values, so an unprofiled
-- list(distinct condition_group) would credit an NSCLC trial with ADRD's
-- condition groups and inflate same_condition in both directions.
conditions as (
    select
        indication_profile_id,
        nct_id,
        list(distinct condition_group) as condition_groups
    from {{ ref('bridge_trial_condition') }}
    group by 1, 2
),

interventions as (
    select
        i.indication_profile_id,
        i.nct_id,
        list(distinct i.intervention_type) as intervention_types
    from {{ ref('stg_trial_interventions') }} i
    inner join current_trials c
        on i.ingestion_run_id = c.ingestion_run_id
        and i.indication_profile_id = c.indication_profile_id
        and i.nct_id = c.nct_id
    where i.intervention_type is not null
    group by 1, 2
),

latest_site_snapshot as (
    select indication_profile_id, max(snapshot_date) as snapshot_date
    from {{ ref('fct_trial_site') }}
    group by 1
),

geography as (
    select
        f.indication_profile_id,
        f.nct_id,
        list(distinct f.state_normalized) as states
    from {{ ref('fct_trial_site') }} f
    inner join latest_site_snapshot ls
        using (indication_profile_id, snapshot_date)
    where f.state_normalized is not null
    group by 1, 2
)

select
    tf.*,
    coalesce(co.condition_groups, []) as condition_groups,
    coalesce(iv.intervention_types, []) as intervention_types,
    coalesce(g.states, []) as states
from trial_fields tf
left join conditions co
    on tf.nct_id = co.nct_id and tf.indication_profile_id = co.indication_profile_id
left join interventions iv
    on tf.nct_id = iv.nct_id and tf.indication_profile_id = iv.indication_profile_id
left join geography g
    on tf.nct_id = g.nct_id and tf.indication_profile_id = g.indication_profile_id
