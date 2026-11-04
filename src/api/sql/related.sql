-- On-demand pairwise trial comparability query with MeSH-ancestor + phase blocking.
-- Evaluates objective study design comparability factors against target trial $nct_id.
-- Bounded by candidate blocking and hard per-trial cap (Issue #55).

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
    FROM stg_trials st
    WHERE UPPER(st.nct_id) = UPPER($nct_id)
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY st.nct_id
        ORDER BY st.snapshot_timestamp_utc DESC, st.ingestion_run_id DESC
    ) = 1
),
target_conditions AS (
    SELECT DISTINCT tc.condition_group
    FROM stg_trial_conditions tc
    WHERE UPPER(tc.nct_id) = UPPER($nct_id)
      AND tc.condition_group IS NOT NULL
),
target_mesh AS (
    SELECT DISTINCT m.mesh_id, m.is_ancestor
    FROM stg_trial_condition_mesh m
    WHERE UPPER(m.nct_id) = UPPER($nct_id)
      AND m.mesh_id IS NOT NULL
),
target_interventions AS (
    SELECT DISTINCT ti.intervention_type
    FROM stg_trial_interventions ti
    WHERE UPPER(ti.nct_id) = UPPER($nct_id)
      AND ti.intervention_type IS NOT NULL
),
target_states AS (
    SELECT DISTINCT s.state_normalized
    FROM fct_trial_site s
    WHERE UPPER(s.nct_id) = UPPER($nct_id)
      AND s.state_normalized IS NOT NULL
),
condition_group_matches AS (
    SELECT DISTINCT tc.nct_id, 1 AS has_same_condition
    FROM stg_trial_conditions tc
    INNER JOIN target_conditions tcond
        ON tc.condition_group = tcond.condition_group
    WHERE UPPER(tc.nct_id) != UPPER($nct_id)
),
mesh_matches AS (
    SELECT DISTINCT
        m.nct_id,
        CASE WHEN NOT tm.is_ancestor AND NOT m.is_ancestor THEN 2 ELSE 1 END AS mesh_match_depth
    FROM stg_trial_condition_mesh m
    INNER JOIN target_mesh tm
        ON m.mesh_id = tm.mesh_id
    WHERE UPPER(m.nct_id) != UPPER($nct_id)
      AND NOT (tm.is_ancestor AND m.is_ancestor)
),
candidate_pool AS (
    SELECT
        nct_id,
        MAX(has_same_condition) AS has_same_condition,
        MAX(mesh_match_depth) AS mesh_match_depth
    FROM (
        SELECT nct_id, has_same_condition, 0 AS mesh_match_depth FROM condition_group_matches
        UNION ALL
        SELECT nct_id, 0 AS has_same_condition, mesh_match_depth FROM mesh_matches
    )
    GROUP BY nct_id
),
candidate_trials AS (
    SELECT
        cp.nct_id,
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
    INNER JOIN stg_trials st
        ON cp.nct_id = st.nct_id
    CROSS JOIN target tg
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY cp.nct_id
        ORDER BY st.snapshot_timestamp_utc DESC, st.ingestion_run_id DESC
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
    FROM dim_trial dt
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
            SELECT 1 FROM fct_trial_site s
            INNER JOIN target_states ts ON s.state_normalized = ts.state_normalized
            WHERE s.nct_id = cc.nct_id
        ) THEN 1 ELSE 0 END AS geography_overlap,
        w.w_geography_overlap AS weight_geography_overlap,
        ROUND(w.w_geography_overlap * (CASE WHEN EXISTS (
            SELECT 1 FROM fct_trial_site s
            INNER JOIN target_states ts ON s.state_normalized = ts.state_normalized
            WHERE s.nct_id = cc.nct_id
        ) THEN 1 ELSE 0 END), 4) AS weighted_geography_overlap,

        CASE WHEN EXISTS (
            SELECT 1 FROM stg_trial_interventions i
            INNER JOIN target_interventions ti ON i.intervention_type = ti.intervention_type
            WHERE i.nct_id = cc.nct_id
        ) THEN 1 ELSE 0 END AS intervention_type_overlap,
        w.w_intervention_type_overlap AS weight_intervention_type_overlap,
        ROUND(w.w_intervention_type_overlap * (CASE WHEN EXISTS (
            SELECT 1 FROM stg_trial_interventions i
            INNER JOIN target_interventions ti ON i.intervention_type = ti.intervention_type
            WHERE i.nct_id = cc.nct_id
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
