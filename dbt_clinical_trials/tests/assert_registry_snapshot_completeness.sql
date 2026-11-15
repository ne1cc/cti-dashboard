-- Complete (success) whole-registry snapshots should reconcile at source grain:
-- freshly fetched silver registry trial rows equal the manifest record count,
-- with unique NCT IDs across the containing snapshot. Warn severity matches the
-- legacy assert_snapshot_completeness check.
{{ config(severity='warn') }}

select distinct
    ingestion_run_id,
    source_profile,
    manifest_record_count,
    source_trial_row_count,
    source_fetched_trial_row_count,
    source_distinct_trial_count
from {{ ref('mart_registry_data_reliability') }}
where status = 'success'
  and (
      not coalesce(manifest_reconciled_flag, false)
      or not coalesce(unique_nct_flag, false)
  )
