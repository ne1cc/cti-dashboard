"""Clinical Trial Read API service with privacy-safe caching, DuckDB, and RFC 7807 errors."""

import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Any, Literal

import requests
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from src.api.cache import (
    CacheEntry,
    CacheState,
    CircuitBreaker,
    CircuitState,
    SQLiteCache,
    UpstreamUnavailableError,
    canonical_cache_key,
    scrub_contact_pii,
)
from src.api.live import (
    build_upstream_search_params,
    filter_and_sort_summaries,
    normalize_study_detail,
    normalize_study_summary,
)
from src.api.models import (
    InvalidParam,
    PaginationMetadata,
    ProblemDetail,
    ReadinessStatus,
    TrialDetail,
    TrialsListResponse,
    TrialSummary,
)
from src.api.warehouse import (
    DuckDBWarehouseManager,
    WarehouseNotReadyError,
)
from src.config import ApiConfig, HttpConfig
from src.ingest.ctg_client import CTGClient

logger = logging.getLogger(__name__)
NCT_REGEX = re.compile(r"^NCT\d{8}$", re.IGNORECASE)
StatusFilter = Literal[
    "RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "ENROLLING_BY_INVITATION",
    "NOT_YET_RECRUITING",
    "COMPLETED",
    "SUSPENDED",
    "TERMINATED",
    "WITHDRAWN",
]
PhaseFilter = Literal["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NOT_APPLICABLE"]
SexFilter = Literal["ALL", "FEMALE", "MALE"]
AgeGroupFilter = Literal["CHILD", "ADULT", "OLDER_ADULT"]
SponsorFilter = Literal["INDUSTRY", "NIH", "FED", "OTHER", "OTHER_GOV", "INDIV", "NETWORK"]
SortOrder = Literal["distance", "updated", "-updated", "start_date", "-start_date"]


def create_problem_response(
    status_code: int,
    title: str,
    detail: str,
    instance: str,
    error_type: str | None = None,
    code: str | None = None,
    invalid_params: list[InvalidParam] | None = None,
) -> JSONResponse:
    """Generate RFC 7807 problem details without exception diagnostics."""
    types = {
        400: "invalid-parameters",
        404: "not-found",
        422: "validation-error",
        429: "rate-limit-exceeded",
        500: "internal-error",
        503: "service-unavailable",
    }
    problem = ProblemDetail(
        type=error_type
        or f"https://api.cti.example.org/errors/{types.get(status_code, 'general-error')}",
        title=title,
        status=status_code,
        detail=detail,
        instance=instance,
        code=code,
        invalid_params=invalid_params,
    )
    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


class APIContainer:
    """Shared interactive client, cache, circuit breaker, and warehouse dependencies."""

    def __init__(
        self,
        ctg_client: CTGClient | None = None,
        cache: SQLiteCache | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        cache_path: str | Path | None = None,
        warehouse: DuckDBWarehouseManager | None = None,
        warehouse_path: str | Path | None = None,
    ) -> None:
        self.ctg_client = ctg_client or CTGClient(
            ApiConfig(
                base_url="https://clinicaltrials.gov/api/v2",
                http=HttpConfig(timeout_seconds=5.0, max_retries=2),
            )
        )
        self.cache = cache or SQLiteCache(
            cache_path
            if cache_path is not None
            else os.environ.get("CTI_CACHE_PATH", ".api_cache.db")
        )
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

        # Warehouse DuckDB configuration
        resolved_wh_path = (
            warehouse_path if warehouse_path is not None else os.environ.get("CTI_WAREHOUSE_PATH")
        )
        if warehouse is not None:
            self.warehouse: DuckDBWarehouseManager | None = warehouse
        elif resolved_wh_path is not None:
            self.warehouse = DuckDBWarehouseManager(resolved_wh_path)
        else:
            self.warehouse = None


def _sanitized_payload(data: dict[str, Any]) -> tuple[str, str]:
    payload = json.dumps(scrub_contact_pii(data), sort_keys=True, separators=(",", ":"))
    etag = f'W/"{hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]}"'
    return payload, etag


def create_app(
    container: APIContainer | None = None,
    cache_path: str | Path | None = None,
    warehouse: DuckDBWarehouseManager | None = None,
    warehouse_path: str | Path | None = None,
) -> FastAPI:
    """Create an app explicitly; importing this module never opens a cache or client."""
    if container is None:
        state_container = APIContainer(
            cache_path=cache_path,
            warehouse=warehouse,
            warehouse_path=warehouse_path,
        )
    else:
        state_container = container

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI) -> AsyncGenerator[None, None]:
        yield
        state_container.cache.close()
        state_container.ctg_client.close()
        if state_container.warehouse is not None:
            state_container.warehouse.close()

    app = FastAPI(
        title="Clinical Trial Intelligence Read API",
        version="0.1.0",
        lifespan=lifespan,
        responses={code: {"model": ProblemDetail} for code in (400, 404, 405, 422, 500, 503)},
    )
    app.state.container = state_container

    # CORS configuration
    cors_origins = [
        o.strip() for o in os.environ.get("CTI_CORS_ORIGINS", "*").split(",") if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "Data-Snapshot",
            "Data-Updated",
            "ETag",
            "Cache-Control",
            "X-Request-ID",
            "X-Cache-Status",
        ],
    )

    # Request ID and Structured Logging Middleware
    @app.middleware("http")
    async def request_correlation_and_logging_middleware(
        request: Request, call_next: Any
    ) -> Response:
        request_id = request.headers.get("X-Request-ID")
        if not request_id or not request_id.strip():
            request_id = str(uuid.uuid4())
        else:
            request_id = request_id.strip()

        request.state.request_id = request_id
        start_time = time.perf_counter()

        response: Response = await call_next(request)

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Request-ID"] = request_id

        log_data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        }
        logger.info(json.dumps(log_data))
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        coordinates = any(err.get("loc", ())[-1:] in (("lat",), ("lon",)) for err in errors)
        return create_problem_response(
            400 if coordinates else 422,
            "Invalid Query Parameters",
            "Request validation failed on one or more parameters.",
            str(request.url),
            code="INVALID_COORDINATES" if coordinates else "VALIDATION_FAILED",
            invalid_params=[
                InvalidParam(
                    name=str(err["loc"][-1]),
                    reason=err.get("msg", "Invalid parameter"),
                    value=err.get("input"),
                )
                for err in errors
            ],
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("API HTTP error", exc_info=exc)
        result = create_problem_response(
            exc.status_code,
            HTTPStatus(exc.status_code).phrase,
            "An unexpected internal error occurred."
            if exc.status_code >= 500
            else exc.detail
            if isinstance(exc.detail, str)
            else "Request failed.",
            str(request.url),
        )
        if exc.headers:
            result.headers.update(exc.headers)
        return result

    @app.exception_handler(requests.HTTPError)
    async def upstream_client_error_handler(
        request: Request, exc: requests.HTTPError
    ) -> JSONResponse:
        upstream_status = exc.response.status_code if exc.response is not None else 400
        status_code = upstream_status if upstream_status in (400, 404, 422) else 400
        return create_problem_response(
            status_code,
            HTTPStatus(status_code).phrase,
            f"Study {request.path_params.get('nct_id', 'identifier')} was not found."
            if status_code == 404
            else "The registry rejected the query or cursor.",
            str(request.url),
            code="STUDY_NOT_FOUND" if status_code == 404 else "UPSTREAM_INVALID_QUERY",
        )

    @app.exception_handler(UpstreamUnavailableError)
    async def unavailable_handler(request: Request, exc: UpstreamUnavailableError) -> JSONResponse:
        return create_problem_response(
            503,
            "Service Unavailable",
            "Upstream service is temporarily unavailable.",
            str(request.url),
            code="UPSTREAM_UNAVAILABLE",
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("API request failed", exc_info=exc)
        return create_problem_response(
            500,
            "Internal Server Error",
            "An unexpected internal error occurred.",
            str(request.url),
            code="INTERNAL_ERROR",
        )

    def get_container() -> APIContainer:
        return state_container

    ContainerDep = Annotated[APIContainer, Depends(get_container)]

    def set_live_response_headers(response: Response, entry: CacheEntry) -> None:
        response.headers["Data-Snapshot"] = "live-api"
        response.headers["Data-Updated"] = datetime.fromtimestamp(entry.created_at, UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        response.headers["ETag"] = entry.etag
        response.headers["Cache-Control"] = (
            "no-cache" if entry.state == CacheState.STALE else "public, max-age=300"
        )
        response.headers["X-Cache-Status"] = entry.state.value
        if entry.state == CacheState.STALE:
            response.headers["Warning"] = '110 "Response is Stale"'

    @app.get("/healthz", tags=["Diagnostics"])
    def liveness_probe() -> dict[str, Any]:
        """Process liveness probe."""
        return {
            "status": "ok",
            "timestamp": datetime.now(UTC).isoformat(),
        }

    @app.get("/readyz", tags=["Diagnostics"], response_model=ReadinessStatus)
    def readiness_probe(request: Request, deps: ContainerDep) -> Any:
        """Readiness probe checking warehouse accessibility, schemas, and freshness."""
        if deps.warehouse is not None:
            try:
                return deps.warehouse.check_readiness()
            except WarehouseNotReadyError as err:
                return create_problem_response(
                    503,
                    "Service Unavailable",
                    f"Warehouse is not ready: {err}",
                    str(request.url),
                    code="WAREHOUSE_NOT_READY",
                )
        else:
            metrics = deps.circuit_breaker.get_metrics()
            if metrics["state"] == CircuitState.OPEN.value:
                return create_problem_response(
                    503,
                    "Service Unavailable",
                    "Circuit breaker is OPEN.",
                    str(request.url),
                    code="CIRCUIT_BREAKER_OPEN",
                )
            now_iso = datetime.now(UTC).isoformat()
            return ReadinessStatus(
                status="ready",
                backend="live",
                database_accessible=True,
                snapshot_id="live-api",
                snapshot_date=date.today().isoformat(),
                snapshot_timestamp_utc=now_iso,
                trials_count=None,
                timestamp=now_iso,
            )

    @app.get("/health")
    @app.get("/v1/metadata/status")
    def health_check(deps: ContainerDep) -> dict[str, Any]:
        metrics = deps.circuit_breaker.get_metrics()
        status = "ok" if metrics["state"] == CircuitState.CLOSED.value else "degraded"
        if deps.warehouse is not None:
            try:
                wh_status = deps.warehouse.check_readiness()
                status = wh_status.status
            except Exception:
                status = "degraded"

        return {
            "status": status,
            "cache": deps.cache.get_metrics(),
            "circuit_breaker": metrics,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    @app.get("/v1/trials", response_model=TrialsListResponse)
    def search_trials(
        request: Request,
        response: Response,
        deps: ContainerDep,
        cond: str | None = Query(default=None),
        status_filter: Annotated[StatusFilter | None, Query(alias="status")] = None,
        phase: Annotated[PhaseFilter | None, Query()] = None,
        lat: float | None = Query(default=None, ge=-90, le=90, allow_inf_nan=False),
        lon: float | None = Query(default=None, ge=-180, le=180, allow_inf_nan=False),
        radius_mi: float = Query(default=50.0, ge=1, le=500, allow_inf_nan=False),
        min_age: int | None = Query(default=None, ge=0, le=130),
        max_age: int | None = Query(default=None, ge=0, le=130),
        sex: Annotated[SexFilter | None, Query()] = None,
        healthy_volunteers: bool | None = Query(default=None),
        std_age_group: Annotated[AgeGroupFilter | None, Query()] = None,
        intervention_type: str | None = Query(default=None),
        sponsor_class: Annotated[SponsorFilter | None, Query()] = None,
        updated_since: Annotated[date | None, Query()] = None,
        cursor: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        sort: Annotated[SortOrder, Query()] = "-updated",
    ) -> Any:
        if (lat is None) != (lon is None):
            return create_problem_response(
                400,
                "Invalid Query Parameters",
                "Supply both lat and lon.",
                str(request.url),
                code="INVALID_COORDINATES",
            )
        if min_age is not None and max_age is not None and min_age > max_age:
            return create_problem_response(
                400,
                "Invalid Query Parameters",
                "min_age cannot exceed max_age.",
                str(request.url),
                code="INVALID_AGE_RANGE",
            )
        if sort == "distance" and lat is None:
            return create_problem_response(
                422,
                "Unprocessable Query Combination",
                "Distance sort requires lat and lon.",
                str(request.url),
                code="MISSING_COORDINATES_FOR_SORT",
                invalid_params=[
                    InvalidParam(name="sort", reason="Requires coordinates", value=sort)
                ],
            )

        # Warehouse backend path
        if deps.warehouse is not None:
            try:
                result = deps.warehouse.search_trials(
                    cond=cond,
                    status=status_filter,
                    phase=phase,
                    lat=lat,
                    lon=lon,
                    radius_mi=radius_mi,
                    min_age=min_age,
                    max_age=max_age,
                    sex=sex,
                    healthy_volunteers=healthy_volunteers,
                    std_age_group=std_age_group,
                    intervention_type=intervention_type,
                    sponsor_class=sponsor_class,
                    updated_since=updated_since,
                    cursor=cursor,
                    limit=limit,
                    sort=sort,
                )
            except ValueError as val_err:
                return create_problem_response(
                    400,
                    "Invalid Query Parameters",
                    str(val_err),
                    str(request.url),
                    code="INVALID_CURSOR",
                )
            except Exception as err:
                logger.error("Warehouse search failed", exc_info=err)
                return create_problem_response(
                    500,
                    "Internal Server Error",
                    "Warehouse query failed.",
                    str(request.url),
                    code="WAREHOUSE_QUERY_ERROR",
                )

            snap_id, snap_ts = deps.warehouse.get_snapshot_metadata()
            payload_str = json.dumps(result.model_dump(), sort_keys=True, separators=(",", ":"))
            etag = f'W/"{hashlib.sha256(payload_str.encode("utf-8")).hexdigest()[:16]}"'

            response.headers["Data-Snapshot"] = snap_id
            response.headers["Data-Updated"] = snap_ts
            response.headers["ETag"] = etag
            response.headers["Cache-Control"] = "public, max-age=300"

            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=dict(response.headers))

            return result

        # Live API backend path
        unsupported = [
            name
            for name, value in (
                ("intervention_type", intervention_type),
                ("updated_since", updated_since),
            )
            if value is not None
        ]
        if unsupported:
            return create_problem_response(
                422,
                "Unsupported Filter",
                "These filters are not supported by the live backend.",
                str(request.url),
                code="UNSUPPORTED_FILTER",
                invalid_params=[
                    InvalidParam(name=name, reason="Unsupported filter") for name in unsupported
                ],
            )
        upstream_params = build_upstream_search_params(
            cond=cond,
            status=status_filter,
            lat=lat,
            lon=lon,
            radius_mi=radius_mi,
            cursor=cursor,
            limit=limit,
        )
        cache_key = canonical_cache_key("trials_search", upstream_params)

        def fetch_upstream() -> tuple[str, str]:
            data, _ = deps.ctg_client.fetch_studies(upstream_params)
            if not isinstance(data, dict) or not isinstance(data.get("studies", []), list):
                raise ValueError("Malformed upstream search response")
            return _sanitized_payload(data)

        entry = deps.cache.stale_if_error(cache_key, fetch_upstream, deps.circuit_breaker)
        data = json.loads(entry.payload)
        summaries: list[TrialSummary] = []
        raw_studies = data.get("studies", [])
        for study in raw_studies:
            try:
                summaries.append(normalize_study_summary(study, user_lat=lat, user_lon=lon))
            except (ValueError, TypeError, AttributeError, KeyError):
                logger.warning("Skipping malformed upstream study", exc_info=True)
        items = filter_and_sort_summaries(
            summaries,
            min_age=min_age,
            max_age=max_age,
            sex=sex,
            healthy_volunteers=healthy_volunteers,
            phase=phase,
            std_age_group=std_age_group,
            sponsor_class=sponsor_class,
            sort=sort,
        )
        post_filtered = any(
            value is not None
            for value in (
                min_age,
                max_age,
                sex,
                healthy_volunteers,
                phase,
                std_age_group,
                sponsor_class,
            )
        ) or len(summaries) != len(raw_studies)
        set_live_response_headers(response, entry)
        return TrialsListResponse(
            items=items,
            pagination=PaginationMetadata(
                next_cursor=data.get("nextPageToken"),
                prev_cursor=None,
                limit=limit,
                total_count=None if post_filtered else data.get("totalCount"),
                has_more=bool(data.get("nextPageToken")),
            ),
        )

    @app.get("/v1/trials/{nct_id}", response_model=TrialDetail)
    def get_trial_detail(
        nct_id: str,
        request: Request,
        response: Response,
        deps: ContainerDep,
        lat: float | None = Query(default=None, ge=-90, le=90, allow_inf_nan=False),
        lon: float | None = Query(default=None, ge=-180, le=180, allow_inf_nan=False),
    ) -> Any:
        canonical_nct = nct_id.strip().upper()
        if not NCT_REGEX.fullmatch(canonical_nct):
            return create_problem_response(
                400,
                "Invalid NCT ID",
                "Expected an NCT identifier followed by eight digits.",
                str(request.url),
                code="INVALID_NCT_ID",
            )
        if (lat is None) != (lon is None):
            return create_problem_response(
                400,
                "Invalid Query Parameters",
                "Supply both lat and lon.",
                str(request.url),
                code="INVALID_COORDINATES",
            )

        # Warehouse backend path
        if deps.warehouse is not None:
            try:
                detail = deps.warehouse.get_trial_detail(canonical_nct, lat=lat, lon=lon)
            except Exception as err:
                logger.error("Warehouse detail query failed", exc_info=err)
                return create_problem_response(
                    500,
                    "Internal Server Error",
                    "Warehouse detail query failed.",
                    str(request.url),
                    code="WAREHOUSE_QUERY_ERROR",
                )

            if detail is None:
                return create_problem_response(
                    404,
                    "Not Found",
                    f"Study {canonical_nct} was not found in the warehouse.",
                    str(request.url),
                    code="STUDY_NOT_FOUND",
                )

            snap_id, snap_ts = deps.warehouse.get_snapshot_metadata()
            payload_str = json.dumps(detail.model_dump(), sort_keys=True, separators=(",", ":"))
            etag = f'W/"{hashlib.sha256(payload_str.encode("utf-8")).hexdigest()[:16]}"'

            response.headers["Data-Snapshot"] = snap_id
            response.headers["Data-Updated"] = snap_ts
            response.headers["ETag"] = etag
            response.headers["Cache-Control"] = "public, max-age=300"

            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=dict(response.headers))

            return detail

        # Live API backend path
        cache_key = canonical_cache_key("trial_detail", {"nct_id": canonical_nct})

        def fetch_upstream() -> tuple[str, str]:
            study, _ = deps.ctg_client.fetch_study(canonical_nct)
            return _sanitized_payload(study)

        entry = deps.cache.stale_if_error(cache_key, fetch_upstream, deps.circuit_breaker)
        data = json.loads(entry.payload)
        try:
            detail = normalize_study_detail(
                data.get("study", data), user_lat=lat, user_lon=lon, retrieved_at=entry.created_at
            )
        except (ValueError, TypeError, AttributeError, KeyError):
            logger.warning("Malformed upstream study detail", exc_info=True)
            return create_problem_response(
                503,
                "Service Unavailable",
                "The registry study could not be normalized.",
                str(request.url),
                code="UPSTREAM_INVALID_STUDY",
            )
        set_live_response_headers(response, entry)
        return detail

    return app
