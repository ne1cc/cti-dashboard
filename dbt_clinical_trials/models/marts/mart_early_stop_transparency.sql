-- Early stop transparency mart: counts of terminated, withdrawn, and suspended
-- trials by condition group, sponsor class, and year, with the sponsor-stated
-- whyStopped reason passed through verbatim. Never categorizes reasons into
-- subjective judgements like "failure" (Issue #76).
--
-- Denominator: all terminated, withdrawn, and suspended trial records in each
-- indication profile's current snapshot.
with stopped_trials as (
    select
        d.trial_key,
        d.nct_id,
        d.indication_profile_id,
        d.current_overall_status as overall_status,
        d.why_stopped,
        cast(coalesce(
            year(d.primary_completion_date),
            year(d.completion_date),
            year(d.start_date),
            year(d.study_first_post_date)
        ) as integer) as stopped_year,
        coalesce(s.sponsor_class, 'UNKNOWN') as sponsor_class,
        coalesce(c.condition_group, 'unmapped') as condition_group
    from {{ ref('dim_trial') }} d
    left join {{ ref('bridge_trial_sponsor') }} s
        on d.trial_key = s.trial_key and s.lead_sponsor_flag
    left join {{ ref('bridge_trial_condition') }} c
        on d.trial_key = c.trial_key and c.mesh_id is null
    where d.current_overall_status in ('TERMINATED', 'WITHDRAWN', 'SUSPENDED')
)

select
    {{ generate_surrogate_key([
        'indication_profile_id',
        'condition_group',
        'sponsor_class',
        "coalesce(cast(stopped_year as varchar), '__null__')",
        'overall_status',
        "coalesce(why_stopped, '__none__')",
    ]) }} as early_stop_key,
    indication_profile_id,
    condition_group,
    sponsor_class,
    stopped_year,
    overall_status,
    why_stopped,
    count(distinct nct_id) as stopped_trial_count,
    string_agg(distinct nct_id, ', ' order by nct_id) as contributing_nct_ids
from stopped_trials
group by
    indication_profile_id,
    condition_group,
    sponsor_class,
    stopped_year,
    overall_status,
    why_stopped
