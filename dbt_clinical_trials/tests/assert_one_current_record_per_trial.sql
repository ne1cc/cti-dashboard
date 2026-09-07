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
-- The asymmetric state it exists to catch is in the corpus. `divergent_fixture_root`
-- (`tests/conftest.py`) builds ADRD twice a week apart with NSCLC's only run a
-- week behind ADRD's second, and
-- `tests/test_dbt_fixture_build.py::test_fct_trial_snapshot_currency_is_per_profile`
-- measures that shape (2026-09-06): NSCLC is current at its own 2026-09-01 while
-- the warehouse max is 2026-09-08. The fixture harness ends on a `dbt build`, so
-- this assertion runs there. The red direction was checked directly on 2026-09-07
-- against that same run/date shape with the flag computed globally: this SQL
-- returns 10 rows -- every `oncology_nsclc` pair, zero current -- and the
-- `having count(*) > 1` form this file replaced returns none.
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
