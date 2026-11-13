-- Snapshot-aware preset trial bridge with explicit unassigned scope.
-- Grain: preset_id x nct_id x snapshot_date.
-- Unmapped / no-MeSH trials: assigned to 'unassigned' scope; never silently dropped.
-- Preset membership counts each trial once per preset and once in the registry union.

with successful_snapshots as (
    select
        ingestion_run_id,
        started_at_utc,
        snapshot_date
    from {{ ref('stg_registry_trial_snapshots') }}
    where status = 'success'
),

trials_with_snapshots as (
    select
        t.nct_id,
        t.ingestion_run_id,
        t.snapshot_date,
        t.snapshot_timestamp_utc,
        s.started_at_utc,
        t.source_json_hash
    from {{ ref('stg_registry_trials') }} t
    inner join successful_snapshots s
        on t.ingestion_run_id = s.ingestion_run_id
),

snapshot_timeline as (
    select
        nct_id,
        snapshot_date,
        max(started_at_utc) as max_started_at_utc
    from trials_with_snapshots
    group by nct_id, snapshot_date
),

distinct_trial_snapshots as (
    select
        t.nct_id,
        min(t.ingestion_run_id) as ingestion_run_id,
        t.snapshot_date,
        t.snapshot_timestamp_utc,
        t.started_at_utc,
        t.source_json_hash
    from trials_with_snapshots t
    inner join snapshot_timeline st
        on t.nct_id = st.nct_id
        and t.snapshot_date = st.snapshot_date
        and t.started_at_utc = st.max_started_at_utc
    group by
        t.nct_id,
        t.snapshot_date,
        t.snapshot_timestamp_utc,
        t.started_at_utc,
        t.source_json_hash
),

raw_files as (
    select filename, content
    from read_text('config/profiles/*.yml')
),

extracted as (
    select
        regexp_extract(content, 'profile:[^\n]*\n(?:[ \t]+.*\n)*?[ \t]+id:[ \t]*([a-zA-Z0-9_]+)', 1) as preset_id,
        regexp_extract(content, 'mesh_terms:[^\n]*\n((?:[ \t]+-[^\n]*\n)+)', 1) as terms_block
    from raw_files
),

lines as (
    select
        preset_id,
        unnest(string_split(terms_block, chr(10))) as line
    from extracted
    where terms_block is not null and terms_block != ''
),

preset_mesh_mapping as (
    select
        preset_id,
        trim(both '"'' ' from regexp_extract(regexp_replace(line, '#.*', ''), '-[ \t]*(.*)', 1)) as mesh_term
    from lines
    where regexp_extract(regexp_replace(line, '#.*', ''), '-[ \t]*(.*)', 1) != ''
),

trial_mesh as (
    select distinct
        m.nct_id,
        m.ingestion_run_id,
        m.mesh_id,
        m.mesh_term
    from {{ ref('stg_registry_trial_condition_mesh') }} m
),

preset_matches as (
    select distinct
        p.preset_id,
        ts.nct_id,
        ts.snapshot_date,
        ts.ingestion_run_id,
        ts.snapshot_timestamp_utc,
        false as is_unassigned,
        'preset' as membership_scope
    from distinct_trial_snapshots ts
    inner join trial_mesh m
        on ts.nct_id = m.nct_id
        and ts.ingestion_run_id = m.ingestion_run_id
    inner join preset_mesh_mapping p
        on lower(trim(m.mesh_term)) = lower(trim(p.mesh_term))
),

unassigned_memberships as (
    select
        'unassigned' as preset_id,
        ts.nct_id,
        ts.snapshot_date,
        ts.ingestion_run_id,
        ts.snapshot_timestamp_utc,
        true as is_unassigned,
        'unassigned' as membership_scope
    from distinct_trial_snapshots ts
    where not exists (
        select 1
        from preset_matches pm
        where pm.nct_id = ts.nct_id
          and pm.snapshot_date = ts.snapshot_date
    )
),

all_memberships as (
    select * from preset_matches
    union all
    select * from unassigned_memberships
),

latest_date_per_trial as (
    select
        nct_id,
        max(snapshot_date) as max_snapshot_date
    from distinct_trial_snapshots
    group by nct_id
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
inner join latest_date_per_trial l
    on m.nct_id = l.nct_id
