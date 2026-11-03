-- Issue #79: sponsor_hhi must be null for every segment with fewer distinct
-- sponsors than min_sponsors_for_hhi (covers 0, 1, 2 ... threshold - 1); a
-- non-null HHI below the threshold would let a single-sponsor segment score
-- the maximum concentration by definition.
select *
from {{ ref('int_sponsor_concentration') }}
where sponsor_hhi is not null
  and sponsor_count < {{ var('min_sponsors_for_hhi', 3) }}
