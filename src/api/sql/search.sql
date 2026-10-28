-- Search trials across warehouse marts and staging tables with full parameterization.
-- Strictly complies with ADR 0065: zero coordinator/investigator contact PII.

WITH latest_trial_run AS (
    SELECT
        nct_id,
        ingestion_run_id,
        last_update_post_date,
        ROW_NUMBER() OVER (
            PARTITION BY nct_id 
            ORDER BY snapshot_timestamp_utc DESC, ingestion_run_id DESC
        ) AS run_rn
    FROM stg_trials
),

active_trial_runs AS (
    SELECT
        nct_id,
        ingestion_run_id,
        CAST(last_update_post_date AS VARCHAR) AS last_update_post_date
    FROM latest_trial_run
    WHERE run_rn = 1
),

distinct_trials AS (
    SELECT
        dt.nct_id,
        dt.current_brief_title AS brief_title,
        dt.current_overall_status AS overall_status,
        dt.current_phase AS phase,
        dt.current_study_type AS study_type,
        dt.current_lead_sponsor AS lead_sponsor,
        CAST(dt.start_date AS VARCHAR) AS start_date,
        dt.min_age_years,
        dt.max_age_years,
        dt.sex_eligibility,
        dt.accepts_healthy_volunteers,
        dt.std_age_groups,
        dt.registry_url,
        atr.ingestion_run_id,
        atr.last_update_post_date,
        ROW_NUMBER() OVER (PARTITION BY dt.nct_id ORDER BY dt.latest_seen_snapshot_date DESC) AS trial_rn
    FROM dim_trial dt
    LEFT JOIN active_trial_runs atr ON dt.nct_id = atr.nct_id
),

-- Issue #63: only recruiting / not-yet-recruiting sites count as "available
-- near me"; other site statuses are inactive here (the detail endpoint still
-- lists them with their status). Registry geoPoint coordinates are preferred;
-- sites missing them fall back to the offline Census Gazetteer ZIP (ZCTA) or
-- city centroid and are flagged approximate. Gazetteer views always exist
-- (empty when the reference files were never downloaded via `make gazetteer`).
nearest_sites AS (
    SELECT
        nct_id,
        facility_name,
        city,
        state,
        location_status,
        coordinate_approximate,
        -- Haversine in miles; propagates to NULL when the user point or the
        -- site's effective coordinates are missing.
        2 * 3958.8 * asin(sqrt(
            greatest(0.0, least(1.0,
                power(sin(radians(site_latitude - $lat) / 2.0), 2) +
                cos(radians($lat)) * cos(radians(site_latitude)) *
                power(sin(radians(site_longitude - $lon) / 2.0), 2)
            ))
        )) AS distance_miles,
        ROW_NUMBER() OVER (PARTITION BY nct_id ORDER BY distance_miles ASC) AS site_rn
    FROM (
        SELECT
            loc.nct_id,
            loc.facility_name,
            loc.city,
            COALESCE(loc.state_raw, loc.state_normalized) AS state,
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
        FROM stg_trial_locations loc
        INNER JOIN active_trial_runs atr
            ON loc.nct_id = atr.nct_id AND loc.ingestion_run_id = atr.ingestion_run_id
        LEFT JOIN gazetteer_zcta gz ON left(loc.zip_code, 5) = gz.zcta
        LEFT JOIN gazetteer_place gp
            ON gp.state_usps = loc.state_normalized
           AND (gp.city_name = loc.city_normalized OR gp.city_name_stripped = loc.city_normalized)
        WHERE upper(COALESCE(loc.location_status, '')) IN ('RECRUITING', 'NOT_YET_RECRUITING')
          AND (
              $lat IS NULL OR
              (loc.latitude IS NOT NULL AND loc.longitude IS NOT NULL) OR
              gz.zcta IS NOT NULL OR
              gp.state_usps IS NOT NULL
          )
    )
),

matched_candidates AS (
    SELECT
        dt.nct_id,
        dt.brief_title,
        dt.overall_status,
        dt.phase,
        dt.study_type,
        dt.lead_sponsor,
        (
            SELECT min(ts.sponsor_class)
            FROM stg_trial_sponsors ts
            WHERE ts.nct_id = dt.nct_id
              AND (dt.ingestion_run_id IS NULL OR ts.ingestion_run_id = dt.ingestion_run_id)
              AND ts.sponsor_role = 'lead_sponsor'
        ) AS sponsor_class,
        dt.start_date,
        dt.last_update_post_date,
        (
            SELECT list(DISTINCT tc.condition_raw ORDER BY tc.condition_raw)
            FROM stg_trial_conditions tc
            WHERE tc.nct_id = dt.nct_id
              AND (dt.ingestion_run_id IS NULL OR tc.ingestion_run_id = dt.ingestion_run_id)
        ) AS conditions,
        dt.min_age_years,
        dt.max_age_years,
        CASE
            WHEN dt.min_age_years IS NULL AND dt.max_age_years IS NULL THEN TRUE
            ELSE FALSE
        END AS age_not_stated,
        COALESCE(dt.sex_eligibility, 'ALL') AS sex,
        dt.accepts_healthy_volunteers,
        COALESCE(dt.std_age_groups, []) AS std_age_groups,
        ns.facility_name AS nearest_facility_name,
        ns.city AS nearest_city,
        ns.state AS nearest_state,
        ns.location_status AS nearest_location_status,
        ns.coordinate_approximate AS nearest_coordinate_approximate,
        round(ns.distance_miles, 1) AS nearest_distance_miles,
        ns.distance_miles AS raw_distance_miles,
        dt.registry_url
    FROM distinct_trials dt
    LEFT JOIN nearest_sites ns
        ON dt.nct_id = ns.nct_id AND ns.site_rn = 1
    WHERE dt.trial_rn = 1
      AND ($status IS NULL OR upper(dt.overall_status) = upper($status))
      AND (
          $phase IS NULL OR
          upper(dt.phase) = upper($phase) OR
          list_contains(string_split(upper(dt.phase), '/'), upper($phase))
      )
      AND ($min_age IS NULL OR dt.max_age_years IS NULL OR dt.max_age_years >= $min_age)
      AND ($max_age IS NULL OR dt.min_age_years IS NULL OR dt.min_age_years <= $max_age)
      AND (
          $sex IS NULL OR
          upper($sex) = 'ALL' OR
          upper(COALESCE(dt.sex_eligibility, 'ALL')) IN ('ALL', upper($sex), 'UNKNOWN')
      )
      AND (
          $healthy_volunteers IS NULL OR
          dt.accepts_healthy_volunteers IS NULL OR
          dt.accepts_healthy_volunteers = $healthy_volunteers
      )
      AND (
          $std_age_group IS NULL OR
          dt.std_age_groups IS NULL OR
          list_contains(dt.std_age_groups, $std_age_group)
      )
      AND (
          $sponsor_class IS NULL OR
          EXISTS (
              SELECT 1 FROM stg_trial_sponsors ts
              WHERE ts.nct_id = dt.nct_id
                AND (dt.ingestion_run_id IS NULL OR ts.ingestion_run_id = dt.ingestion_run_id)
                AND ts.sponsor_class = $sponsor_class
          )
      )
      AND (
          $intervention_type IS NULL OR
          EXISTS (
              SELECT 1 FROM stg_trial_interventions ti
              WHERE ti.nct_id = dt.nct_id
                AND (dt.ingestion_run_id IS NULL OR ti.ingestion_run_id = dt.ingestion_run_id)
                AND upper(ti.intervention_type) = upper($intervention_type)
          )
      )
      AND (
          $cond IS NULL OR
          lower(dt.brief_title) LIKE '%' || lower($cond) || '%' OR
          EXISTS (
              SELECT 1 FROM stg_trial_conditions tc
              WHERE tc.nct_id = dt.nct_id
                AND (dt.ingestion_run_id IS NULL OR tc.ingestion_run_id = dt.ingestion_run_id)
                AND (
                    lower(tc.condition_raw) LIKE '%' || lower($cond) || '%' OR
                    lower(tc.condition_group) LIKE '%' || lower($cond) || '%' OR
                    lower(tc.condition_normalized) LIKE '%' || lower($cond) || '%'
                )
          )
      )
      AND (
          $lat IS NULL OR
          (ns.distance_miles IS NOT NULL AND ns.distance_miles <= $radius_mi)
      )
),

matched_with_totals AS (
    SELECT
        mc.*,
        COUNT(*) OVER () AS total_unpaged_count
    FROM matched_candidates mc
    WHERE (
        $updated_since IS NULL OR
        (
            mc.last_update_post_date IS NOT NULL AND
            strptime(mc.last_update_post_date, '%Y-%m-%d') >= strptime($updated_since, '%Y-%m-%d')
        )
    )
)

SELECT *
FROM matched_with_totals
WHERE (
    $has_cursor IS FALSE OR
    ($sort = '-updated' AND (
        ($cursor_val IS NOT NULL AND (
            (last_update_post_date < $cursor_val) OR
            (last_update_post_date = $cursor_val AND nct_id < $cursor_id) OR
            (last_update_post_date IS NULL)
        )) OR
        ($cursor_val IS NULL AND (
            last_update_post_date IS NULL AND nct_id < $cursor_id
        ))
    )) OR
    ($sort = 'updated' AND (
        ($cursor_val IS NOT NULL AND (
            (last_update_post_date > $cursor_val) OR
            (last_update_post_date = $cursor_val AND nct_id > $cursor_id) OR
            (last_update_post_date IS NULL)
        )) OR
        ($cursor_val IS NULL AND (
            last_update_post_date IS NULL AND nct_id > $cursor_id
        ))
    )) OR
    ($sort = '-start_date' AND (
        ($cursor_val IS NOT NULL AND (
            (start_date < $cursor_val) OR
            (start_date = $cursor_val AND nct_id < $cursor_id) OR
            (start_date IS NULL)
        )) OR
        ($cursor_val IS NULL AND (
            start_date IS NULL AND nct_id < $cursor_id
        ))
    )) OR
    ($sort = 'start_date' AND (
        ($cursor_val IS NOT NULL AND (
            (start_date > $cursor_val) OR
            (start_date = $cursor_val AND nct_id > $cursor_id) OR
            (start_date IS NULL)
        )) OR
        ($cursor_val IS NULL AND (
            start_date IS NULL AND nct_id > $cursor_id
        ))
    )) OR
    ($sort = 'distance' AND (
        ($cursor_dist IS NOT NULL AND (
            (raw_distance_miles > $cursor_dist) OR
            (raw_distance_miles = $cursor_dist AND nct_id > $cursor_id) OR
            (raw_distance_miles IS NULL)
        )) OR
        ($cursor_dist IS NULL AND (
            raw_distance_miles IS NULL AND nct_id > $cursor_id
        ))
    ))
)
ORDER BY
    CASE WHEN $sort = 'distance' THEN raw_distance_miles END ASC NULLS LAST,
    CASE WHEN $sort = 'updated' THEN last_update_post_date END ASC NULLS LAST,
    CASE WHEN $sort = '-updated' THEN last_update_post_date END DESC NULLS LAST,
    CASE WHEN $sort = 'start_date' THEN start_date END ASC NULLS LAST,
    CASE WHEN $sort = '-start_date' THEN start_date END DESC NULLS LAST,
    CASE WHEN $sort IN ('-updated', '-start_date') THEN nct_id END DESC,
    nct_id ASC
LIMIT $limit_plus_one;
