-- One row per preset per NCT ID (current canonical record in that preset):
-- the fields needed to score pairwise trial comparability against another trial
-- within the preset.
with current_trials as (
    select
        b.preset_id,
        c.snapshot_id,
        c.snapshot_date,
        c.nct_id
    from {{ ref('int_registry_current_trial_status') }} c
    inner join {{ ref('bridge_preset_trial') }} b
        on c.nct_id = b.nct_id
        and c.snapshot_date = b.snapshot_date
        and c.snapshot_id = b.snapshot_id
        and b.current_record_flag = true
    where not b.is_unassigned
),

trial_fields as (
    select
        c.preset_id,
        c.snapshot_id,
        t.registry_trial_key,
        t.nct_id,
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
        t.minimum_age_years,
        t.maximum_age_years,
        t.start_date,
        t.completion_date
    from {{ ref('stg_registry_trials') }} t
    inner join current_trials c
        on t.snapshot_id = c.snapshot_id
        and t.nct_id = c.nct_id
),

conditions as (
    select
        m.preset_id,
        m.nct_id,
        list(distinct m.condition_group) as condition_groups
    from {{ ref('int_registry_preset_condition_mapping') }} m
    inner join current_trials c
        on m.preset_id = c.preset_id
        and m.snapshot_id = c.snapshot_id
        and m.nct_id = c.nct_id
    group by 1, 2
),

interventions as (
    select
        c.preset_id,
        i.nct_id,
        list(distinct i.intervention_type) as intervention_types
    from {{ ref('stg_registry_trial_interventions') }} i
    inner join current_trials c
        on i.snapshot_id = c.snapshot_id
        and i.nct_id = c.nct_id
    where i.intervention_type is not null
    group by 1, 2
),

geography as (
    select
        c.preset_id,
        f.nct_id,
        list(distinct f.state_normalized) as states
    from {{ ref('fct_registry_trial_site') }} f
    inner join current_trials c
        on f.snapshot_id = c.snapshot_id
        and f.nct_id = c.nct_id
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
    on tf.nct_id = co.nct_id and tf.preset_id = co.preset_id
left join interventions iv
    on tf.nct_id = iv.nct_id and tf.preset_id = iv.preset_id
left join geography g
    on tf.nct_id = g.nct_id and tf.preset_id = g.preset_id
