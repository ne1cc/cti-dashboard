-- On-demand pairwise trial comparability query with MeSH-ancestor + phase blocking
-- at canonical registry grain. Evaluates objective study design comparability
-- factors against target trial $nct_id, keeping preset condition groups keyed
-- by (preset_id, condition_group) and bounded by candidate blocking (Issue #55).

WITH weights AS (
    SELECT
        MAX(CASE WHEN component = 'same_condition' THEN weight END) AS w_same_condition,
        MAX(CASE WHEN component = 'same_phase' THEN weight END) AS w_same_phase,
        MAX(CASE WHEN component = 'geography_overlap' THEN weight END) AS w_geography_overlap,
        MAX(CASE WHEN component = 'intervention_type_overlap' THEN weight END) AS w_intervention_type_overlap,
        MAX(CASE WHEN component = 'study_design_match' THEN weight END) AS w_study_design_match,
        MAX(CASE WHEN component = 'eligibility_compatible' THEN weight END) AS w_eligibility_compatible,
        MAX(CASE WHEN component = 'enrollment_band_match' THEN weight END) AS w_enrollment_band_match
    FROM similarity_score_weights
),
target AS (
    SELECT
        st.nct_id,
        COALESCE(st.snapshot_id, st.ingestion_run_id) AS effective_run_key,
        st.phase_normalized,
        st.study_type,
        st.allocation,
        st.primary_purpose,
        st.enrollment_count,
        CASE
            WHEN st.enrollment_count IS NULL THEN NULL
            WHEN st.enrollment_count < 50 THEN 'small'
            WHEN st.enrollment_count <= 200 THEN 'medium'
            ELSE 'large'
        END AS enrollment_band,
        st.healthy_volunteers,
        COALESCE(st.sex, 'ALL') AS sex,
        st.minimum_age_years,
        st.maximum_age_years
    FROM stg_registry_trials st
    LEFT JOIN stg_registry_trial_snapshots sm
        ON COALESCE(st.snapshot_id, st.ingestion_run_id) = sm.ingestion_run_id
    WHERE UPPER(st.nct_id) = UPPER($nct_id)
      AND (
          NOT EXISTS (SELECT 1 FROM stg_registry_trial_snapshots)
          OR (
              sm.ingestion_run_id IS NOT NULL
              AND sm.status = 'success'
              AND sm.profile IN ('full_catalog', 'full-catalog', 'default')
              AND (sm.condition IS NULL OR trim(sm.condition) = '')
          )
      )
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY st.nct_id
        ORDER BY
            COALESCE(sm.started_at_utc, st.snapshot_timestamp_utc) DESC,
            st.snapshot_timestamp_utc DESC,
            COALESCE(st.snapshot_id, st.ingestion_run_id) DESC
    ) = 1
),
current_trials AS (
    SELECT
        dt.nct_id,
        COALESCE(dt.snapshot_id, dt.ingestion_run_id) AS effective_run_key
    FROM dim_registry_trial dt
    WHERE UPPER(dt.nct_id) != UPPER($nct_id)
),
target_conditions AS (
    SELECT DISTINCT
        pcm.preset_id,
        pcm.condition_group
    FROM int_registry_preset_condition_mapping pcm
    INNER JOIN target tg
        ON pcm.nct_id = tg.nct_id
       AND pcm.snapshot_id = tg.effective_run_key
    WHERE pcm.condition_group IS NOT NULL
    UNION
    SELECT DISTINCT
        '__direct__' AS preset_id,
        tc.condition_group
    FROM stg_registry_trial_conditions tc
    INNER JOIN target tg
        ON tc.nct_id = tg.nct_id
       AND COALESCE(tc.snapshot_id, tc.ingestion_run_id) = tg.effective_run_key
    WHERE tc.condition_group IS NOT NULL
),
target_mesh AS (
    SELECT DISTINCT m.mesh_id, m.is_ancestor
    FROM stg_registry_trial_condition_mesh m
    INNER JOIN target tg
        ON m.nct_id = tg.nct_id
       AND COALESCE(m.snapshot_id, m.ingestion_run_id) = tg.effective_run_key
    WHERE m.mesh_id IS NOT NULL
),
target_interventions AS (
    SELECT DISTINCT ti.intervention_type
    FROM stg_registry_trial_interventions ti
    INNER JOIN target tg
        ON ti.nct_id = tg.nct_id
       AND COALESCE(ti.snapshot_id, ti.ingestion_run_id) = tg.effective_run_key
    WHERE ti.intervention_type IS NOT NULL
),
latest_site_states AS (
    SELECT DISTINCT
        s.nct_id,
        s.state_normalized
    FROM fct_registry_trial_site s
    INNER JOIN (
        SELECT
            nct_id,
            MAX(snapshot_date) AS max_snapshot_date
        FROM fct_registry_trial_site
        GROUP BY nct_id
    ) ls
        ON s.nct_id = ls.nct_id
       AND (s.snapshot_date IS NOT DISTINCT FROM ls.max_snapshot_date)
    WHERE s.state_normalized IS NOT NULL
),
target_states AS (
    SELECT DISTINCT s.state_normalized
    FROM latest_site_states s
    WHERE UPPER(s.nct_id) = UPPER($nct_id)
),
condition_group_matches AS (
    SELECT DISTINCT
        pcm.nct_id,
        ct.effective_run_key,
        1 AS has_same_condition
    FROM int_registry_preset_condition_mapping pcm
    INNER JOIN current_trials ct
        ON pcm.nct_id = ct.nct_id
       AND (ct.effective_run_key IS NULL OR pcm.snapshot_id = ct.effective_run_key)
    INNER JOIN target_conditions tcond
        ON pcm.preset_id = tcond.preset_id
       AND pcm.condition_group = tcond.condition_group
    UNION
    SELECT DISTINCT
        tc.nct_id,
        ct.effective_run_key,
        1 AS has_same_condition
    FROM stg_registry_trial_conditions tc
    INNER JOIN current_trials ct
        ON tc.nct_id = ct.nct_id
       AND (
           ct.effective_run_key IS NULL
           OR COALESCE(tc.snapshot_id, tc.ingestion_run_id) = ct.effective_run_key
       )
    INNER JOIN target_conditions tcond
        ON tcond.preset_id = '__direct__'
       AND tc.condition_group = tcond.condition_group
),
mesh_matches AS (
    SELECT DISTINCT
        m.nct_id,
        ct.effective_run_key,
        CASE WHEN NOT tm.is_ancestor AND NOT m.is_ancestor THEN 2 ELSE 1 END AS mesh_match_depth
    FROM stg_registry_trial_condition_mesh m
    INNER JOIN current_trials ct
        ON m.nct_id = ct.nct_id
       AND (
           ct.effective_run_key IS NULL
           OR COALESCE(m.snapshot_id, m.ingestion_run_id) = ct.effective_run_key
       )
    INNER JOIN target_mesh tm
        ON m.mesh_id = tm.mesh_id
    WHERE NOT (tm.is_ancestor AND m.is_ancestor)
),
candidate_pool AS (
    SELECT
        nct_id,
        MAX(effective_run_key) AS effective_run_key,
        MAX(has_same_condition) AS has_same_condition,
        MAX(mesh_match_depth) AS mesh_match_depth
    FROM (
        SELECT nct_id, effective_run_key, has_same_condition, 0 AS mesh_match_depth FROM condition_group_matches
        UNION ALL
        SELECT nct_id, effective_run_key, 0 AS has_same_condition, mesh_match_depth FROM mesh_matches
    )
    GROUP BY nct_id
),
candidate_trials AS (
    SELECT
        cp.nct_id,
        COALESCE(st.snapshot_id, st.ingestion_run_id) AS effective_run_key,
        cp.has_same_condition,
        st.phase_normalized,
        st.study_type,
        st.allocation,
        st.primary_purpose,
        st.enrollment_count,
        CASE
            WHEN st.enrollment_count IS NULL THEN NULL
            WHEN st.enrollment_count < 50 THEN 'small'
            WHEN st.enrollment_count <= 200 THEN 'medium'
            ELSE 'large'
        END AS enrollment_band,
        st.healthy_volunteers,
        COALESCE(st.sex, 'ALL') AS sex,
        st.minimum_age_years,
        st.maximum_age_years,
        CASE WHEN st.phase_normalized = tg.phase_normalized THEN 1 ELSE 0 END AS phase_match,
        cp.has_same_condition AS cond_match,
        cp.mesh_match_depth
    FROM candidate_pool cp
    INNER JOIN stg_registry_trials st
        ON cp.nct_id = st.nct_id
       AND (
           cp.effective_run_key IS NULL
           OR COALESCE(st.snapshot_id, st.ingestion_run_id) = cp.effective_run_key
       )
    LEFT JOIN stg_registry_trial_snapshots sm
        ON COALESCE(st.snapshot_id, st.ingestion_run_id) = sm.ingestion_run_id
    CROSS JOIN target tg
    WHERE NOT EXISTS (SELECT 1 FROM stg_registry_trial_snapshots)
       OR (
           sm.ingestion_run_id IS NOT NULL
           AND sm.status = 'success'
           AND sm.profile IN ('full_catalog', 'full-catalog', 'default')
           AND (sm.condition IS NULL OR trim(sm.condition) = '')
       )
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY cp.nct_id
        ORDER BY
            COALESCE(sm.started_at_utc, st.snapshot_timestamp_utc) DESC,
            st.snapshot_timestamp_utc DESC,
            COALESCE(st.snapshot_id, st.ingestion_run_id) DESC
    ) = 1
),
capped_candidates AS (
    SELECT *
    FROM candidate_trials
    ORDER BY phase_match DESC, cond_match DESC, mesh_match_depth DESC, nct_id ASC
    LIMIT 250
),
candidate_dim AS (
    SELECT
        dt.nct_id,
        dt.current_brief_title AS brief_title,
        dt.current_overall_status AS overall_status,
        dt.current_phase AS phase,
        dt.current_lead_sponsor AS lead_sponsor,
        COALESCE(dt.registry_url, 'https://clinicaltrials.gov/study/' || dt.nct_id) AS registry_url
    FROM dim_registry_trial dt
    INNER JOIN capped_candidates cc ON dt.nct_id = cc.nct_id
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY dt.nct_id
        ORDER BY dt.latest_seen_snapshot_date DESC
    ) = 1
),
scored AS (
    SELECT
        cc.nct_id,
        cd.brief_title,
        cd.overall_status,
        cd.phase,
        cd.lead_sponsor,
        cd.registry_url,

        cc.has_same_condition AS same_condition,
        w.w_same_condition AS weight_same_condition,
        ROUND(w.w_same_condition * cc.has_same_condition, 4) AS weighted_same_condition,

        CASE WHEN cc.phase_normalized = tg.phase_normalized THEN 1 ELSE 0 END AS same_phase,
        w.w_same_phase AS weight_same_phase,
        ROUND(w.w_same_phase * (CASE WHEN cc.phase_normalized = tg.phase_normalized THEN 1 ELSE 0 END), 4) AS weighted_same_phase,

        CASE WHEN EXISTS (
            SELECT 1 FROM latest_site_states s
            INNER JOIN target_states ts ON s.state_normalized = ts.state_normalized
            WHERE s.nct_id = cc.nct_id
        ) THEN 1 ELSE 0 END AS geography_overlap,
        w.w_geography_overlap AS weight_geography_overlap,
        ROUND(w.w_geography_overlap * (CASE WHEN EXISTS (
            SELECT 1 FROM latest_site_states s
            INNER JOIN target_states ts ON s.state_normalized = ts.state_normalized
            WHERE s.nct_id = cc.nct_id
        ) THEN 1 ELSE 0 END), 4) AS weighted_geography_overlap,

        CASE WHEN EXISTS (
            SELECT 1 FROM stg_registry_trial_interventions i
            INNER JOIN target_interventions ti ON i.intervention_type = ti.intervention_type
            WHERE i.nct_id = cc.nct_id
              AND COALESCE(i.snapshot_id, i.ingestion_run_id) = cc.effective_run_key
        ) THEN 1 ELSE 0 END AS intervention_type_overlap,
        w.w_intervention_type_overlap AS weight_intervention_type_overlap,
        ROUND(w.w_intervention_type_overlap * (CASE WHEN EXISTS (
            SELECT 1 FROM stg_registry_trial_interventions i
            INNER JOIN target_interventions ti ON i.intervention_type = ti.intervention_type
            WHERE i.nct_id = cc.nct_id
              AND COALESCE(i.snapshot_id, i.ingestion_run_id) = cc.effective_run_key
        ) THEN 1 ELSE 0 END), 4) AS weighted_intervention_type_overlap,

        CASE WHEN cc.study_type = tg.study_type
              AND cc.allocation = tg.allocation
              AND cc.primary_purpose = tg.primary_purpose
             THEN 1 ELSE 0 END AS study_design_match,
        w.w_study_design_match AS weight_study_design_match,
        ROUND(w.w_study_design_match * (
            CASE WHEN cc.study_type = tg.study_type
                  AND cc.allocation = tg.allocation
                  AND cc.primary_purpose = tg.primary_purpose
                 THEN 1 ELSE 0 END
        ), 4) AS weighted_study_design_match,

        CASE WHEN (cc.sex = tg.sex OR cc.sex = 'ALL' OR tg.sex = 'ALL')
              AND cc.healthy_volunteers = tg.healthy_volunteers
              AND (tg.minimum_age_years IS NULL OR cc.maximum_age_years IS NULL OR tg.minimum_age_years <= cc.maximum_age_years)
              AND (cc.minimum_age_years IS NULL OR tg.maximum_age_years IS NULL OR cc.minimum_age_years <= tg.maximum_age_years)
             THEN 1 ELSE 0 END AS eligibility_compatible,
        w.w_eligibility_compatible AS weight_eligibility_compatible,
        ROUND(w.w_eligibility_compatible * (
            CASE WHEN (cc.sex = tg.sex OR cc.sex = 'ALL' OR tg.sex = 'ALL')
                  AND cc.healthy_volunteers = tg.healthy_volunteers
                  AND (tg.minimum_age_years IS NULL OR cc.maximum_age_years IS NULL OR tg.minimum_age_years <= cc.maximum_age_years)
                  AND (cc.minimum_age_years IS NULL OR tg.maximum_age_years IS NULL OR cc.minimum_age_years <= tg.maximum_age_years)
                 THEN 1 ELSE 0 END
        ), 4) AS weighted_eligibility_compatible,

        CASE WHEN cc.enrollment_band = tg.enrollment_band THEN 1 ELSE 0 END AS enrollment_band_match,
        w.w_enrollment_band_match AS weight_enrollment_band_match,
        ROUND(w.w_enrollment_band_match * (CASE WHEN cc.enrollment_band = tg.enrollment_band THEN 1 ELSE 0 END), 4) AS weighted_enrollment_band_match,

        tg.phase_normalized AS target_phase,
        tg.enrollment_band AS target_enrollment_band
    FROM capped_candidates cc
    INNER JOIN candidate_dim cd ON cc.nct_id = cd.nct_id
    CROSS JOIN target tg
    CROSS JOIN weights w
),
computed AS (
    SELECT
        s.*,
        ROUND(
            weighted_same_condition +
            weighted_same_phase +
            weighted_geography_overlap +
            weighted_intervention_type_overlap +
            weighted_study_design_match +
            weighted_eligibility_compatible +
            weighted_enrollment_band_match
        , 4) AS similarity_score,
        CONCAT_WS('; ',
            CASE WHEN s.same_condition = 1 THEN 'shared condition mapping' END,
            CASE WHEN s.same_phase = 1 THEN 'same phase (' || s.target_phase || ')' END,
            CASE WHEN s.geography_overlap = 1 THEN 'overlapping U.S. states' END,
            CASE WHEN s.intervention_type_overlap = 1 THEN 'shared intervention type' END,
            CASE WHEN s.study_design_match = 1 THEN 'matching study type, allocation, and primary purpose' END,
            CASE WHEN s.eligibility_compatible = 1 THEN 'compatible eligibility criteria' END,
            CASE WHEN s.enrollment_band_match = 1 THEN 'similar enrollment size (' || s.target_enrollment_band || ')' END
        ) AS similarity_explanation
    FROM scored s
)
SELECT
    nct_id,
    brief_title,
    overall_status,
    phase,
    lead_sponsor,
    similarity_score,
    ROW_NUMBER() OVER (ORDER BY similarity_score DESC, nct_id ASC) AS similarity_rank,
    same_condition,
    weight_same_condition,
    weighted_same_condition,
    same_phase,
    weight_same_phase,
    weighted_same_phase,
    geography_overlap,
    weight_geography_overlap,
    weighted_geography_overlap,
    intervention_type_overlap,
    weight_intervention_type_overlap,
    weighted_intervention_type_overlap,
    study_design_match,
    weight_study_design_match,
    weighted_study_design_match,
    eligibility_compatible,
    weight_eligibility_compatible,
    weighted_eligibility_compatible,
    enrollment_band_match,
    weight_enrollment_band_match,
    weighted_enrollment_band_match,
    similarity_explanation,
    registry_url
FROM computed
WHERE ($min_score IS NULL OR similarity_score >= $min_score)
QUALIFY similarity_rank <= $limit
ORDER BY similarity_rank;
