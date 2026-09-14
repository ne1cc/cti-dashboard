-- Lead sponsors with the most currently recruiting listings, by class.
-- Listing counts, not market share.
select
    d.indication_profile_id,
    d.current_lead_sponsor as lead_sponsor,
    any_value(s.sponsor_class) as sponsor_class,
    count(distinct d.nct_id) as recruiting_trial_count,
    string_agg(distinct d.current_phase, ' | ' order by d.current_phase)
        as phase_mix
from {{ ref('dim_trial') }} d
left join {{ ref('bridge_trial_sponsor') }} s
    on d.trial_key = s.trial_key and s.lead_sponsor_flag
where d.current_overall_status = 'RECRUITING'
group by 1, 2
order by recruiting_trial_count desc, lead_sponsor
limit 25
