-- On-demand pairwise trial comparability query with MeSH-ancestor + phase blocking.
-- Evaluates objective study design comparability factors against target trial $nct_id.
-- Bounded by candidate blocking and hard per-trial cap (Issue #55).

WITH target AS (
    SELECT
        t.nct_id,
        t.current_phase AS phase,
        t.current_study_type AS study_type,
        st.allocation,
        st.primary_purpose,
        t.enrollment_count,
        CASE
            WHEN t.enrollment_count IS NULL THEN NULL
            WHEN t.enrollment_count < 50 THEN 'small'
            WHEN t.enrollment_count <= 200 THEN 'medium'
            ELSE 'large'
        END AS enrollment_band,
        t.accepts_healthy_volunteers AS healthy_volunteers,
        t.sex_eligibility AS sex,
        t.min_age_years,
        t.max_age_years
    FROM dim_trial t
    LEFT JOIN stg_trials st ON t.nct_id = st.nct_id
    WHERE t.nct_id = $nct_id
    LIMIT 1
),
target_mesh AS (
    SELECT DISTINCT mesh_id
    FROM stg_trial_condition_mesh
    WHERE nct_id = $nct_id AND mesh_id IS NOT NULL
),
target_conditions AS (
    SELECT DISTINCT condition_group AS cond
    FROM stg_trial_conditions
    WHERE nct_id = $nct_id AND condition_group IS NOT NULL
),
target_interventions AS (
    SELECT DISTINCT intervention_type AS itype
    FROM stg_trial_interventions
    WHERE nct_id = $nct_id AND intervention_type IS NOT NULL
),
target_states AS (
    SELECT DISTINCT state_normalized AS st
    FROM fct_trial_site
    WHERE nct_id = $nct_id AND state_normalized IS NOT NULL
),
candidate_trials AS (
    -- MeSH-ancestor + phase blocking with hard per-trial cap (250 candidate limit)
    SELECT
        c.nct_id,
        CASE WHEN dt.current_phase = tg.phase THEN 1 ELSE 0 END AS phase_match
    FROM (
        -- Trials sharing MeSH terms or ancestors
        SELECT DISTINCT m.nct_id
        FROM stg_trial_condition_mesh m
        INNER JOIN target_mesh tm ON m.mesh_id = tm.mesh_id
        WHERE m.nct_id != $nct_id

        UNION

        -- Trials sharing condition group (fallback or overlay)
        SELECT DISTINCT tc.nct_id
        FROM stg_trial_conditions tc
        INNER JOIN target_conditions tcond ON tc.condition_group = tcond.cond
        WHERE tc.nct_id != $nct_id
    ) c
    INNER JOIN dim_trial dt ON c.nct_id = dt.nct_id
    CROSS JOIN target tg
    ORDER BY phase_match DESC, c.nct_id ASC
    LIMIT 250
),
candidate_features AS (
    SELECT
        t.nct_id,
        t.current_brief_title AS brief_title,
        t.current_overall_status AS overall_status,
        t.current_phase AS phase,
        t.current_lead_sponsor AS lead_sponsor,
        t.current_study_type AS study_type,
        st.allocation,
        st.primary_purpose,
        t.enrollment_count,
        CASE
            WHEN t.enrollment_count IS NULL THEN NULL
            WHEN t.enrollment_count < 50 THEN 'small'
            WHEN t.enrollment_count <= 200 THEN 'medium'
            ELSE 'large'
        END AS enrollment_band,
        t.accepts_healthy_volunteers AS healthy_volunteers,
        t.sex_eligibility AS sex,
        t.min_age_years,
        t.max_age_years,
        COALESCE(t.registry_url, 'https://clinicaltrials.gov/study/' || t.nct_id) AS registry_url
    FROM dim_trial t
    INNER JOIN candidate_trials ct ON t.nct_id = ct.nct_id
    LEFT JOIN stg_trials st ON t.nct_id = st.nct_id
),
scored AS (
    SELECT
        cf.nct_id,
        cf.brief_title,
        cf.overall_status,
        cf.phase,
        cf.lead_sponsor,
        cf.registry_url,
        1 AS same_condition,
        0.15 AS weight_same_condition,
        ROUND(0.15 * 1, 4) AS weighted_same_condition,
        CASE WHEN cf.phase = tg.phase THEN 1 ELSE 0 END AS same_phase,
        0.20 AS weight_same_phase,
        ROUND(0.20 * CASE WHEN cf.phase = tg.phase THEN 1 ELSE 0 END, 4) AS weighted_same_phase,
        CASE WHEN EXISTS (
            SELECT 1 FROM fct_trial_site s
            INNER JOIN target_states ts ON s.state_normalized = ts.st
            WHERE s.nct_id = cf.nct_id
        ) THEN 1 ELSE 0 END AS geography_overlap,
        0.15 AS weight_geography_overlap,
        ROUND(0.15 * CASE WHEN EXISTS (
            SELECT 1 FROM fct_trial_site s
            INNER JOIN target_states ts ON s.state_normalized = ts.st
            WHERE s.nct_id = cf.nct_id
        ) THEN 1 ELSE 0 END, 4) AS weighted_geography_overlap,
        CASE WHEN EXISTS (
            SELECT 1 FROM stg_trial_interventions i
            INNER JOIN target_interventions ti ON i.intervention_type = ti.itype
            WHERE i.nct_id = cf.nct_id
        ) THEN 1 ELSE 0 END AS intervention_type_overlap,
        0.15 AS weight_intervention_type_overlap,
        ROUND(0.15 * CASE WHEN EXISTS (
            SELECT 1 FROM stg_trial_interventions i
            INNER JOIN target_interventions ti ON i.intervention_type = ti.itype
            WHERE i.nct_id = cf.nct_id
        ) THEN 1 ELSE 0 END, 4) AS weighted_intervention_type_overlap,
        CASE WHEN cf.study_type = tg.study_type
              AND cf.allocation IS NOT DISTINCT FROM tg.allocation
              AND cf.primary_purpose IS NOT DISTINCT FROM tg.primary_purpose
             THEN 1 ELSE 0 END AS study_design_match,
        0.15 AS weight_study_design_match,
        ROUND(0.15 * CASE WHEN cf.study_type = tg.study_type
              AND cf.allocation IS NOT DISTINCT FROM tg.allocation
              AND cf.primary_purpose IS NOT DISTINCT FROM tg.primary_purpose
             THEN 1 ELSE 0 END, 4) AS weighted_study_design_match,
        CASE WHEN (cf.sex = tg.sex OR cf.sex = 'ALL' OR tg.sex = 'ALL')
              AND cf.healthy_volunteers IS NOT DISTINCT FROM tg.healthy_volunteers
              AND (tg.min_age_years IS NULL OR cf.max_age_years IS NULL OR tg.min_age_years <= cf.max_age_years)
              AND (cf.min_age_years IS NULL OR tg.max_age_years IS NULL OR cf.min_age_years <= tg.max_age_years)
             THEN 1 ELSE 0 END AS eligibility_compatible,
        0.10 AS weight_eligibility_compatible,
        ROUND(0.10 * CASE WHEN (cf.sex = tg.sex OR cf.sex = 'ALL' OR tg.sex = 'ALL')
              AND cf.healthy_volunteers IS NOT DISTINCT FROM tg.healthy_volunteers
              AND (tg.min_age_years IS NULL OR cf.max_age_years IS NULL OR tg.min_age_years <= cf.max_age_years)
              AND (cf.min_age_years IS NULL OR tg.max_age_years IS NULL OR cf.min_age_years <= tg.max_age_years)
             THEN 1 ELSE 0 END, 4) AS weighted_eligibility_compatible,
        CASE WHEN cf.enrollment_band IS NOT NULL AND cf.enrollment_band = tg.enrollment_band THEN 1 ELSE 0 END AS enrollment_band_match,
        0.10 AS weight_enrollment_band_match,
        ROUND(0.10 * CASE WHEN cf.enrollment_band IS NOT NULL AND cf.enrollment_band = tg.enrollment_band THEN 1 ELSE 0 END, 4) AS weighted_enrollment_band_match,
        tg.phase AS target_phase,
        tg.enrollment_band AS target_enrollment_band
    FROM candidate_features cf
    CROSS JOIN target tg
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
