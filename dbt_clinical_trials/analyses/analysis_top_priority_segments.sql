-- Top segments awaiting human feasibility review, with the deterministic
-- explanation and the mandatory interpretation note.
-- Compile with: dbt compile --select analysis_top_priority_segments
select
    indication_profile_id,
    priority_rank,
    condition_group,
    state_normalized,
    phase_normalized,
    feasibility_review_priority_score,
    priority_band,
    recruiting_trial_count,
    sponsor_hhi,
    site_overlap_share,
    priority_explanation,
    interpretation_note
from {{ ref('mart_feasibility_priority_queue') }}
order by indication_profile_id, priority_rank
limit 25
