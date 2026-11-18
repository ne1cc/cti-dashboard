-- Protocol detail query over canonical registry-grain warehouse models.
-- ADR 0065 strict compliance: zero investigator/coordinator contact PII.
-- Directs inquiries to canonical registry via registry_contact_url.

-- Issue #63: one row per site with effective coordinates. Registry geoPoint
-- preferred; ZIP (ZCTA) then city Gazetteer centroids as flagged-approximate
-- fallback. The place join can fan out (repeated city names within a state),
-- so one deterministic row wins per site — the same dedup and tie-break as
-- search.sql, so both endpoints report identical coordinates for a site.
WITH site_fallback AS (
    SELECT
        COALESCE(loc.snapshot_id, loc.ingestion_run_id) AS effective_run_key,
        loc.nct_id,
        loc.location_ordinal,
        loc.facility_name,
        loc.city,
        COALESCE(loc.state_raw, loc.state_normalized) AS state,
        loc.zip_code,
        loc.country,
        loc.location_status,
        CASE
            WHEN loc.latitude IS NOT NULL AND loc.longitude IS NOT NULL THEN loc.latitude
            WHEN gz.zcta IS NOT NULL THEN gz.latitude
            ELSE gp.latitude
        END AS site_latitude,
        CASE
            WHEN loc.latitude IS NOT NULL AND loc.longitude IS NOT NULL THEN loc.longitude
            WHEN gz.zcta IS NOT NULL THEN gz.longitude
            ELSE gp.longitude
        END AS site_longitude,
        CASE
            WHEN loc.latitude IS NOT NULL AND loc.longitude IS NOT NULL THEN FALSE
            WHEN gz.zcta IS NOT NULL OR gp.state_usps IS NOT NULL THEN TRUE
            ELSE NULL
        END AS coordinate_approximate
    FROM stg_registry_trial_locations loc
    LEFT JOIN gazetteer_zcta gz ON left(loc.zip_code, 5) = gz.zcta
    LEFT JOIN gazetteer_place gp
        ON gp.state_usps = loc.state_normalized
       AND (gp.city_name = loc.city_normalized OR gp.city_name_stripped = loc.city_normalized)
    QUALIFY row_number() OVER (
        PARTITION BY COALESCE(loc.snapshot_id, loc.ingestion_run_id), loc.nct_id, loc.location_ordinal
        ORDER BY CASE WHEN gz.zcta IS NOT NULL THEN 0 ELSE 1 END, gp.latitude, gp.longitude
    ) = 1
)

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
    st.intervention_model,
    st.masking,
    st.has_placebo_arm,
    st.why_stopped,
    st.brief_summary,
    st.primary_outcome_time_frame,

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
        FROM stg_registry_trial_sponsors ts
        WHERE ts.nct_id = st.nct_id
          AND COALESCE(ts.snapshot_id, ts.ingestion_run_id) = COALESCE(st.snapshot_id, st.ingestion_run_id)
          AND ts.sponsor_role = 'lead_sponsor'
    ) AS lead_sponsor_class,
    (
        SELECT list({
            'name': ts.sponsor_name,
            'class': ts.sponsor_class
        } ORDER BY ts.sponsor_name)
        FROM stg_registry_trial_sponsors ts
        WHERE ts.nct_id = st.nct_id
          AND COALESCE(ts.snapshot_id, ts.ingestion_run_id) = COALESCE(st.snapshot_id, st.ingestion_run_id)
          AND ts.sponsor_role != 'lead_sponsor'
    ) AS collaborators,
    st.responsible_party_type,

    -- Conditions
    (
        SELECT list({
            'condition_raw': tc.condition_raw,
            'condition_normalized': tc.condition_normalized,
            'condition_group': COALESCE(
                tc.condition_group,
                (
                    SELECT min(pcm.condition_group)
                    FROM int_registry_preset_condition_mapping pcm
                    WHERE pcm.nct_id = st.nct_id
                      AND pcm.snapshot_id = COALESCE(st.snapshot_id, st.ingestion_run_id)
                ),
                (
                    SELECT min(m.mesh_term)
                    FROM stg_registry_trial_condition_mesh m
                    WHERE m.nct_id = st.nct_id
                      AND COALESCE(m.snapshot_id, m.ingestion_run_id) = COALESCE(st.snapshot_id, st.ingestion_run_id)
                      AND NOT m.is_ancestor
                      AND lower(m.mesh_term) = tc.condition_normalized
                )
            ),
            'mesh_id': (
                SELECT min(m.mesh_id)
                FROM stg_registry_trial_condition_mesh m
                WHERE m.nct_id = st.nct_id
                  AND COALESCE(m.snapshot_id, m.ingestion_run_id) = COALESCE(st.snapshot_id, st.ingestion_run_id)
                  AND NOT m.is_ancestor
                  AND lower(m.mesh_term) = tc.condition_normalized
            ),
            'dementia_relevance_flag': COALESCE(
                tc.dementia_relevance_flag,
                (
                    SELECT bool_or(pcm.dementia_relevance_flag)
                    FROM int_registry_preset_condition_mapping pcm
                    WHERE pcm.nct_id = st.nct_id
                      AND pcm.snapshot_id = COALESCE(st.snapshot_id, st.ingestion_run_id)
                )
            )
        } ORDER BY tc.condition_raw)
        FROM stg_registry_trial_conditions tc
        WHERE tc.nct_id = st.nct_id
          AND COALESCE(tc.snapshot_id, tc.ingestion_run_id) = COALESCE(st.snapshot_id, st.ingestion_run_id)
    ) AS conditions,

    -- Interventions
    (
        SELECT list({
            'intervention_type': COALESCE(ti.intervention_type, 'UNKNOWN'),
            'intervention_name': COALESCE(ti.intervention_name, ''),
            'description': ti.intervention_description
        } ORDER BY ti.intervention_name, ti.intervention_type)
        FROM stg_registry_trial_interventions ti
        WHERE ti.nct_id = st.nct_id
          AND COALESCE(ti.snapshot_id, ti.ingestion_run_id) = COALESCE(st.snapshot_id, st.ingestion_run_id)
    ) AS interventions,

    -- Arm groups (registry text stored verbatim)
    (
        SELECT list({
            'label': ta.arm_label,
            'arm_type': ta.arm_type,
            'description': ta.arm_description,
            'intervention_names': COALESCE(ta.intervention_names, [])
        } ORDER BY ta.arm_ordinal)
        FROM stg_registry_trial_arms ta
        WHERE ta.nct_id = st.nct_id
          AND COALESCE(ta.snapshot_id, ta.ingestion_run_id) = COALESCE(st.snapshot_id, st.ingestion_run_id)
    ) AS arms,

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
            'facility_name': COALESCE(s.facility_name, 'UNKNOWN'),
            'city': COALESCE(s.city, 'UNKNOWN'),
            'state': s.state,
            'zip_code': s.zip_code,
            'country': COALESCE(s.country, 'UNKNOWN'),
            'location_status': s.location_status,
            'latitude': s.site_latitude,
            'longitude': s.site_longitude,
            'coordinate_approximate': s.coordinate_approximate,
            'distance_miles': CASE
                WHEN $lat IS NOT NULL AND $lon IS NOT NULL AND s.site_latitude IS NOT NULL AND s.site_longitude IS NOT NULL THEN
                    round(2 * 3958.8 * asin(sqrt(
                        greatest(0.0, least(1.0,
                            power(sin(radians(s.site_latitude - $lat) / 2.0), 2) +
                            cos(radians($lat)) * cos(radians(s.site_latitude)) *
                            power(sin(radians(s.site_longitude - $lon) / 2.0), 2)
                        ))
                    )), 1)
                ELSE NULL
            END
        } ORDER BY s.location_ordinal, s.facility_name)
        FROM site_fallback s
        WHERE s.nct_id = st.nct_id
          AND s.effective_run_key = COALESCE(st.snapshot_id, st.ingestion_run_id)
    ) AS sites,

    -- Provenance (public compatibility mapping for indication_profile_id via preset membership)
    COALESCE(st.ingestion_run_id, st.snapshot_id) AS ingestion_run_id,
    COALESCE(st.snapshot_id, st.ingestion_run_id) AS snapshot_id,
    CAST(COALESCE(sm.snapshot_date, st.snapshot_date, current_date) AS VARCHAR) AS snapshot_date,
    strftime(
        timezone('UTC', COALESCE(sm.started_at_utc, st.snapshot_timestamp_utc, now())),
        '%Y-%m-%dT%H:%M:%SZ'
    ) AS snapshot_timestamp_utc,
    COALESCE(st.source_json_hash, '') AS source_json_hash,
    COALESCE(st.record_quality_flag, 'ok') AS record_quality_flag,
    strftime(timezone('UTC', COALESCE(st.retrieved_at_utc, now())), '%Y-%m-%dT%H:%M:%SZ') AS retrieved_at_utc,
    COALESCE(
        (
            SELECT min(b.preset_id)
            FROM bridge_preset_trial b
            WHERE b.nct_id = st.nct_id
              AND b.current_record_flag = TRUE
              AND NOT b.is_unassigned
        ),
        (
            SELECT min(b.preset_id)
            FROM bridge_preset_trial b
            WHERE b.nct_id = st.nct_id
              AND b.current_record_flag = TRUE
        ),
        'warehouse'
    ) AS indication_profile_id

FROM stg_registry_trials st
LEFT JOIN stg_registry_trial_snapshots sm
    ON COALESCE(st.snapshot_id, st.ingestion_run_id) = sm.ingestion_run_id
WHERE upper(st.nct_id) = upper($nct_id)
  AND (
      NOT EXISTS (SELECT 1 FROM stg_registry_trial_snapshots)
      OR (
          sm.ingestion_run_id IS NOT NULL
          AND sm.status = 'success'
          AND sm.profile IN ('full_catalog', 'full-catalog', 'default')
          AND (sm.condition IS NULL OR trim(sm.condition) = '')
      )
  )
QUALIFY row_number() OVER (
    PARTITION BY st.nct_id
    ORDER BY
        COALESCE(sm.started_at_utc, st.snapshot_timestamp_utc) DESC,
        st.snapshot_timestamp_utc DESC,
        COALESCE(st.snapshot_id, st.ingestion_run_id) DESC
) = 1;
