-- dim_geography's grain is (indication_profile_id, state_code), so Task 11
-- removed `unique` from state_code in _marts.yml — TX legitimately appears once
-- per profile. Removing a declared test without replacing it would leave the
-- composite grain asserted nowhere, which is the gap this singular test closes:
-- two rows sharing a (profile, state) mean a duplicate slipped past the group
-- by, and every count on those rows is then double-weighted.
select
    indication_profile_id,
    state_code,
    count(*) as row_count,
    sum(trial_count) as trial_count_sum
from {{ ref('dim_geography') }}
group by indication_profile_id, state_code
having count(*) > 1
