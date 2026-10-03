-- Joining location eligibility must select studies once, never sum fanout rows.
with location_studies as (
    select distinct study_audit_key
    from {{ ref('mart_location_snapshot_audit') }}
    where usable_geography_flag
), joined as (
    select s.indication_profile_id, s.snapshot_id, s.enrollment_category,
        count(*) as study_count, sum(s.enrollment_count) as enrollment_total
    from {{ ref('mart_study_snapshot_audit') }} s
    join location_studies l using (study_audit_key)
    group by 1, 2, 3
), baseline as (
    select indication_profile_id, snapshot_id, enrollment_category,
        count(*) as study_count, sum(enrollment_count) as enrollment_total
    from {{ ref('mart_study_snapshot_audit') }}
    where geography_category = 'usable'
    group by 1, 2, 3
)
select coalesce(j.indication_profile_id, b.indication_profile_id) as indication_profile_id
from joined j full join baseline b using (indication_profile_id, snapshot_id, enrollment_category)
where j.study_count is distinct from b.study_count
   or j.enrollment_total is distinct from b.enrollment_total
