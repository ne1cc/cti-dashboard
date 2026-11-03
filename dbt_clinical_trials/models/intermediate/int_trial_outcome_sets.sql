-- One row per profile per NCT ID per ingestion run: an order-sensitive hash of
-- the trial's primary + secondary outcome measure/description/time_frame.
-- Built from stg_trials so a trial with no outcomes still gets a row (empty set).
with outcomes as (
    select
        ingestion_run_id, indication_profile_id, nct_id,
        count(*) as outcome_count,
        md5(string_agg(
            concat_ws('||', outcome_type, coalesce(outcome_measure, ''),
                coalesce(outcome_description, ''), coalesce(time_frame, '')),
            '##' order by outcome_type, outcome_index
        )) as outcome_set_hash,
        string_agg(
            outcome_type || ': ' || coalesce(outcome_measure, '(unnamed)')
                || ' [' || coalesce(time_frame, 'no time frame') || ']',
            '; ' order by outcome_type, outcome_index
        ) as outcome_summary
    from {{ ref('stg_trial_outcomes') }}
    where lower(outcome_type) in ('primary', 'secondary')
    group by 1, 2, 3
)

select
    t.ingestion_run_id,
    t.indication_profile_id,
    t.nct_id,
    coalesce(o.outcome_count, 0) as outcome_count,
    coalesce(o.outcome_set_hash, md5('')) as outcome_set_hash,
    coalesce(o.outcome_summary, '(none recorded)') as outcome_summary
from {{ ref('stg_trials') }} t
left join outcomes o
    using (ingestion_run_id, indication_profile_id, nct_id)
