-- Complete (success) snapshots should reconcile: silver trial rows equal the
-- manifest record count, with unique NCT IDs. Warn severity: a mismatch means
-- "investigate", not "block the pipeline".
--
-- ingest_only profiles (full_catalog) are excluded: they are bronze-only by
-- design, so their trial_row_count is legitimately null and their
-- manifest_reconciled_flag would fail forever. The var is asserted to match the
-- Python registry by tests/test_profiles.py::test_dbt_ingest_only_var_matches_registry.
--
-- The members are quoted by a loop, not by `| map('repr')`: dbt's Jinja
-- environment registers no `repr` filter, and the plan's form fails to compile
-- with "No filter named 'repr'". The list still comes from the var alone.
{{ config(severity='warn') }}

select
    ingestion_run_id,
    indication_profile_id,
    manifest_record_count,
    trial_row_count,
    distinct_trial_count
from {{ ref('mart_data_reliability') }}
where status = 'success'
  and indication_profile_id not in (
    {%- for profile_id in var('ingest_only_profiles') %}
    '{{ profile_id }}'{{ "," if not loop.last }}
    {%- endfor %}
  )
  and (
      not coalesce(manifest_reconciled_flag, false)
      or not coalesce(unique_nct_flag, false)
  )
