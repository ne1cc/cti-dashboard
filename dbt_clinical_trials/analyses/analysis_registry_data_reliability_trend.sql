-- Reliability of every authoritative whole-registry ingestion run broken out by
-- preset membership scope (including 'unassigned'), with source-wide completeness
-- and reconciliation totals kept beside each preset's membership denominators.
select
    ingestion_run_id,
    preset_id,
    membership_scope,
    source_profile,
    snapshot_date,
    status,
    manifest_record_count,
    source_trial_row_count,
    source_fetched_trial_row_count,
    manifest_reconciled_flag,
    unique_nct_flag,
    trial_row_count,
    distinct_trial_count,
    quarantined_record_count,
    flagged_record_share,
    usable_location_share,
    source_usable_location_share,
    low_confidence_condition_share
from {{ ref('mart_registry_data_reliability') }}
-- Filter to one preset_id or membership_scope when trending a single slice, e.g.:
-- where preset_id = 'adrd'
order by snapshot_date desc, ingestion_run_id desc, preset_id asc
