"""DuckDB read-only warehouse connection manager and query adapter for the read API.

Conforms strictly to ADR 0065 (contact privacy guardrails: zero coordinator/investigator PII)
and Issue #59 architecture specifications.
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
    DataProvenance,
    EnrollmentInfo,
    GeoPoint,
    NearestSiteSummary,
    PaginationMetadata,
    ReadinessStatus,
    SponsorDetail,
    StructuredEligibility,
    StructuredEligibilitySummary,
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

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parent / "sql"
SEARCH_SQL_PATH = SQL_DIR / "search.sql"
DETAIL_SQL_PATH = SQL_DIR / "detail.sql"

REQUIRED_TABLES = (
    "dim_trial",
    "fct_trial_snapshot",
    "fct_trial_site",
    "stg_trials",
    "stg_trial_locations",
    "stg_trial_sponsors",
    "stg_trial_conditions",
    "stg_trial_interventions",
)


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
            candidates = ["main_marts", "main_staging", "marts", "staging", "main"]
            valid = [s for s in candidates if s in schemas]
            if valid:
                path_str = ",".join(valid)
                conn.execute(f"SET search_path = '{path_str}';")
            # Offline Gazetteer fallback coordinates (Issue #63): always present
            # (empty when reference files are missing) so queries stay bindable.
            register_gazetteer_views(conn, self.gazetteer_dir)
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

                # 2. Extract snapshot metadata and trials count strictly from real schema
                meta_query = """
                SELECT
                    st.snapshot_id,
                    CAST(COALESCE(st.snapshot_date, dt.max_date) AS VARCHAR) AS snapshot_date,
                    strftime(
                        timezone('UTC', st.snapshot_timestamp_utc),
                        '%Y-%m-%dT%H:%M:%SZ'
                    ) AS snapshot_timestamp_utc,
                    dt.trial_count
                FROM (
                    SELECT
                        COUNT(DISTINCT nct_id) AS trial_count,
                        MAX(latest_seen_snapshot_date) AS max_date
                    FROM dim_trial
                ) dt
                CROSS JOIN (
                    SELECT
                        snapshot_id,
                        snapshot_date,
                        snapshot_timestamp_utc
                    FROM stg_trials
                    WHERE snapshot_timestamp_utc IS NOT NULL
                    ORDER BY snapshot_timestamp_utc DESC, ingestion_run_id DESC
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
            "cond": cond.strip() if cond else None,
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

        # Eligibility (Issue #62)
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
            criteria_text=r.get("criteria_text"),
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
            eligibility=eligibility,
            sites=sites,
            registry_url=f"https://clinicaltrials.gov/study/{canonical_nct}",
            registry_contact_url=f"https://clinicaltrials.gov/study/{canonical_nct}#contacts-locations",
            provenance=provenance,
        )
