-- One row per ingestion run. Snapshot metrics downstream must only use
-- status = 'success' runs (complete snapshots).
select
    ingestion_run_id,
    query_hash,
    condition,
    mode,
    status,
    cast(try_cast(started_at_utc as timestamptz) as timestamp) as started_at_utc,
    cast(try_cast(ended_at_utc as timestamptz) as timestamp) as ended_at_utc,
    cast(try_cast(started_at_utc as timestamptz) as date) as snapshot_date,
    cast(page_count as integer) as page_count,
    cast(record_count as integer) as record_count,
    try_cast(total_count_reported as integer) as total_count_reported,
    cast(quarantined_record_count as integer) as quarantined_record_count,
    profile,
    -- The manifest field is `profile`; every other model in this project calls
    -- the same concept indication_profile_id. Alias at the boundary so the
    -- rename happens once.
    profile as indication_profile_id,
    -- All-null summaries (fixture and first successes) read back as INTEGER;
    -- normalize to VARCHAR so the mart contract matches the manifest schema.
    cast(error as varchar) as error
from {{ source('bronze', 'ingestion_manifests') }}
