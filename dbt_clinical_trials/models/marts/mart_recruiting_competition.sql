-- Potential competition signal per recruiting segment, per indication profile.
-- Grain: profile x snapshot_date x condition_group x state x phase.
-- Density proxy = count of RECRUITING registry listings; this is a
-- potential competition signal, not a recruitment forecast and not a
-- measure of patient availability.
-- Windowed recruiting-transition figures (new_recruiting_30d/90d,
-- recruiting_growth_90d) are unknown until the profile's retained snapshots
-- actually cover the window: they are null unless observed_history_days
-- reaches the window length AND the latest gap between snapshots does not
-- itself span the window -- a transition observed across a gap longer than
-- the window has unobserved timing inside it, so summing it into the window
-- would claim a precision the captures do not have. Null means "not enough
-- history"; zero is reserved for a covered window with no transitions.
-- newly_registered_90d counts registry study_first_post_date values, which
-- are fully observed from a single snapshot; it measures registrations,
-- never recruiting transitions.
-- density_percentile and competition_signal_band are distributions *within
-- one profile's* segments: partitioned by snapshot_date alone, an ADRD
-- segment would be ranked against NSCLC's segment sizes.
with activity as (
    select * from {{ ref('int_condition_geography_activity') }}
    where overall_status = 'RECRUITING'
),

snapshot_calendar as (
    select distinct indication_profile_id, snapshot_date
    from {{ ref('fct_trial_snapshot') }}
),

profile_history as (
    select
        indication_profile_id,
        snapshot_date,
        datediff('day', min(snapshot_date) over profile_window, snapshot_date)
            as observed_history_days,
        lag(snapshot_date) over profile_window as previous_snapshot_date
    from snapshot_calendar
    window profile_window as (
        partition by indication_profile_id order by snapshot_date
    )
),

profile_window_coverage as (
    select
        indication_profile_id,
        snapshot_date,
        observed_history_days,
        previous_snapshot_date,
        first_value(snapshot_date) over profile_30d as window_30d_earliest_date,
        first_value(previous_snapshot_date) over profile_30d as window_30d_earliest_pred_date,
        first_value(snapshot_date) over profile_90d as window_90d_earliest_date,
        first_value(previous_snapshot_date) over profile_90d as window_90d_earliest_pred_date
    from profile_history
    window
        profile_30d as (
            partition by indication_profile_id order by snapshot_date
            range between interval 30 day preceding and current row
        ),
        profile_90d as (
            partition by indication_profile_id order by snapshot_date
            range between interval 90 day preceding and current row
        )
),

segments as (
    select
        indication_profile_id,
        snapshot_date,
        condition_group,
        state_normalized,
        phase_normalized,
        count(distinct nct_id) as recruiting_trial_count,
        sum(listed_site_count_in_state) as listed_site_count,
        count(distinct nct_id) filter (entered_recruiting_flag)
            as entered_recruiting_count,
        count(distinct nct_id) filter (
            study_first_post_date is not null
            and study_first_post_date >= snapshot_date - interval 90 day
        ) as newly_registered_90d
    from activity
    group by 1, 2, 3, 4, 5
),

windowed as (
    select
        s.*,
        h.observed_history_days,
        h.previous_snapshot_date,
        h.window_30d_earliest_date,
        h.window_30d_earliest_pred_date,
        h.window_90d_earliest_date,
        h.window_90d_earliest_pred_date,
        sum(entered_recruiting_count) over segment_30d as new_recruiting_30d_raw,
        sum(entered_recruiting_count) over segment_90d as new_recruiting_90d_raw,
        first_value(recruiting_trial_count) over segment_90d
            as recruiting_count_90d_baseline,
        first_value(s.snapshot_date) over segment_90d
            as segment_90d_first_snapshot_date
    from segments s
    inner join profile_window_coverage h
        on h.indication_profile_id = s.indication_profile_id
        and h.snapshot_date = s.snapshot_date
    window
        segment_30d as (
            partition by
                s.indication_profile_id, s.condition_group,
                s.state_normalized, s.phase_normalized
            order by s.snapshot_date
            range between interval 30 days preceding and current row
        ),
        segment_90d as (
            partition by
                s.indication_profile_id, s.condition_group,
                s.state_normalized, s.phase_normalized
            order by s.snapshot_date
            range between interval 90 days preceding and current row
        )
),

-- Coverage guards (applied here so the raw window sums and helper dates
-- never reach the mart's contract): the sums are emitted only when the window
-- is covered (profile history reaches the window, earliest in-window snapshot
-- and its predecessor sit within a 14-day tolerance of the window boundary, and
-- the latest snapshot gap does not exceed the window).
-- Growth additionally requires its baseline to be an earlier snapshot at the
-- start of the window with non-zero recruiting trial count; segments that first
-- appear in the window or have zero baseline emit null, never manufactured 0.0.
with_concentration as (
    select
        w.* exclude (
            new_recruiting_30d_raw, new_recruiting_90d_raw, previous_snapshot_date,
            window_30d_earliest_date, window_30d_earliest_pred_date,
            window_90d_earliest_date, window_90d_earliest_pred_date,
            segment_90d_first_snapshot_date
        ),
        case
            when w.observed_history_days >= 30
                and w.window_30d_earliest_date <= w.snapshot_date - interval 30 day + interval 14 day
                and (
                    w.window_30d_earliest_pred_date is null
                    or w.window_30d_earliest_pred_date >= w.snapshot_date - interval 30 day - interval 14 day
                )
                and w.previous_snapshot_date >= w.snapshot_date - interval 30 day
            then w.new_recruiting_30d_raw
        end as new_recruiting_30d,
        case
            when w.observed_history_days >= 90
                and w.window_90d_earliest_date <= w.snapshot_date - interval 90 day + interval 14 day
                and (
                    w.window_90d_earliest_pred_date is null
                    or w.window_90d_earliest_pred_date >= w.snapshot_date - interval 90 day - interval 14 day
                )
                and w.previous_snapshot_date >= w.snapshot_date - interval 90 day
            then w.new_recruiting_90d_raw
        end as new_recruiting_90d,
        case
            when w.observed_history_days >= 90
                and w.window_90d_earliest_date <= w.snapshot_date - interval 90 day + interval 14 day
                and (
                    w.window_90d_earliest_pred_date is null
                    or w.window_90d_earliest_pred_date >= w.snapshot_date - interval 90 day - interval 14 day
                )
                and w.previous_snapshot_date >= w.snapshot_date - interval 90 day
                and w.segment_90d_first_snapshot_date = w.window_90d_earliest_date
                and w.recruiting_count_90d_baseline > 0
            then {{ safe_divide(
                'w.recruiting_trial_count - w.recruiting_count_90d_baseline',
                'w.recruiting_count_90d_baseline',
            ) }}
        end as recruiting_growth_90d,
        sc.sponsor_count,
        sc.top_sponsor_share,
        sc.sponsor_hhi
    from windowed w
    left join {{ ref('int_sponsor_concentration') }} sc
        using (
            snapshot_date, indication_profile_id, condition_group,
            state_normalized, phase_normalized
        )
)

select
    *,
    percent_rank() over (
        partition by indication_profile_id, snapshot_date
        order by recruiting_trial_count
    ) as density_percentile,
    case
        when percent_rank() over (
            partition by indication_profile_id, snapshot_date
            order by recruiting_trial_count
        ) < 0.5 then 'low'
        when percent_rank() over (
            partition by indication_profile_id, snapshot_date
            order by recruiting_trial_count
        ) < 0.8 then 'moderate'
        else 'elevated'
    end as competition_signal_band
from with_concentration
