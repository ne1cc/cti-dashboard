-- Enforce Policy Item 3:
-- Equal source timestamps with conflicting payloads must fail with NCT ID, run IDs and hashes.
-- Identical payloads at equal timestamps coalesce without failure.
with trial_snapshots as (
    select
        t.nct_id,
        s.started_at_utc,
        t.ingestion_run_id,
        t.source_json_hash
    from {{ ref('stg_registry_trials') }} t
    inner join {{ ref('stg_registry_trial_snapshots') }} s
        on t.ingestion_run_id = s.ingestion_run_id
    where s.status = 'success'
),

conflicts as (
    select
        nct_id,
        started_at_utc,
        count(distinct source_json_hash) as distinct_hash_count,
        string_agg(distinct ingestion_run_id, ', ') as run_ids,
        string_agg(distinct source_json_hash, ', ') as hashes
    from trial_snapshots
    group by nct_id, started_at_utc
    having count(distinct source_json_hash) > 1
)

select
    nct_id,
    started_at_utc,
    distinct_hash_count,
    run_ids,
    hashes
from conflicts
