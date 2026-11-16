-- Joining registry location audit rows must select each canonical study
-- once per snapshot, never summing fanout rows across locations.
with location_studies as (
    select distinct study_audit_key
    from {{ ref('mart_registry_location_snapshot_audit') }}
    where usable_geography_flag
), joined as (
    select
        s.snapshot_id,
        s.enrollment_category,
        count(*) as study_count,
        sum(s.enrollment_count) as enrollment_total
    from {{ ref('mart_registry_study_snapshot_audit') }} s
    inner join location_studies l
        using (study_audit_key)
    group by 1, 2
), baseline as (
    select
        snapshot_id,
        enrollment_category,
        count(*) as study_count,
        sum(enrollment_count) as enrollment_total
    from {{ ref('mart_registry_study_snapshot_audit') }}
    where geography_category = 'usable'
    group by 1, 2
)
select coalesce(j.snapshot_id, b.snapshot_id) as snapshot_id
from joined j
full join baseline b
    using (snapshot_id, enrollment_category)
where j.study_count is distinct from b.study_count
   or j.enrollment_total is distinct from b.enrollment_total
