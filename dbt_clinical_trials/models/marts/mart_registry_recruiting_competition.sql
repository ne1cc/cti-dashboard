-- Potential competition signal per recruiting segment, per preset.
-- Grain: preset_id x snapshot_date x condition_group x state x phase.
with activity as (
    select * from {{ ref('int_registry_condition_geography_activity') }}
    where overall_status = 'RECRUITING'
),

snapshot_calendar as (
    select distinct b.preset_id, f.snapshot_date
    from {{ ref('fct_registry_trial_snapshot') }} f
    inner join {{ ref('bridge_preset_trial') }} b
        on f.nct_id = b.nct_id
        and f.snapshot_date = b.snapshot_date
        and f.snapshot_id = b.snapshot_id
    where not b.is_unassigned
),

preset_history as (
    select
        preset_id,
        snapshot_date,
        datediff('day', min(snapshot_date) over preset_window, snapshot_date)
            as observed_history_days,
        lag(snapshot_date) over preset_window as previous_snapshot_date
    from snapshot_calendar
    window preset_window as (
        partition by preset_id order by snapshot_date
    )
),

preset_window_coverage as (
    select
        preset_id,
        snapshot_date,
        observed_history_days,
        previous_snapshot_date,
        first_value(snapshot_date) over preset_30d as window_30d_earliest_date,
        first_value(previous_snapshot_date) over preset_30d as window_30d_earliest_pred_date,
        first_value(snapshot_date) over preset_90d as window_90d_earliest_date,
        first_value(previous_snapshot_date) over preset_90d as window_90d_earliest_pred_date
    from preset_history
    window
        preset_30d as (
            partition by preset_id order by snapshot_date
            range between interval 30 day preceding and current row
        ),
        preset_90d as (
            partition by preset_id order by snapshot_date
            range between interval 90 day preceding and current row
        )
),

segments as (
    select
        preset_id,
        snapshot_date,
        condition_group,
        state_normalized,
        phase_normalized,
        count(distinct nct_id) as recruiting_trial_count,
        sum(listed_site_count_in_state) as listed_site_count,
        count(distinct nct_id) filter (entered_recruiting_flag)
            as entered_recruiting_count,
        min(previous_trial_snapshot_date) filter (entered_recruiting_flag)
            as segment_earliest_entrant_pred_date,
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
        min(segment_earliest_entrant_pred_date) over segment_30d
            as window_30d_earliest_entrant_pred_date,
        min(segment_earliest_entrant_pred_date) over segment_90d
            as window_90d_earliest_entrant_pred_date,
        first_value(recruiting_trial_count) over segment_90d
            as recruiting_count_90d_baseline,
        first_value(s.snapshot_date) over segment_90d
            as segment_90d_first_snapshot_date,
        first_value(s.entered_recruiting_count) over segment_90d
            as segment_90d_first_entered_recruiting_count
    from segments s
    inner join preset_window_coverage h
        on h.preset_id = s.preset_id
        and h.snapshot_date = s.snapshot_date
    window
        segment_30d as (
            partition by
                s.preset_id, s.condition_group,
                s.state_normalized, s.phase_normalized
            order by s.snapshot_date
            range between interval 30 days preceding and current row
        ),
        segment_90d as (
            partition by
                s.preset_id, s.condition_group,
                s.state_normalized, s.phase_normalized
            order by s.snapshot_date
            range between interval 90 days preceding and current row
        )
),

with_concentration as (
    select
        w.* exclude (
            new_recruiting_30d_raw, new_recruiting_90d_raw, previous_snapshot_date,
            segment_earliest_entrant_pred_date,
            window_30d_earliest_entrant_pred_date, window_90d_earliest_entrant_pred_date,
            window_30d_earliest_date, window_30d_earliest_pred_date,
            window_90d_earliest_date, window_90d_earliest_pred_date,
            segment_90d_first_snapshot_date, segment_90d_first_entered_recruiting_count
        ),
        case
            when w.observed_history_days >= 30
                and w.window_30d_earliest_date <= w.snapshot_date - interval 30 day + interval 7 day
                and w.previous_snapshot_date >= w.snapshot_date - interval 30 day
                and (
                    w.window_30d_earliest_entrant_pred_date is null
                    or w.window_30d_earliest_entrant_pred_date >= w.snapshot_date - interval 30 day
                )
            then w.new_recruiting_30d_raw
        end as new_recruiting_30d,
        case
            when w.observed_history_days >= 90
                and w.window_90d_earliest_date <= w.snapshot_date - interval 90 day + interval 7 day
                and w.previous_snapshot_date >= w.snapshot_date - interval 90 day
                and (
                    w.window_90d_earliest_entrant_pred_date is null
                    or w.window_90d_earliest_entrant_pred_date >= w.snapshot_date - interval 90 day
                )
            then w.new_recruiting_90d_raw
        end as new_recruiting_90d,
        case
            when w.observed_history_days >= 90
                and w.window_90d_earliest_date <= w.snapshot_date - interval 90 day + interval 7 day
                and w.previous_snapshot_date >= w.snapshot_date - interval 90 day
                and (
                    w.window_90d_earliest_entrant_pred_date is null
                    or w.window_90d_earliest_entrant_pred_date >= w.snapshot_date - interval 90 day
                )
                and w.segment_90d_first_snapshot_date = w.window_90d_earliest_date
                and (
                    w.new_recruiting_90d_raw = 0
                    or w.segment_90d_first_entered_recruiting_count = 0
                )
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
    left join {{ ref('int_registry_sponsor_concentration') }} sc
        using (
            snapshot_date, preset_id, condition_group,
            state_normalized, phase_normalized
        )
)

select
    *,
    percent_rank() over (
        partition by preset_id, snapshot_date
        order by recruiting_trial_count
    ) as density_percentile,
    case
        when percent_rank() over (
            partition by preset_id, snapshot_date
            order by recruiting_trial_count
        ) < 0.5 then 'low'
        when percent_rank() over (
            partition by preset_id, snapshot_date
            order by recruiting_trial_count
        ) < 0.8 then 'moderate'
        else 'elevated'
    end as competition_signal_band,
    {{ generate_surrogate_key(["'group:' || condition_group"]) }} as registry_condition_key
from with_concentration
