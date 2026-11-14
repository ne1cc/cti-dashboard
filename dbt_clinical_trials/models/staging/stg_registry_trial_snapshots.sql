-- One row per ingestion run for canonical registry-wide snapshots.
-- Retains source manifest identity without filtering on profile or status;
-- filtering to successful, complete snapshots (policy item 2) lands in phase 3.
select
    ingestion_run_id,
    query_hash,
    condition,
    mode,
    status,
    timezone('UTC', try_cast(started_at_utc as timestamptz)) as started_at_utc,
    timezone('UTC', try_cast(ended_at_utc as timestamptz)) as ended_at_utc,
    cast(timezone('UTC', try_cast(started_at_utc as timestamptz)) as date) as snapshot_date,
    cast(page_count as integer) as page_count,
    cast(record_count as integer) as record_count,
    try_cast(total_count_reported as integer) as total_count_reported,
    cast(quarantined_record_count as integer) as quarantined_record_count,
    profile,
    cast(error as varchar) as error
from {{ source('bronze', 'ingestion_manifests') }}
