-- Protocol detail query over warehouse staging and dimensional models.
-- ADR 0065 strict compliance: zero investigator/coordinator contact PII.
-- Directs inquiries to canonical registry via registry_contact_url.

SELECT
    st.nct_id,
    st.brief_title,
    st.official_title,
    st.overall_status,
    st.last_known_status,
    st.status_verified_date_raw AS status_verified_date,
    st.study_type,
    COALESCE(st.has_expanded_access, FALSE) AS has_expanded_access,

    -- Study design
    st.phase_raw,
    st.phase_normalized,
    st.allocation,
    st.primary_purpose,

    -- Enrollment
    st.enrollment_count,
    st.enrollment_type,

    -- Dates
    CAST(st.start_date AS VARCHAR) AS start_date,
    CAST(st.primary_completion_date AS VARCHAR) AS primary_completion_date,
    CAST(st.completion_date AS VARCHAR) AS completion_date,
    CAST(st.study_first_post_date AS VARCHAR) AS study_first_post_date,
    CAST(st.results_first_post_date AS VARCHAR) AS results_first_post_date,
    CAST(st.last_update_post_date AS VARCHAR) AS last_update_post_date,

    -- Sponsors
    st.lead_sponsor_name,
    (
        SELECT min(ts.sponsor_class)
        FROM stg_trial_sponsors ts
        WHERE ts.nct_id = st.nct_id AND ts.sponsor_role = 'lead_sponsor'
    ) AS lead_sponsor_class,
    (
        SELECT list({
            'name': ts.sponsor_name,
            'class': ts.sponsor_class
        })
        FROM stg_trial_sponsors ts
        WHERE ts.nct_id = st.nct_id AND ts.sponsor_role != 'lead_sponsor'
    ) AS collaborators,
    st.responsible_party_type,

    -- Conditions
    (
        SELECT list({
            'condition_raw': tc.condition_raw,
            'condition_normalized': tc.condition_normalized,
            'condition_group': tc.condition_group,
            'mesh_id': NULL,
            'dementia_relevance_flag': tc.dementia_relevance_flag
        })
        FROM stg_trial_conditions tc
        WHERE tc.nct_id = st.nct_id
    ) AS conditions,

    -- Interventions
    (
        SELECT list({
            'intervention_type': COALESCE(ti.intervention_type, 'UNKNOWN'),
            'intervention_name': COALESCE(ti.intervention_name, ''),
            'description': ti.intervention_description
        })
        FROM stg_trial_interventions ti
        WHERE ti.nct_id = st.nct_id
    ) AS interventions,

    -- Eligibility (Issue #62)
    st.minimum_age_raw,
    st.maximum_age_raw,
    st.minimum_age_years,
    st.maximum_age_years,
    CASE
        WHEN st.minimum_age_years IS NULL AND st.maximum_age_years IS NULL THEN TRUE
        ELSE FALSE
    END AS age_not_stated,
    COALESCE(st.sex, 'ALL') AS sex,
    st.gender_based,
    st.gender_description,
    st.healthy_volunteers AS accepts_healthy_volunteers,
    COALESCE(st.std_age_groups, []) AS std_age_groups,
    st.eligibility_criteria_text AS criteria_text,

    -- Sites (Strictly omitting investigator names/emails/phones)
    (
        SELECT list({
            'facility_name': COALESCE(loc.facility_name, 'UNKNOWN'),
            'city': COALESCE(loc.city, 'UNKNOWN'),
            'state': COALESCE(loc.state_raw, loc.state_normalized),
            'zip_code': loc.zip_code,
            'country': COALESCE(loc.country, 'UNKNOWN'),
            'location_status': loc.location_status,
            'latitude': loc.latitude,
            'longitude': loc.longitude,
            'distance_miles': CASE
                WHEN $lat IS NOT NULL AND $lon IS NOT NULL AND loc.latitude IS NOT NULL AND loc.longitude IS NOT NULL THEN
                    round(2 * 3958.8 * asin(sqrt(
                        greatest(0.0, least(1.0,
                            power(sin(radians(loc.latitude - $lat) / 2.0), 2) +
                            cos(radians($lat)) * cos(radians(loc.latitude)) *
                            power(sin(radians(loc.longitude - $lon) / 2.0), 2)
                        ))
                    )), 1)
                ELSE NULL
            END
        })
        FROM stg_trial_locations loc
        WHERE loc.nct_id = st.nct_id
    ) AS sites,

    -- Provenance
    st.ingestion_run_id,
    st.snapshot_id,
    CAST(COALESCE(st.snapshot_date, current_date) AS VARCHAR) AS snapshot_date,
    CAST(COALESCE(st.snapshot_timestamp_utc, now()) AS VARCHAR) AS snapshot_timestamp_utc,
    COALESCE(st.source_json_hash, '') AS source_json_hash,
    COALESCE(st.record_quality_flag, 'ok') AS record_quality_flag,
    CAST(st.retrieved_at_utc AS VARCHAR) AS retrieved_at_utc,
    COALESCE(st.indication_profile_id, 'warehouse') AS indication_profile_id

FROM stg_trials st
WHERE upper(st.nct_id) = upper($nct_id)
QUALIFY row_number() OVER (PARTITION BY st.nct_id ORDER BY st.snapshot_timestamp_utc DESC) = 1;
