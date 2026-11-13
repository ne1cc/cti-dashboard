-- Snapshot-aware preset trial bridge with explicit unassigned scope.
-- Grain: preset_id x nct_id x snapshot_date.
-- Unmapped / no-MeSH trials: assigned to 'unassigned' scope; never silently dropped.
-- Preset membership counts each trial once per preset and once in the registry union.

with complete_preset_snapshots as (
    select
        ingestion_run_id,
        indication_profile_id,
        snapshot_date
    from {{ ref('stg_trial_snapshots') }}
    where status = 'success'
      and indication_profile_id != 'full_catalog'
),

profile_condition_trials as (
    select distinct ingestion_run_id, indication_profile_id, nct_id
    from {{ ref('stg_trial_conditions') }}
    where condition_normalized is not null

    union

    select distinct ingestion_run_id, indication_profile_id, nct_id
    from {{ ref('stg_trial_condition_mesh') }}
    where mesh_id is not null
),

preset_memberships as (
    select
        t.indication_profile_id as preset_id,
        t.nct_id,
        t.snapshot_date,
        max(t.ingestion_run_id) as ingestion_run_id,
        max(t.snapshot_timestamp_utc) as snapshot_timestamp_utc,
        false as is_unassigned,
        'preset' as membership_scope
    from {{ ref('stg_trials') }} t
    inner join complete_preset_snapshots s
        on t.ingestion_run_id = s.ingestion_run_id
        and t.indication_profile_id = s.indication_profile_id
    inner join profile_condition_trials c
        on t.ingestion_run_id = c.ingestion_run_id
        and t.indication_profile_id = c.indication_profile_id
        and t.nct_id = c.nct_id
    group by
        t.indication_profile_id,
        t.nct_id,
        t.snapshot_date
),

complete_registry_snapshots as (
    select
        ingestion_run_id,
        started_at_utc,
        snapshot_date
    from {{ ref('stg_registry_trial_snapshots') }}
    where status = 'success'
),

registry_snapshots as (
    select
        t.nct_id,
        t.snapshot_date,
        max(t.ingestion_run_id) as ingestion_run_id,
        max(t.snapshot_timestamp_utc) as snapshot_timestamp_utc
    from {{ ref('stg_registry_trials') }} t
    inner join complete_registry_snapshots s
        on t.ingestion_run_id = s.ingestion_run_id
    group by
        t.nct_id,
        t.snapshot_date
),

unassigned_memberships as (
    select
        'unassigned' as preset_id,
        r.nct_id,
        r.snapshot_date,
        r.ingestion_run_id,
        r.snapshot_timestamp_utc,
        true as is_unassigned,
        'unassigned' as membership_scope
    from registry_snapshots r
    where not exists (
        select 1
        from preset_memberships p
        where p.nct_id = r.nct_id
          and p.snapshot_date = r.snapshot_date
    )
),

all_memberships as (
    select * from preset_memberships
    union all
    select * from unassigned_memberships
),

latest_date_per_preset_trial as (
    select
        preset_id,
        nct_id,
        max(snapshot_date) as max_snapshot_date
    from all_memberships
    group by preset_id, nct_id
)

select
    {{ generate_surrogate_key(['m.preset_id', 'm.nct_id', 'm.snapshot_date']) }} as preset_trial_key,
    m.preset_id,
    m.nct_id,
    {{ generate_surrogate_key(['m.nct_id']) }} as registry_trial_key,
    m.ingestion_run_id,
    m.snapshot_date,
    m.snapshot_timestamp_utc,
    m.is_unassigned,
    m.membership_scope,
    (m.snapshot_date = l.max_snapshot_date) as current_record_flag
from all_memberships m
inner join latest_date_per_preset_trial l
    on m.preset_id = l.preset_id
    and m.nct_id = l.nct_id
