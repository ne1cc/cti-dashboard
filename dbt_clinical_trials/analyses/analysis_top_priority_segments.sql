-- Top segments awaiting human feasibility review, ordered by active
-- recruiting trials with raw metrics and mandatory interpretation note.
-- Compile with: dbt compile --select analysis_top_priority_segments
select
    indication_profile_id,
    condition_group,
    state_normalized,
    phase_normalized,
    active_recruiting_trials,
    recruiting_trial_count,
    distinct_sponsors,
    sponsor_hhi,
    site_overlap_rate,
    priority_explanation,
    interpretation_note
from {{ ref('mart_feasibility_priority_queue') }}
order by indication_profile_id, active_recruiting_trials desc, distinct_sponsors desc, priority_queue_key asc
limit 25
