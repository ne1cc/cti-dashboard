-- One row per indication profile per normalized condition (from each trial's
-- current record in that profile). Counts are per profile: the ADRD taxonomy and
-- the NSCLC taxonomy can map the same raw condition to different groups, so a
-- shared trial_count would be wrong on both profiles' rows.
with current_runs as (
    select ingestion_run_id, indication_profile_id, nct_id
    from {{ ref('int_current_trial_status') }}
),

conditions as (
    select c.*
    from {{ ref('stg_trial_conditions') }} c
    inner join current_runs r
        on c.ingestion_run_id = r.ingestion_run_id
        and c.indication_profile_id = r.indication_profile_id
        and c.nct_id = r.nct_id
    where c.condition_normalized is not null
)

select
    {{ generate_surrogate_key(['condition_normalized', 'indication_profile_id']) }}
        as condition_key,
    indication_profile_id,
    condition_normalized,
    min(condition_raw) as condition_display_name,
    min(condition_group) as condition_group,
    bool_or(dementia_relevance_flag) as dementia_relevance_flag,
    min(mapping_confidence) as mapping_confidence,
    count(distinct nct_id) as trial_count
from conditions
group by indication_profile_id, condition_normalized
