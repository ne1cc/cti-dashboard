-- Sponsor concentration per segment: profile + condition_group + state +
-- phase + snapshot_date. HHI = sum of squared lead-sponsor shares (0..1 scale);
-- a concentration signal from public records, not a market-share claim.
with activity as (
    select * from {{ ref('int_condition_geography_activity') }}
    where overall_status = 'RECRUITING'
),

sponsor_counts as (
    select
        snapshot_date,
        indication_profile_id,
        condition_group,
        state_normalized,
        phase_normalized,
        lead_sponsor_normalized,
        count(distinct nct_id) as sponsor_trial_count
    from activity
    group by 1, 2, 3, 4, 5, 6
),

segment_totals as (
    select
        snapshot_date,
        indication_profile_id,
        condition_group,
        state_normalized,
        phase_normalized,
        sum(sponsor_trial_count) as segment_trial_count,
        count(distinct lead_sponsor_normalized) as sponsor_count
    from sponsor_counts
    group by 1, 2, 3, 4, 5
)

select
    t.snapshot_date,
    t.indication_profile_id,
    t.condition_group,
    t.state_normalized,
    t.phase_normalized,
    t.sponsor_count,
    t.segment_trial_count,
    max({{ safe_divide('c.sponsor_trial_count', 't.segment_trial_count') }})
        as top_sponsor_share,
    sum(
        power({{ safe_divide('c.sponsor_trial_count', 't.segment_trial_count') }}, 2)
    ) as sponsor_hhi
from segment_totals t
inner join sponsor_counts c
    using (
        snapshot_date, indication_profile_id, condition_group,
        state_normalized, phase_normalized
    )
group by 1, 2, 3, 4, 5, 6, 7
