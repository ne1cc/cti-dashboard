-- One row per NCT ID + arm group + run. Arm labels, types and intervention
-- name lists are stored verbatim from the registry (including
-- PLACEBO_COMPARATOR types); arm_ordinal preserves the registry order.
select
    ingestion_run_id,
    indication_profile_id,
    nct_id,
    cast(arm_ordinal as bigint) as arm_ordinal,
    arm_label,
    arm_type,
    arm_description,
    intervention_names,
    source_json_hash
from {{ source('silver', 'silver_trial_arms') }}
where nct_id is not null
