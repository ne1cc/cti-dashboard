-- Exactly one current record per (profile, trial) in fct_trial_snapshot.
--
-- The (indication_profile_id, nct_id) pairs are enumerated from the whole fact
-- and left joined to the current rows, so a profile left with ZERO current
-- records is a failure here. The form this replaces -- `having count(*) > 1`
-- over a `where current_record_flag` filter -- could only ever catch
-- duplicates: under a global max(snapshot_date) the stale profile's rows are
-- filtered out before the group by, no group forms for them, and the predicate
-- cannot fire at all. That is the collapse this migration had to make
-- catchable.
--
-- Catchable is not yet caught. Exercising the zero branch also needs a fixture
-- whose two profiles land on different snapshot dates (Amendment A15 item 2);
-- while both fixture profiles share one date, every pair has exactly one
-- current record and this assertion passes on the zero branch by absence of
-- counterexample, not by measurement.
with pairs as (
    select distinct indication_profile_id, nct_id
    from {{ ref('fct_trial_snapshot') }}
),

current_counts as (
    select indication_profile_id, nct_id, count(*) as current_record_count
    from {{ ref('fct_trial_snapshot') }}
    where current_record_flag
    group by 1, 2
)

select
    p.indication_profile_id,
    p.nct_id,
    coalesce(c.current_record_count, 0) as current_record_count
from pairs p
left join current_counts c
    using (indication_profile_id, nct_id)
where coalesce(c.current_record_count, 0) != 1
