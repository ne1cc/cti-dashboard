"""DuckDB read-only warehouse connection manager and query adapter for the read API.

Conforms strictly to ADR 0065 (contact privacy guardrails: zero coordinator/investigator PII)
and Issue #59 architecture specifications. Reads canonical registry-grain warehouse tables
(`registry_trial_key = md5(nct_id)`); a live warehouse built on the legacy profile grain
requires a full rebuild before cutover.
Clean separation: never imports dashboard.components.data or touches Streamlit state.
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from src.api.gazetteer import register_gazetteer_views
from src.api.models import (
    ComparabilityFactors,
    DataProvenance,
    EligibilitySection,
    EligibilitySections,
    EnrollmentInfo,
    FactorScore,
    GeoPoint,
    NearestSiteSummary,
    PaginationMetadata,
    ReadinessStatus,
    RelatedTrialItem,
    RelatedTrialsResponse,
    SponsorDetail,
    StructuredEligibility,
    StructuredEligibilitySummary,
    StudyArm,
    StudyCondition,
    StudyDates,
    StudyDesign,
    StudyIntervention,
    StudySponsors,
    TrialDetail,
    TrialSite,
    TrialsListResponse,
    TrialSummary,
)
from src.transform.eligibility_sections import split_eligibility_sections

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parent / "sql"
SEARCH_SQL_PATH = SQL_DIR / "search.sql"
DETAIL_SQL_PATH = SQL_DIR / "detail.sql"
RELATED_SQL_PATH = SQL_DIR / "related.sql"

REQUIRED_TABLES = (
    "dim_registry_trial",
    "fct_registry_trial_snapshot",
    "fct_registry_trial_site",
    "stg_registry_trials",
    "stg_registry_trial_locations",
    "stg_registry_trial_sponsors",
    "stg_registry_trial_conditions",
    "stg_registry_trial_interventions",
    "stg_registry_trial_arms",
)

# Compatibility fallback ONLY for pre-cutover profile-only dbt fixtures
# (e.g. similarity_equivalence_root before phase 5 rebuilds them at full-catalog grain)
# where dim_registry_trial is empty while dim_trial is populated.
_LEGACY_RELATED_SQL = """
WITH weights AS (
    SELECT
        MAX(CASE WHEN component = 'same_condition' THEN weight END) AS w_same_condition,
        MAX(CASE WHEN component = 'same_phase' THEN weight END) AS w_same_phase,
        MAX(CASE WHEN component = 'geography_overlap' THEN weight END) AS w_geography_overlap,
        MAX(CASE WHEN component = 'intervention_type_overlap' THEN weight END)
            AS w_intervention_type_overlap,
        MAX(CASE WHEN component = 'study_design_match' THEN weight END) AS w_study_design_match,
        MAX(CASE WHEN component = 'eligibility_compatible' THEN weight END)
            AS w_eligibility_compatible,
        MAX(CASE WHEN component = 'enrollment_band_match' THEN weight END)
            AS w_enrollment_band_match
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
        ROUND(
            w.w_same_phase
            * (CASE WHEN cc.phase_normalized = tg.phase_normalized THEN 1 ELSE 0 END),
            4
        ) AS weighted_same_phase,

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
              AND (
                  tg.minimum_age_years IS NULL
                  OR cc.maximum_age_years IS NULL
                  OR tg.minimum_age_years <= cc.maximum_age_years
              )
              AND (
                  cc.minimum_age_years IS NULL
                  OR tg.maximum_age_years IS NULL
                  OR cc.minimum_age_years <= tg.maximum_age_years
              )
             THEN 1 ELSE 0 END AS eligibility_compatible,
        w.w_eligibility_compatible AS weight_eligibility_compatible,
        ROUND(w.w_eligibility_compatible * (
            CASE WHEN (cc.sex = tg.sex OR cc.sex = 'ALL' OR tg.sex = 'ALL')
                  AND cc.healthy_volunteers = tg.healthy_volunteers
                  AND (
                      tg.minimum_age_years IS NULL
                      OR cc.maximum_age_years IS NULL
                      OR tg.minimum_age_years <= cc.maximum_age_years
                  )
                  AND (
                      cc.minimum_age_years IS NULL
                      OR tg.maximum_age_years IS NULL
                      OR cc.minimum_age_years <= tg.maximum_age_years
                  )
                 THEN 1 ELSE 0 END
        ), 4) AS weighted_eligibility_compatible,

        CASE WHEN cc.enrollment_band = tg.enrollment_band THEN 1 ELSE 0 END
            AS enrollment_band_match,
        w.w_enrollment_band_match AS weight_enrollment_band_match,
        ROUND(
            w.w_enrollment_band_match
            * (CASE WHEN cc.enrollment_band = tg.enrollment_band THEN 1 ELSE 0 END),
            4
        ) AS weighted_enrollment_band_match,

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
            CASE WHEN s.study_design_match = 1
                THEN 'matching study type, allocation, and primary purpose' END,
            CASE WHEN s.eligibility_compatible = 1 THEN 'compatible eligibility criteria' END,
            CASE WHEN s.enrollment_band_match = 1
                THEN 'similar enrollment size (' || s.target_enrollment_band || ')' END
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
"""


def _escape_like(text: str) -> str:
    """Make LIKE wildcards literal; the SQL declares ESCAPE '\\'."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class WarehouseNotReadyError(Exception):
    """Raised when warehouse database is missing, corrupted, or inaccessible."""


class WarehousePoolTimeoutError(TimeoutError):
    """Raised when acquiring a connection from the DuckDB pool times out."""


def encode_keyset_cursor(payload: dict[str, Any]) -> str:
    """Encode pagination cursor token to URL-safe base64 string."""
    raw_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw_json.encode("utf-8")).decode("utf-8")


def decode_keyset_cursor(cursor_str: str) -> dict[str, Any]:
    """Decode URL-safe base64 cursor token to dictionary.

    Raises:
        ValueError: When token is malformed, not valid base64, or not JSON dict.
    """
    try:
        raw_json = base64.urlsafe_b64decode(cursor_str.strip().encode("utf-8")).decode("utf-8")
        parsed = json.loads(raw_json)
        if not isinstance(parsed, dict):
            raise ValueError("Cursor payload must be a JSON dictionary")
        return parsed
    except Exception as err:
        raise ValueError(f"Malformed pagination cursor: {err}") from err


class DuckDBConnectionPool:
    """Thread-safe, bounded, read-only DuckDB connection pool."""

    def __init__(
        self,
        database_path: str | Path,
        max_connections: int = 10,
        timeout: float = 5.0,
        gazetteer_dir: str | Path | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.gazetteer_dir = gazetteer_dir
        self.max_connections = max(1, max_connections)
        self.timeout = timeout
        self._pool: queue.Queue[duckdb.DuckDBPyConnection] = queue.Queue(
            maxsize=self.max_connections
        )
        self._lock = threading.Lock()
        self._created_count = 0
        self._closed = False

    def _create_connection(self) -> duckdb.DuckDBPyConnection:
        if not self.database_path.exists():
            raise WarehouseNotReadyError(f"Warehouse database file not found: {self.database_path}")
        try:
            conn = duckdb.connect(str(self.database_path), read_only=True)
            conn.execute("SET TimeZone = 'UTC';")
            # Add existing schemas to search_path so relations resolve transparently
            schemas = {
                row[0]
                for row in conn.execute(
                    "SELECT schema_name FROM information_schema.schemata;"
                ).fetchall()
            }
            candidates = [
                "main_marts",
                "main_intermediate",
                "main_staging",
                "main_seeds",
                "marts",
                "intermediate",
                "staging",
                "seeds",
                "main",
            ]
            valid = [s for s in candidates if s in schemas]
            if valid:
                path_str = ",".join(valid)
                conn.execute(f"SET search_path = '{path_str}';")
            # Offline Gazetteer fallback coordinates (Issue #63): always present
            # (empty when reference files are missing) so queries stay bindable.
            register_gazetteer_views(conn, self.gazetteer_dir)

            # Ensure optional registry views exist so SQL remains bindable on
            # minimal test fixtures that omit MeSH, manifest, or preset bridges.
            existing_tables = {
                row[0].lower()
                for row in conn.execute(
                    "SELECT DISTINCT lower(table_name) FROM information_schema.tables;"
                ).fetchall()
            }
            if "stg_registry_trial_condition_mesh" not in existing_tables:
                conn.execute("""
                    CREATE TEMP VIEW stg_registry_trial_condition_mesh AS
                    SELECT
                        cast(null as varchar) as registry_trial_key,
                        cast(null as varchar) as ingestion_run_id,
                        cast(null as varchar) as snapshot_id,
                        cast(null as varchar) as nct_id,
                        cast(null as varchar) as mesh_id,
                        cast(null as varchar) as mesh_term,
                        cast(null as boolean) as is_ancestor,
                        cast(null as varchar) as branch,
                        cast(null as varchar) as source_json_hash
                    WHERE false;
                """)
            if "stg_registry_trial_snapshots" not in existing_tables:
                conn.execute("""
                    CREATE TEMP VIEW stg_registry_trial_snapshots AS
                    SELECT
                        cast(null as varchar) as ingestion_run_id,
                        cast(null as varchar) as query_hash,
                        cast(null as varchar) as condition,
                        cast(null as varchar) as mode,
                        cast(null as varchar) as status,
                        cast(null as timestamp) as started_at_utc,
                        cast(null as timestamp) as ended_at_utc,
                        cast(null as date) as snapshot_date,
                        cast(null as integer) as page_count,
                        cast(null as integer) as record_count,
                        cast(null as integer) as total_count_reported,
                        cast(null as integer) as quarantined_record_count,
                        cast(null as varchar) as profile,
                        cast(null as varchar) as error
                    WHERE false;
                """)
            if "bridge_preset_trial" not in existing_tables:
                conn.execute("""
                    CREATE TEMP VIEW bridge_preset_trial AS
                    SELECT
                        cast(null as varchar) as preset_trial_key,
                        cast(null as varchar) as preset_id,
                        cast(null as varchar) as nct_id,
                        cast(null as varchar) as registry_trial_key,
                        cast(null as varchar) as snapshot_id,
                        cast(null as varchar) as ingestion_run_id,
                        cast(null as date) as snapshot_date,
                        cast(null as timestamp) as snapshot_timestamp_utc,
                        cast(null as boolean) as is_unassigned,
                        cast(null as varchar) as membership_scope,
                        cast(null as boolean) as current_record_flag
                    WHERE false;
                """)
            if "bridge_registry_trial_condition" not in existing_tables:
                conn.execute("""
                    CREATE TEMP VIEW bridge_registry_trial_condition AS
                    SELECT
                        cast(null as varchar) as registry_trial_condition_key,
                        cast(null as varchar) as registry_trial_key,
                        cast(null as varchar) as registry_condition_key,
                        cast(null as varchar) as nct_id,
                        cast(null as varchar) as node_id,
                        cast(null as varchar) as condition_group,
                        cast(null as varchar) as mesh_id,
                        cast(null as boolean) as is_ancestor,
                        cast(null as varchar) as mapping_source,
                        cast(null as boolean) as dementia_relevance_flag,
                        cast(null as varchar) as mapping_confidence,
                        cast(null as bigint) as source_condition_count
                    WHERE false;
                """)
            if "int_registry_preset_condition_mapping" not in existing_tables:
                conn.execute("""
                    CREATE TEMP VIEW int_registry_preset_condition_mapping AS
                    SELECT
                        cast(null as varchar) as preset_id,
                        cast(null as varchar) as snapshot_id,
                        cast(null as date) as snapshot_date,
                        cast(null as varchar) as nct_id,
                        cast(null as varchar) as condition_group,
                        cast(null as boolean) as dementia_relevance_flag,
                        cast(null as varchar) as mapping_confidence,
                        cast(null as varchar) as mapping_source,
                        cast(null as bigint) as source_condition_count
                    WHERE false;
                """)
            return conn
        except Exception as err:
            raise WarehouseNotReadyError(f"Failed to connect to DuckDB warehouse: {err}") from err

    def acquire(self) -> duckdb.DuckDBPyConnection:
        """Acquire a read-only DuckDB connection from the pool."""
        if self._closed:
            raise RuntimeError("Connection pool is closed")

        # Try to retrieve an idle connection first
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            pass

        # Try to create a new connection if below capacity
        with self._lock:
            if self._created_count < self.max_connections:
                conn = self._create_connection()
                self._created_count += 1
                return conn

        # Wait for a connection to be returned
        try:
            return self._pool.get(timeout=self.timeout)
        except queue.Empty as err:
            raise WarehousePoolTimeoutError(
                f"Timed out after {self.timeout}s waiting for a DuckDB connection from pool"
            ) from err

    def release(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Return a connection to the pool or close it if the pool is closed."""
        if self._closed:
            try:
                conn.close()
            except Exception:
                pass
            return

        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            try:
                conn.close()
            except Exception:
                pass
            with self._lock:
                self._created_count -= 1

    @contextmanager
    def connection(self) -> Generator[duckdb.DuckDBPyConnection, None, None]:
        """Context manager yielding a pooled connection."""
        conn = self.acquire()
        try:
            yield conn
        finally:
            self.release(conn)

    def close(self) -> None:
        """Close all connections and shut down pool."""
        with self._lock:
            self._closed = True
            while not self._pool.empty():
                try:
                    conn = self._pool.get_nowait()
                    conn.close()
                except Exception:
                    pass
            self._created_count = 0


class DuckDBWarehouseManager:
    """Thread-safe warehouse manager executing parameterized queries over DuckDB marts."""

    def __init__(
        self,
        database_path: str | Path,
        max_connections: int = 10,
        timeout: float = 5.0,
        gazetteer_dir: str | Path | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.pool = DuckDBConnectionPool(
            self.database_path,
            max_connections=max_connections,
            timeout=timeout,
            gazetteer_dir=gazetteer_dir,
        )
        self._search_sql = SEARCH_SQL_PATH.read_text(encoding="utf-8")
        self._detail_sql = DETAIL_SQL_PATH.read_text(encoding="utf-8")
        self._related_sql = RELATED_SQL_PATH.read_text(encoding="utf-8")
        self._cached_snapshot_meta: tuple[str, str] | None = None

    def close(self) -> None:
        """Close connection pool."""
        self.pool.close()

    def check_readiness(self) -> ReadinessStatus:
        """Execute readiness diagnostic verifying database access, schema, and freshness."""
        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.database_path.exists():
            raise WarehouseNotReadyError(
                f"Warehouse database file does not exist at {self.database_path}"
            )

        try:
            with self.pool.connection() as conn:
                # 1. Verify all required schema tables exist
                tables_res = conn.execute(
                    "SELECT DISTINCT lower(table_name) FROM information_schema.tables;"
                ).fetchall()
                found_tables = {row[0] for row in tables_res}
                missing_tables = [t for t in REQUIRED_TABLES if t not in found_tables]
                if missing_tables:
                    raise WarehouseNotReadyError(
                        f"Warehouse missing required tables: {', '.join(sorted(missing_tables))}"
                    )

                # 2. Extract snapshot metadata and trials count from canonical registry grain
                meta_query = """
                SELECT
                    COALESCE(st.snapshot_id, st.ingestion_run_id) AS snapshot_id,
                    CAST(
                        COALESCE(st.manifest_snapshot_date, st.snapshot_date, dt.max_date)
                        AS VARCHAR
                    ) AS snapshot_date,
                    strftime(
                        timezone('UTC', COALESCE(st.started_at_utc, st.snapshot_timestamp_utc)),
                        '%Y-%m-%dT%H:%M:%SZ'
                    ) AS snapshot_timestamp_utc,
                    dt.trial_count
                FROM (
                    SELECT
                        COUNT(DISTINCT nct_id) AS trial_count,
                        MAX(latest_seen_snapshot_date) AS max_date
                    FROM dim_registry_trial
                ) dt
                CROSS JOIN (
                    SELECT
                        st.snapshot_id,
                        st.ingestion_run_id,
                        st.snapshot_date,
                        sm.snapshot_date AS manifest_snapshot_date,
                        st.snapshot_timestamp_utc,
                        sm.started_at_utc
                    FROM stg_registry_trials st
                    LEFT JOIN stg_registry_trial_snapshots sm
                        ON COALESCE(st.snapshot_id, st.ingestion_run_id) = sm.ingestion_run_id
                    WHERE st.snapshot_timestamp_utc IS NOT NULL
                      AND (
                          NOT EXISTS (SELECT 1 FROM stg_registry_trial_snapshots)
                          OR (
                              sm.ingestion_run_id IS NOT NULL
                              AND sm.status = 'success'
                              AND sm.profile IN ('full_catalog', 'full-catalog', 'default')
                              AND (sm.condition IS NULL OR trim(sm.condition) = '')
                          )
                      )
                    ORDER BY
                        COALESCE(sm.started_at_utc, st.snapshot_timestamp_utc) DESC,
                        st.snapshot_timestamp_utc DESC,
                        COALESCE(st.snapshot_id, st.ingestion_run_id) DESC
                    LIMIT 1
                ) st;
                """
                row = conn.execute(meta_query).fetchone()
                if row is None or not row[0]:
                    raise WarehouseNotReadyError("No snapshot metadata found in warehouse tables")

                snap_id, snap_date, snap_ts, count = row
                if isinstance(snap_ts, datetime):
                    if snap_ts.tzinfo is None:
                        snap_ts = snap_ts.replace(tzinfo=UTC)
                    snap_ts_str = snap_ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    snap_ts_str = str(snap_ts)

                self._cached_snapshot_meta = (str(snap_id), snap_ts_str)
                return ReadinessStatus(
                    status="ready",
                    backend="warehouse",
                    database_accessible=True,
                    snapshot_id=str(snap_id),
                    snapshot_date=str(snap_date),
                    snapshot_timestamp_utc=snap_ts_str,
                    trials_count=int(count),
                    timestamp=now_iso,
                )
        except WarehouseNotReadyError:
            raise
        except Exception as err:
            logger.error("Warehouse readiness check failed", exc_info=err)
            raise WarehouseNotReadyError(f"Warehouse check failed: {err}") from err

    def get_snapshot_metadata(self) -> tuple[str, str]:
        """Retrieve snapshot identifier and timestamp for response headers."""
        if self._cached_snapshot_meta is not None:
            return self._cached_snapshot_meta

        status = self.check_readiness()
        if not status.snapshot_id or not status.snapshot_timestamp_utc:
            raise WarehouseNotReadyError("Warehouse snapshot metadata is incomplete")
        return (status.snapshot_id, status.snapshot_timestamp_utc)

    def search_trials(
        self,
        cond: str | None = None,
        status: str | None = None,
        phase: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        radius_mi: float = 50.0,
        min_age: int | None = None,
        max_age: int | None = None,
        sex: str | None = None,
        healthy_volunteers: bool | None = None,
        std_age_group: str | None = None,
        intervention_type: str | None = None,
        sponsor_class: str | None = None,
        updated_since: date | None = None,
        cursor: str | None = None,
        limit: int = 20,
        sort: str = "-updated",
    ) -> TrialsListResponse:
        """Execute parameterized trial search over DuckDB marts."""
        # Keyset cursor unpacking
        has_cursor = False
        cursor_val: str | None = None
        cursor_dist: float | None = None
        cursor_id: str | None = None

        if cursor:
            cursor_data = decode_keyset_cursor(cursor)
            cursor_sort = cursor_data.get("sort")
            if cursor_sort != sort:
                raise ValueError(
                    f"Cursor sort order '{cursor_sort}' does not match request sort '{sort}'"
                )
            has_cursor = True
            cursor_val = cursor_data.get("v")
            if cursor_val is not None and not isinstance(cursor_val, str):
                raise ValueError("Cursor value 'v' must be a string or null")

            raw_dist = cursor_data.get("dist")
            if raw_dist is not None:
                try:
                    cursor_dist = float(raw_dist)
                except (ValueError, TypeError) as err:
                    raise ValueError(f"Cursor distance must be a valid float: {err}") from err
            else:
                cursor_dist = None

            cursor_id_val = cursor_data.get("id")
            if cursor_id_val is not None and not isinstance(cursor_id_val, str):
                raise ValueError("Cursor 'id' must be a string")
            cursor_id = cursor_id_val

        params: dict[str, Any] = {
            "cond": _escape_like(cond.strip()) if cond else None,
            "status": status.strip() if status else None,
            "phase": phase.strip() if phase else None,
            "min_age": float(min_age) if min_age is not None else None,
            "max_age": float(max_age) if max_age is not None else None,
            "sex": sex.strip() if sex else None,
            "healthy_volunteers": healthy_volunteers,
            "std_age_group": std_age_group.strip() if std_age_group else None,
            "intervention_type": intervention_type.strip() if intervention_type else None,
            "sponsor_class": sponsor_class.strip() if sponsor_class else None,
            "updated_since": updated_since.isoformat() if updated_since else None,
            "lat": lat,
            "lon": lon,
            "radius_mi": float(radius_mi),
            "sort": sort,
            "has_cursor": has_cursor,
            "cursor_val": cursor_val,
            "cursor_dist": float(cursor_dist) if cursor_dist is not None else None,
            "cursor_id": cursor_id,
            "limit_plus_one": limit + 1,
        }

        with self.pool.connection() as conn:
            cursor_res = conn.execute(self._search_sql, params)
            col_names = [desc[0] for desc in cursor_res.description]
            raw_rows = cursor_res.fetchall()

        records = [dict(zip(col_names, row, strict=True)) for row in raw_rows]
        has_more = len(records) > limit
        page_records = records[:limit]

        total_count: int | None = None
        if records:
            total_count = records[0].get("total_unpaged_count")
        else:
            total_count = 0

        items: list[TrialSummary] = []
        for r in page_records:
            elig = StructuredEligibilitySummary(
                minimum_age_years=r.get("min_age_years"),
                maximum_age_years=r.get("max_age_years"),
                age_not_stated=bool(r.get("age_not_stated")),
                sex=r.get("sex", "ALL"),
                accepts_healthy_volunteers=r.get("accepts_healthy_volunteers"),
                std_age_groups=r.get("std_age_groups") or [],
            )

            nearest_site: NearestSiteSummary | None = None
            if r.get("nearest_facility_name"):
                nearest_site = NearestSiteSummary(
                    facility_name=r["nearest_facility_name"],
                    city=r["nearest_city"],
                    state=r.get("nearest_state"),
                    location_status=r.get("nearest_location_status"),
                    coordinate_approximate=bool(r.get("nearest_coordinate_approximate")),
                    distance_miles=r.get("nearest_distance_miles"),
                )

            items.append(
                TrialSummary(
                    nct_id=r["nct_id"],
                    brief_title=r["brief_title"],
                    overall_status=r["overall_status"],
                    phase=r["phase"],
                    study_type=r["study_type"],
                    lead_sponsor=r["lead_sponsor"],
                    sponsor_class=r.get("sponsor_class"),
                    start_date=r.get("start_date"),
                    last_update_post_date=r.get("last_update_post_date"),
                    conditions=r.get("conditions") or [],
                    structured_eligibility=elig,
                    nearest_site=nearest_site,
                    registry_url=r.get("registry_url")
                    or f"https://clinicaltrials.gov/study/{r['nct_id']}",
                )
            )

        next_cursor: str | None = None
        if has_more and page_records:
            last_rec = page_records[-1]
            last_sort_val: str | None = None
            if sort in ("-updated", "updated"):
                last_sort_val = last_rec.get("last_update_post_date")
            elif sort in ("-start_date", "start_date"):
                last_sort_val = last_rec.get("start_date")

            next_cursor = encode_keyset_cursor(
                {
                    "sort": sort,
                    "v": last_sort_val,
                    "dist": last_rec.get("raw_distance_miles"),
                    "id": last_rec["nct_id"],
                }
            )

        return TrialsListResponse(
            items=items,
            pagination=PaginationMetadata(
                next_cursor=next_cursor,
                prev_cursor=None,
                limit=limit,
                total_count=total_count,
                has_more=has_more,
            ),
        )

    def get_trial_detail(
        self,
        nct_id: str,
        lat: float | None = None,
        lon: float | None = None,
    ) -> TrialDetail | None:
        """Fetch protocol details for a specific NCT ID from warehouse marts."""
        canonical_nct = nct_id.strip().upper()
        params = {
            "nct_id": canonical_nct,
            "lat": lat,
            "lon": lon,
        }

        with self.pool.connection() as conn:
            cursor_res = conn.execute(self._detail_sql, params)
            col_names = [desc[0] for desc in cursor_res.description]
            row = cursor_res.fetchone()

        if row is None:
            return None

        r = dict(zip(col_names, row, strict=True))

        # Study design
        phase_raw = r.get("phase_raw")
        phase_normalized = r.get("phase_normalized") or "NOT_APPLICABLE"
        phases = (
            [p.strip() for p in phase_raw.split("/") if p.strip()]
            if phase_raw
            else [phase_normalized]
        )

        study_design = StudyDesign(
            phases=phases,
            phase_raw=phase_raw,
            phase_normalized=phase_normalized,
            allocation=r.get("allocation"),
            primary_purpose=r.get("primary_purpose"),
            intervention_model=r.get("intervention_model"),
            masking=r.get("masking"),
        )

        enrollment = EnrollmentInfo(
            count=r.get("enrollment_count"),
            type=r.get("enrollment_type"),
        )

        dates = StudyDates(
            start_date=r.get("start_date"),
            primary_completion_date=r.get("primary_completion_date"),
            completion_date=r.get("completion_date"),
            study_first_post_date=r.get("study_first_post_date"),
            results_first_post_date=r.get("results_first_post_date"),
            last_update_post_date=r.get("last_update_post_date"),
        )

        # Sponsors
        lead_sponsor = SponsorDetail(
            name=r.get("lead_sponsor_name") or "Unknown Sponsor",
            sponsor_class=r.get("lead_sponsor_class"),
        )
        collaborators: list[SponsorDetail] = []
        raw_collaborators = r.get("collaborators") or []
        for c in raw_collaborators:
            if isinstance(c, dict) and c.get("name"):
                collaborators.append(SponsorDetail(name=c["name"], sponsor_class=c.get("class")))

        sponsors = StudySponsors(
            lead_sponsor=lead_sponsor,
            collaborators=collaborators,
            responsible_party_type=r.get("responsible_party_type"),
        )

        # Conditions
        conditions: list[StudyCondition] = []
        raw_conditions = r.get("conditions") or []
        for cond in raw_conditions:
            if isinstance(cond, dict) and cond.get("condition_raw"):
                conditions.append(
                    StudyCondition(
                        condition_raw=cond["condition_raw"],
                        condition_normalized=cond.get("condition_normalized")
                        or cond["condition_raw"].lower(),
                        condition_group=cond.get("condition_group"),
                        mesh_id=cond.get("mesh_id"),
                        dementia_relevance_flag=cond.get("dementia_relevance_flag"),
                    )
                )

        # Interventions
        interventions: list[StudyIntervention] = []
        raw_interventions = r.get("interventions") or []
        for item in raw_interventions:
            if isinstance(item, dict) and item.get("intervention_name"):
                interventions.append(
                    StudyIntervention(
                        intervention_type=item.get("intervention_type") or "UNKNOWN",
                        intervention_name=item["intervention_name"],
                        description=item.get("description"),
                    )
                )

        # Arm groups (registry text passed through verbatim)
        arms: list[StudyArm] = []
        raw_arms = r.get("arms") or []
        for arm in raw_arms:
            if not isinstance(arm, dict):
                continue
            arms.append(
                StudyArm(
                    label=arm.get("label"),
                    arm_type=arm.get("arm_type"),
                    description=arm.get("description"),
                    intervention_names=arm.get("intervention_names") or [],
                )
            )

        # Eligibility (Issue #62)
        criteria_text = r.get("criteria_text")
        sections_parsed = split_eligibility_sections(criteria_text)
        eligibility = StructuredEligibility(
            minimum_age_raw=r.get("minimum_age_raw"),
            maximum_age_raw=r.get("maximum_age_raw"),
            minimum_age_years=r.get("minimum_age_years"),
            maximum_age_years=r.get("maximum_age_years"),
            age_not_stated=bool(r.get("age_not_stated")),
            sex=r.get("sex", "ALL"),
            gender_based=r.get("gender_based"),
            gender_description=r.get("gender_description"),
            accepts_healthy_volunteers=r.get("accepts_healthy_volunteers"),
            std_age_groups=r.get("std_age_groups") or [],
            criteria_text=criteria_text,
            eligibility_sections=EligibilitySections(
                parsed=sections_parsed.parsed,
                sections=[
                    EligibilitySection(kind=s.kind, heading=s.heading_raw, items=s.items)
                    for s in sections_parsed.sections
                ],
                inclusion=sections_parsed.inclusion,
                exclusion=sections_parsed.exclusion,
                unclassified=sections_parsed.unclassified,
            )
            if sections_parsed is not None
            else None,
        )

        # Sites (ADR 0065 strict compliance: strictly zero investigator contact PII)
        sites: list[TrialSite] = []
        raw_sites = r.get("sites") or []
        for site in raw_sites:
            if not isinstance(site, dict):
                continue
            coords: GeoPoint | None = None
            if site.get("latitude") is not None and site.get("longitude") is not None:
                try:
                    coords = GeoPoint(lat=float(site["latitude"]), lon=float(site["longitude"]))
                except (ValueError, TypeError):
                    coords = None

            sites.append(
                TrialSite(
                    facility_name=site.get("facility_name") or "Unknown Facility",
                    city=site.get("city") or "Unknown City",
                    state=site.get("state"),
                    zip_code=site.get("zip_code"),
                    country=site.get("country") or "United States",
                    location_status=site.get("location_status"),
                    coordinates=coords,
                    coordinate_approximate=bool(site.get("coordinate_approximate")),
                    distance_miles=site.get("distance_miles"),
                )
            )

        # Provenance
        provenance = DataProvenance(
            ingestion_run_id=r.get("ingestion_run_id") or "warehouse",
            snapshot_id=r.get("snapshot_id") or "warehouse",
            snapshot_date=r.get("snapshot_date") or date.today().isoformat(),
            snapshot_timestamp_utc=r.get("snapshot_timestamp_utc")
            or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            source_json_hash=r.get("source_json_hash") or "",
            record_quality_flag=r.get("record_quality_flag") or "ok",
            retrieved_at_utc=r.get("retrieved_at_utc"),
            indication_profile_id=r.get("indication_profile_id") or "warehouse",
        )

        return TrialDetail(
            nct_id=canonical_nct,
            brief_title=r["brief_title"],
            official_title=r.get("official_title"),
            overall_status=r["overall_status"],
            last_known_status=r.get("last_known_status"),
            status_verified_date=r.get("status_verified_date"),
            study_type=r["study_type"],
            has_expanded_access=bool(r.get("has_expanded_access")),
            study_design=study_design,
            enrollment=enrollment,
            dates=dates,
            sponsors=sponsors,
            conditions=conditions,
            interventions=interventions,
            arms=arms,
            has_placebo_arm=r.get("has_placebo_arm"),
            why_stopped=r.get("why_stopped"),
            brief_summary=r.get("brief_summary"),
            primary_outcome_time_frame=r.get("primary_outcome_time_frame"),
            eligibility=eligibility,
            sites=sites,
            registry_url=f"https://clinicaltrials.gov/study/{canonical_nct}",
            registry_contact_url=f"https://clinicaltrials.gov/study/{canonical_nct}#contacts-locations",
            provenance=provenance,
        )

    def get_related_trials(
        self,
        nct_id: str,
        limit: int = 10,
        min_score: float | None = None,
    ) -> RelatedTrialsResponse | None:
        """Fetch ranked comparable clinical trials for target NCT ID.

        Executes on-demand MeSH-ancestor + phase candidate blocking
        and comparability scoring (Issue #55).
        """
        canonical_nct = nct_id.strip().upper()
        with self.pool.connection() as conn:
            reg_row = conn.execute("SELECT EXISTS (SELECT 1 FROM dim_registry_trial);").fetchone()
            has_registry_rows = bool(reg_row[0]) if reg_row else False

            use_legacy = False
            if not has_registry_rows:
                has_legacy_table = bool(
                    conn.execute(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE lower(table_name) = 'dim_trial' LIMIT 1;"
                    ).fetchone()
                )
                if has_legacy_table:
                    legacy_row = conn.execute("SELECT EXISTS (SELECT 1 FROM dim_trial);").fetchone()
                    use_legacy = bool(legacy_row[0]) if legacy_row else False

            if use_legacy:
                exists_dim = conn.execute(
                    "SELECT 1 FROM dim_trial WHERE upper(nct_id) = ?", [canonical_nct]
                ).fetchone()
                if exists_dim is None:
                    exists_stg = conn.execute(
                        "SELECT 1 FROM stg_trials WHERE upper(nct_id) = ?", [canonical_nct]
                    ).fetchone()
                    if exists_stg is None:
                        return None
                related_sql = _LEGACY_RELATED_SQL
            else:
                exists_dim = conn.execute(
                    "SELECT 1 FROM dim_registry_trial WHERE upper(nct_id) = ?", [canonical_nct]
                ).fetchone()
                if exists_dim is None:
                    exists_stg = conn.execute(
                        "SELECT 1 FROM stg_registry_trials WHERE upper(nct_id) = ?",
                        [canonical_nct],
                    ).fetchone()
                    if exists_stg is None:
                        return None
                related_sql = self._related_sql

            cursor = conn.execute(
                related_sql,
                {
                    "nct_id": canonical_nct,
                    "min_score": min_score,
                    "limit": limit,
                },
            )
            cols = [d[0] for d in cursor.description]
            rows = cursor.fetchall()

        items: list[RelatedTrialItem] = []
        for row in rows:
            r = dict(zip(cols, row, strict=True))
            factors = ComparabilityFactors(
                same_condition=FactorScore(
                    matched=bool(r["same_condition"]),
                    weight=float(r["weight_same_condition"]),
                    weighted_score=float(r["weighted_same_condition"]),
                ),
                same_phase=FactorScore(
                    matched=bool(r["same_phase"]),
                    weight=float(r["weight_same_phase"]),
                    weighted_score=float(r["weighted_same_phase"]),
                ),
                geography_overlap=FactorScore(
                    matched=bool(r["geography_overlap"]),
                    weight=float(r["weight_geography_overlap"]),
                    weighted_score=float(r["weighted_geography_overlap"]),
                ),
                intervention_type_overlap=FactorScore(
                    matched=bool(r["intervention_type_overlap"]),
                    weight=float(r["weight_intervention_type_overlap"]),
                    weighted_score=float(r["weighted_intervention_type_overlap"]),
                ),
                study_design_match=FactorScore(
                    matched=bool(r["study_design_match"]),
                    weight=float(r["weight_study_design_match"]),
                    weighted_score=float(r["weighted_study_design_match"]),
                ),
                eligibility_compatible=FactorScore(
                    matched=bool(r["eligibility_compatible"]),
                    weight=float(r["weight_eligibility_compatible"]),
                    weighted_score=float(r["weighted_eligibility_compatible"]),
                ),
                enrollment_band_match=FactorScore(
                    matched=bool(r["enrollment_band_match"]),
                    weight=float(r["weight_enrollment_band_match"]),
                    weighted_score=float(r["weighted_enrollment_band_match"]),
                ),
                explanation_summary=r["similarity_explanation"] or "",
            )
            items.append(
                RelatedTrialItem(
                    nct_id=r["nct_id"],
                    brief_title=r["brief_title"] or "",
                    overall_status=r["overall_status"] or "",
                    phase=r["phase"] or "",
                    lead_sponsor=r["lead_sponsor"] or "",
                    similarity_score=float(r["similarity_score"]),
                    similarity_rank=int(r["similarity_rank"]),
                    comparability_factors=factors,
                    registry_url=r["registry_url"]
                    or f"https://clinicaltrials.gov/study/{r['nct_id']}",
                )
            )

        return RelatedTrialsResponse(
            target_nct_id=canonical_nct,
            related_trials=items,
        )
