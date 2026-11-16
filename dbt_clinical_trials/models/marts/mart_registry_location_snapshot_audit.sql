-- Grain: immutable successful whole-registry snapshot (snapshot_id) x NCT ID x location_ordinal.
-- Recorded rows, including duplicate facility text; never inferred unique sites.
select
    {{ generate_surrogate_key(['l.snapshot_id', 'l.nct_id', 'l.location_ordinal']) }}
        as location_audit_key,
    s.registry_trial_key,
    s.study_audit_key,
    l.snapshot_id,
    l.ingestion_run_id,
    l.nct_id,
    l.location_ordinal,
    l.retrieved_at_utc,
    l.raw_page_reference,
    l.raw_study_ordinal,
    l.source_json_hash,
    l.facility_name,
    l.facility_normalized,
    l.city,
    l.city_normalized,
    l.state_raw,
    l.state_normalized,
    l.zip_code,
    l.country,
    l.geo_scope,
    l.latitude,
    l.longitude,
    l.location_status,
    l.us_location_flag,
    l.usable_geography_flag,
    l.facility_name is null or trim(l.facility_name) = '' as missing_facility_flag,
    case
        when l.usable_geography_flag then 'usable'
        when l.country is null or trim(l.country) = ''
            or (l.us_location_flag and (l.state_raw is null or trim(l.state_raw) = ''))
            then 'missing'
        else 'unsupported'
    end as geography_category
from {{ ref('stg_registry_trial_locations') }} l
inner join {{ ref('mart_registry_study_snapshot_audit') }} s
    on l.snapshot_id = s.snapshot_id
    and l.nct_id = s.nct_id
