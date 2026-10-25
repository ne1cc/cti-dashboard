"""FastAPI application for the Clinical Trial Read API.

Serves live search and trial protocol detail endpoints, adhering to
docs/api/openapi.yaml, ADR 0065 privacy guardrails, and RFC 7807 problem details.
"""

import hashlib
import json
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.api.cache import (
    CacheState,
    CircuitBreaker,
    CircuitState,
    SQLiteCache,
    UpstreamNotFoundError,
    UpstreamUnavailableError,
    canonical_cache_key,
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
    TrialDetail,
    TrialsListResponse,
    TrialSummary,
)
from src.config import ApiConfig
from src.ingest.ctg_client import CTGClient

NCT_REGEX = re.compile(r"^NCT\d{8}$", re.IGNORECASE)
ALLOWED_SORTS = {"distance", "updated", "-updated", "start_date", "-start_date"}


def create_problem_response(
    status_code: int,
    title: str,
    detail: str,
    instance: str,
    error_type: str | None = None,
    code: str | None = None,
    invalid_params: list[InvalidParam] | None = None,
) -> JSONResponse:
    """Generate RFC 7807 compliant problem details response."""
    default_type_map = {
        400: "https://api.cti.example.org/errors/invalid-parameters",
        404: "https://api.cti.example.org/errors/not-found",
        422: "https://api.cti.example.org/errors/validation-error",
        429: "https://api.cti.example.org/errors/rate-limit-exceeded",
        500: "https://api.cti.example.org/errors/internal-error",
        503: "https://api.cti.example.org/errors/service-unavailable",
    }
    ptype = error_type or default_type_map.get(
        status_code, "https://api.cti.example.org/errors/general-error"
    )

    problem = ProblemDetail(
        type=ptype,
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
    """Holds shared client, cache, and circuit breaker dependencies."""

    def __init__(
        self,
        ctg_client: CTGClient | None = None,
        cache: SQLiteCache | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.ctg_client = ctg_client or CTGClient(
            ApiConfig(base_url="https://clinicaltrials.gov/api/v2")
        )
        self.cache = cache or SQLiteCache()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()


def create_app(container: APIContainer | None = None) -> FastAPI:
    """FastAPI application factory."""
    state_container = container or APIContainer()

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI) -> AsyncGenerator[None, None]:
        yield
        state_container.cache.close()
        state_container.ctg_client.close()

    app = FastAPI(
        title="Clinical Trial Read API",
        version="0.1.0",
        description="Public Read API over ClinicalTrials.gov with caching and circuit breaker.",
        lifespan=lifespan,
    )
    app.state.container = state_container

    # RFC 7807 Exception Handlers
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        invalid_params: list[InvalidParam] = []
        for err in exc.errors():
            loc = err.get("loc", ())
            param_name = str(loc[-1]) if loc else "unknown"
            invalid_params.append(
                InvalidParam(
                    name=param_name,
                    reason=err.get("msg", "Invalid parameter"),
                    value=err.get("input"),
                )
            )

        return create_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unprocessable Query Combination",
            detail="Request validation failed on one or more parameters.",
            instance=str(request.url.path),
            code="VALIDATION_FAILED",
            invalid_params=invalid_params,
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail_msg = exc.detail if isinstance(exc.detail, str) else "HTTP Exception"
        title_map = {
            400: "Invalid Query Parameters",
            404: "Resource Not Found",
            422: "Unprocessable Query Combination",
            503: "Service Unavailable",
        }
        return create_problem_response(
            status_code=exc.status_code,
            title=title_map.get(exc.status_code, "Error"),
            detail=detail_msg,
            instance=str(request.url.path),
        )

    def get_container() -> APIContainer:
        return app.state.container  # type: ignore[no-any-return]

    ContainerDep = Annotated[APIContainer, Depends(get_container)]

    def set_response_headers(response: Response, cache_state: CacheState, etag: str) -> None:
        now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        response.headers["Data-Snapshot"] = "live-api"
        response.headers["Data-Updated"] = now_utc
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        response.headers["X-Cache-Status"] = cache_state.value
        if cache_state == CacheState.STALE:
            response.headers["Warning"] = '110 "Response is Stale"'

    @app.get(
        "/v1/trials",
        response_model=TrialsListResponse,
        responses={
            400: {"model": ProblemDetail, "description": "Invalid Query Parameters"},
            422: {"model": ProblemDetail, "description": "Unprocessable Query Combination"},
            503: {"model": ProblemDetail, "description": "Service Unavailable"},
        },
    )
    def search_trials(
        request: Request,
        response: Response,
        deps: ContainerDep,
        cond: str | None = Query(default=None, description="Condition keyword"),
        status_filter: str | None = Query(
            default=None, alias="status", description="Recruitment status"
        ),
        phase: str | None = Query(default=None, description="Study phase"),
        lat: float | None = Query(default=None, description="Latitude"),
        lon: float | None = Query(default=None, description="Longitude"),
        radius_mi: float = Query(default=50.0, description="Search radius in miles"),
        min_age: int | None = Query(default=None, description="Minimum participant age"),
        max_age: int | None = Query(default=None, description="Maximum participant age"),
        sex: str | None = Query(default=None, description="Sex criteria: ALL, FEMALE, MALE"),
        healthy_volunteers: bool | None = Query(
            default=None, description="Healthy volunteer filter"
        ),
        std_age_group: str | None = Query(default=None, description="Standard age group"),
        intervention_type: str | None = Query(default=None, description="Intervention type"),
        sponsor_class: str | None = Query(default=None, description="Lead sponsor class"),
        updated_since: str | None = Query(
            default=None, description="Filter updated on or after date"
        ),
        cursor: str | None = Query(default=None, description="Pagination cursor"),
        limit: int = Query(default=20, description="Items per page"),
        sort: str = Query(default="-updated", description="Sort order"),
    ) -> Any:
        path = str(request.url.path)

        # Coordinate pairing validation
        if (lat is not None and lon is None) or (lat is None and lon is not None):
            return create_problem_response(
                status_code=400,
                title="Invalid Query Parameters",
                detail=(
                    "Parameters 'lat' and 'lon' must both be provided "
                    "for geographic distance filtering."
                ),
                instance=str(request.url),
                code="INVALID_COORDINATES",
                invalid_params=[
                    InvalidParam(
                        name="lat" if lat is None else "lon",
                        reason="Coordinates must be supplied as a (lat, lon) pair",
                        value=lat if lat is not None else lon,
                    )
                ],
            )

        # Coordinate range checks
        if lat is not None and not (-90.0 <= lat <= 90.0):
            return create_problem_response(
                status_code=400,
                title="Invalid Query Parameters",
                detail=f"Latitude value {lat} is outside the valid range of -90.0 to 90.0.",
                instance=str(request.url),
                code="INVALID_COORDINATES",
                invalid_params=[
                    InvalidParam(
                        name="lat",
                        reason="Must be between -90.0 and 90.0",
                        value=lat,
                    )
                ],
            )

        if lon is not None and not (-180.0 <= lon <= 180.0):
            return create_problem_response(
                status_code=400,
                title="Invalid Query Parameters",
                detail=f"Longitude value {lon} is outside the valid range of -180.0 to 180.0.",
                instance=str(request.url),
                code="INVALID_COORDINATES",
                invalid_params=[
                    InvalidParam(
                        name="lon",
                        reason="Must be between -180.0 and 180.0",
                        value=lon,
                    )
                ],
            )

        # Radius range check
        if radius_mi <= 0.0 or radius_mi > 500.0:
            return create_problem_response(
                status_code=400,
                title="Invalid Query Parameters",
                detail=f"Radius {radius_mi} is outside the allowable range of 1.0 to 500.0 miles.",
                instance=str(request.url),
                code="INVALID_RADIUS",
                invalid_params=[
                    InvalidParam(
                        name="radius_mi",
                        reason="Must be between 1.0 and 500.0",
                        value=radius_mi,
                    )
                ],
            )

        # Age bounds semantic check
        if min_age is not None and (min_age < 0 or min_age > 130):
            return create_problem_response(
                status_code=400,
                title="Invalid Query Parameters",
                detail=f"min_age {min_age} must be between 0 and 130.",
                instance=str(request.url),
                code="INVALID_AGE_RANGE",
            )
        if max_age is not None and (max_age < 0 or max_age > 130):
            return create_problem_response(
                status_code=400,
                title="Invalid Query Parameters",
                detail=f"max_age {max_age} must be between 0 and 130.",
                instance=str(request.url),
                code="INVALID_AGE_RANGE",
            )
        if min_age is not None and max_age is not None and min_age > max_age:
            return create_problem_response(
                status_code=400,
                title="Invalid Query Parameters",
                detail=f"min_age ({min_age}) cannot exceed max_age ({max_age}).",
                instance=str(request.url),
                code="INVALID_AGE_RANGE",
            )

        # Limit check
        if limit < 1 or limit > 100:
            return create_problem_response(
                status_code=400,
                title="Invalid Query Parameters",
                detail=f"limit {limit} must be between 1 and 100.",
                instance=str(request.url),
                code="INVALID_LIMIT",
            )

        # Sort checks
        if sort not in ALLOWED_SORTS:
            return create_problem_response(
                status_code=400,
                title="Invalid Query Parameters",
                detail=(
                    f"Sort parameter '{sort}' is not recognized. "
                    f"Allowed values: {', '.join(sorted(ALLOWED_SORTS))}."
                ),
                instance=str(request.url),
                code="INVALID_SORT",
            )

        if sort == "distance" and (lat is None or lon is None):
            return create_problem_response(
                status_code=422,
                title="Unprocessable Query Combination",
                detail=(
                    "Parameter 'sort=distance' requires both 'lat' and 'lon' "
                    "coordinates to be specified."
                ),
                instance=str(request.url),
                code="MISSING_COORDINATES_FOR_SORT",
                invalid_params=[
                    InvalidParam(
                        name="sort",
                        reason="Cannot sort by distance without search coordinates",
                        value="distance",
                    )
                ],
            )

        # Build upstream parameters
        upstream_params = build_upstream_search_params(
            cond=cond,
            status=status_filter,
            phase=phase,
            lat=lat,
            lon=lon,
            radius_mi=radius_mi,
            cursor=cursor,
            limit=limit,
            count_total=True,
        )

        cache_key = canonical_cache_key("trials_search", upstream_params)

        def fetch_upstream() -> tuple[str, str]:
            _, raw_text = deps.ctg_client.fetch_studies(upstream_params)
            etag = f'W/"{hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]}"'
            return raw_text, etag

        try:
            raw_payload, cache_state, etag = deps.cache.stale_while_revalidate(
                cache_key,
                fetch_upstream,
                circuit_breaker=deps.circuit_breaker,
            )
        except UpstreamUnavailableError as e:
            return create_problem_response(
                status_code=503,
                title="Service Unavailable",
                detail=str(e),
                instance=path,
                code="UPSTREAM_UNAVAILABLE",
            )
        except Exception as e:
            return create_problem_response(
                status_code=500,
                title="Internal Server Error",
                detail=f"Unexpected error executing search: {e}",
                instance=path,
                code="INTERNAL_ERROR",
            )

        set_response_headers(response, cache_state, etag)

        try:
            data = json.loads(raw_payload)
        except json.JSONDecodeError:
            return create_problem_response(
                status_code=500,
                title="Internal Server Error",
                detail="Failed to parse upstream response payload.",
                instance=path,
                code="UPSTREAM_DECODE_ERROR",
            )

        raw_studies = data.get("studies", [])
        summaries: list[TrialSummary] = []
        for s in raw_studies:
            if isinstance(s, dict):
                summary = normalize_study_summary(s, user_lat=lat, user_lon=lon)
                summaries.append(summary)

        # In-memory eligibility and sorting filter
        filtered_summaries = filter_and_sort_summaries(
            summaries,
            min_age=min_age,
            max_age=max_age,
            sex=sex,
            healthy_volunteers=healthy_volunteers,
            phase=phase,
            sort=sort,
        )

        next_page_token = data.get("nextPageToken")
        total_count = data.get("totalCount")
        has_more = bool(next_page_token)

        pagination = PaginationMetadata(
            next_cursor=next_page_token,
            prev_cursor=None,
            limit=limit,
            total_count=total_count,
            has_more=has_more,
        )

        return TrialsListResponse(items=filtered_summaries, pagination=pagination)

    @app.get(
        "/v1/trials/{nct_id}",
        response_model=TrialDetail,
        responses={
            400: {"model": ProblemDetail, "description": "Invalid NCT ID"},
            404: {"model": ProblemDetail, "description": "Study Not Found"},
            503: {"model": ProblemDetail, "description": "Service Unavailable"},
        },
    )
    def get_trial_detail(
        nct_id: str,
        request: Request,
        response: Response,
        deps: ContainerDep,
        lat: float | None = Query(default=None, description="Optional latitude for proximity"),
        lon: float | None = Query(default=None, description="Optional longitude for proximity"),
    ) -> Any:
        path = str(request.url.path)
        canonical_nct = nct_id.strip().upper()

        if not NCT_REGEX.match(canonical_nct):
            return create_problem_response(
                status_code=400,
                title="Invalid NCT ID",
                detail=(
                    f"NCT identifier '{nct_id}' is malformed. "
                    "Must conform to pattern ^NCT\\d{8}$."
                ),
                instance=path,
                code="INVALID_NCT_ID",
            )

        if (lat is not None and lon is None) or (lat is None and lon is not None):
            return create_problem_response(
                status_code=400,
                title="Invalid Query Parameters",
                detail=(
                    "Parameters 'lat' and 'lon' must both be provided "
                    "for geographic distance calculation."
                ),
                instance=str(request.url),
                code="INVALID_COORDINATES",
            )

        cache_key = canonical_cache_key("trial_detail", {"nct_id": canonical_nct})

        def fetch_upstream() -> tuple[str, str]:
            try:
                study_dict, raw_text = deps.ctg_client.fetch_study(canonical_nct)
                etag = f'W/"{hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]}"'
                return raw_text, etag
            except requests.HTTPError as http_err:
                if http_err.response is not None and http_err.response.status_code == 404:
                    raise UpstreamNotFoundError(f"Study {canonical_nct} not found") from http_err
                raise

        try:
            raw_payload, cache_state, etag = deps.cache.stale_while_revalidate(
                cache_key,
                fetch_upstream,
                circuit_breaker=deps.circuit_breaker,
            )
        except UpstreamNotFoundError:
            return create_problem_response(
                status_code=404,
                title="Resource Not Found",
                detail=(
                    f"Study identifier {canonical_nct} was not found "
                    "in active or historical snapshots."
                ),
                instance=path,
                code="STUDY_NOT_FOUND",
            )
        except UpstreamUnavailableError as e:
            return create_problem_response(
                status_code=503,
                title="Service Unavailable",
                detail=str(e),
                instance=path,
                code="UPSTREAM_UNAVAILABLE",
            )
        except Exception as e:
            return create_problem_response(
                status_code=500,
                title="Internal Server Error",
                detail=f"Unexpected error retrieving study {canonical_nct}: {e}",
                instance=path,
                code="INTERNAL_ERROR",
            )

        set_response_headers(response, cache_state, etag)

        try:
            data = json.loads(raw_payload)
        except json.JSONDecodeError:
            return create_problem_response(
                status_code=500,
                title="Internal Server Error",
                detail="Failed to decode study JSON.",
                instance=path,
                code="UPSTREAM_DECODE_ERROR",
            )

        # Upstream study can either be a top-level study dictionary or wrapped in { "study": ... }
        study_dict = data.get("study", data)
        detail = normalize_study_detail(study_dict, user_lat=lat, user_lon=lon)
        return detail

    @app.get("/health")
    @app.get("/v1/metadata/status")
    def health_check(deps: ContainerDep) -> dict[str, Any]:
        """Health check, cache statistics, and circuit breaker status."""
        cb_metrics = deps.circuit_breaker.get_metrics()
        status_str = "ok" if cb_metrics["state"] == CircuitState.CLOSED.value else "degraded"
        return {
            "status": status_str,
            "cache": deps.cache.get_metrics(),
            "circuit_breaker": cb_metrics,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    return app


# Default application instance
app = create_app()
