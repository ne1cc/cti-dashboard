-- Segment-level trial activity per snapshot, per indication profile.
-- Grain: profile x snapshot_date x condition_group x state x phase x
-- overall_status. Counts are counts of registry listings, not patient
-- availability.
select
    indication_profile_id,
    snapshot_date,
    condition_group,
    state_normalized,
    phase_normalized,
    overall_status,
    count(distinct nct_id) as trial_count,
    count(distinct lead_sponsor_normalized) as sponsor_count,
    sum(listed_site_count_in_state) as listed_site_count,
    count(distinct nct_id) filter (entered_recruiting_flag)
        as entered_recruiting_count,
    count(distinct nct_id) filter (left_recruiting_flag)
        as left_recruiting_count,
    count(distinct nct_id) filter (dementia_relevance_flag)
        as dementia_relevant_trial_count,
    count(distinct nct_id) filter (record_quality_flag != 'ok')
        as flagged_record_count
from {{ ref('int_condition_geography_activity') }}
group by 1, 2, 3, 4, 5, 6
