"""Test suite for live ClinicalTrials.gov search and protocol detail API.

Verifies:
- Upstream parameter translation (conditions, geo filter distance syntax, pagination).
- Complete response normalization against OpenAPI 3.1 Pydantic models.
- Issue #62 structured eligibility handling & preservation of unstated age bounds.
- Great-circle Haversine distance computation for proximate study sites.
- Strict ADR 0065 contact privacy enforcement (zero coordinator/investigator PII; deep links only).
- RFC 7807 problem details error envelopes (400, 404, 422, 500, 503).
- Stale-while-revalidate cached responses when upstream is down or degraded.
- Strictly ZERO live network calls.
"""

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests
from fastapi.testclient import TestClient

from src.api.app import APIContainer, create_app
from src.api.cache import CircuitBreaker, CircuitState, SQLiteCache
from src.api.live import (
    build_geo_filter,
    build_upstream_search_params,
    haversine_distance_miles,
    normalize_study_detail,
    normalize_study_summary,
)
from src.api.models import TrialDetail, TrialsListResponse, TrialSummary
from src.ingest.ctg_client import CTGClient

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "api"


@pytest.fixture
def live_search_fixture() -> dict[str, Any]:
    with open(FIXTURES_DIR / "live_search.json", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


@pytest.fixture
def live_detail_fixture() -> dict[str, Any]:
    with open(FIXTURES_DIR / "live_detail.json", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


@pytest.fixture
def source_capture_fixture() -> dict[str, Any]:
    with open(FIXTURES_DIR / "source_capture.json", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


@pytest.fixture
def mock_ctg_client(
    live_search_fixture: dict[str, Any], live_detail_fixture: dict[str, Any]
) -> CTGClient:
    """Mock CTGClient backed entirely by realistic fixtures with zero network calls."""
    client = MagicMock(spec=CTGClient)
    client.fetch_studies.return_value = (
        live_search_fixture,
        json.dumps(live_search_fixture),
    )
    client.fetch_study.return_value = (
        live_detail_fixture,
        json.dumps(live_detail_fixture),
    )
    return client


@pytest.fixture
def test_container(mock_ctg_client: CTGClient) -> Generator[APIContainer, None, None]:
    cache = SQLiteCache(db_path=":memory:")
    circuit_breaker = CircuitBreaker()
    container = APIContainer(
        ctg_client=mock_ctg_client,
        cache=cache,
        circuit_breaker=circuit_breaker,
    )
    yield container
    cache.close()


@pytest.fixture
def test_client(test_container: APIContainer) -> Generator[TestClient, None, None]:
    test_app = create_app(container=test_container)
    with TestClient(test_app) as client:
        yield client


# --- Parameter Translation & Geo Filter Unit Tests ---


def test_build_geo_filter_syntax() -> None:
    geo_str = build_geo_filter(37.7749, -122.4194, 50)
    assert geo_str == "distance(37.7749,-122.4194,50mi)"

    geo_custom_unit = build_geo_filter(40.7128, -74.0060, "25km")
    assert geo_custom_unit == "distance(40.7128,-74.006,25km)"


def test_build_upstream_search_params() -> None:
    params = build_upstream_search_params(
        cond="Alzheimer Disease",
        status="RECRUITING",
        lat=37.7749,
        lon=-122.4194,
        radius_mi=25.0,
        cursor="next_tok",
        limit=10,
    )
    assert params["query.cond"] == "Alzheimer Disease"
    assert params["filter.overallStatus"] == "RECRUITING"
    assert params["filter.geo"] == "distance(37.7749,-122.4194,25.0mi)"
    assert params["pageToken"] == "next_tok"
    assert params["pageSize"] == "10"
    assert params["format"] == "json"


def test_haversine_distance_calculation() -> None:
    # SF (37.7749, -122.4194) to Palo Alto (37.4419, -122.1430) is approx ~28-30 miles
    dist = haversine_distance_miles(37.7749, -122.4194, 37.4419, -122.1430)
    assert 27.0 < dist < 32.0


# --- Response Normalization & Schema Conformance ---


def test_normalize_study_summary_conforms_to_schema(
    live_search_fixture: dict[str, Any],
) -> None:
    raw_study = live_search_fixture["studies"][0]
    summary = normalize_study_summary(raw_study, user_lat=37.7749, user_lon=-122.4194)

    # Validated by Pydantic model
    assert isinstance(summary, TrialSummary)
    assert summary.nct_id == "NCT04245644"
    assert summary.overall_status == "RECRUITING"
    assert summary.phase == "PHASE3"
    assert summary.lead_sponsor == "Biogen Inc."
    assert summary.structured_eligibility.minimum_age_years == 55.0
    assert summary.structured_eligibility.maximum_age_years == 85.0
    assert summary.structured_eligibility.age_not_stated is False
    assert summary.nearest_site is not None
    assert summary.nearest_site.facility_name == "UCSF Memory and Aging Center"
    assert summary.nearest_site.distance_miles is not None
    assert summary.registry_url == "https://clinicaltrials.gov/study/NCT04245644"


def test_normalize_study_detail_conforms_to_schema(
    live_detail_fixture: dict[str, Any],
) -> None:
    detail = normalize_study_detail(live_detail_fixture, user_lat=37.7749, user_lon=-122.4194)

    assert isinstance(detail, TrialDetail)
    assert detail.nct_id == "NCT04245644"
    assert detail.study_design.phase_normalized == "PHASE3"
    assert detail.sponsors.lead_sponsor.name == "Biogen Inc."
    assert detail.registry_url == "https://clinicaltrials.gov/study/NCT04245644"
    assert (
        detail.registry_contact_url
        == "https://clinicaltrials.gov/study/NCT04245644#contacts-and-locations"
    )
    assert len(detail.sites) == 2
    assert detail.sites[0].facility_name == "UCSF Memory and Aging Center"
    assert detail.sites[0].distance_miles is not None


# --- Issue #62 Structured Eligibility & Unstated Ages ---


def test_unstated_age_handling_in_source_capture(
    source_capture_fixture: dict[str, Any],
) -> None:
    study = source_capture_fixture["edge_case_studies"][0]
    summary = normalize_study_summary(study)

    assert summary.structured_eligibility.age_not_stated is True
    assert summary.structured_eligibility.minimum_age_years is None
    assert summary.structured_eligibility.maximum_age_years is None
    assert summary.structured_eligibility.sex == "ALL"
    assert summary.phase == "NOT_APPLICABLE"


def test_multi_phase_and_gender_based_in_source_capture(
    source_capture_fixture: dict[str, Any],
) -> None:
    study = source_capture_fixture["edge_case_studies"][1]
    detail = normalize_study_detail(study)

    assert detail.study_design.phase_normalized == "PHASE1/PHASE2"
    assert detail.eligibility.gender_based is True
    assert "postmenopausal" in (detail.eligibility.gender_description or "").lower()
    assert detail.eligibility.sex == "FEMALE"


# --- ADR 0065 Privacy Guardrails ---


def test_adr_0065_zero_contact_pii_in_payloads(
    test_client: TestClient,
    live_search_fixture: dict[str, Any],
    live_detail_fixture: dict[str, Any],
) -> None:
    """Verifies that personal contact PII in upstream responses is completely redacted."""
    # Verify raw fixture did contain sample PII coordinator
    assert "pii-coordinator@biogen.example.com" in json.dumps(live_search_fixture)
    assert "inquiries-pii@biogen.example.com" in json.dumps(live_detail_fixture)
    assert "415-555-0123" in json.dumps(live_detail_fixture)

    # Search endpoint
    search_resp = test_client.get("/v1/trials?cond=Alzheimer")
    assert search_resp.status_code == 200
    search_body = search_resp.text

    assert "pii-coordinator" not in search_body
    assert "inquiries-pii" not in search_body
    assert "415-555-0123" not in search_body
    assert "@biogen.example.com" not in search_body

    # Detail endpoint
    detail_resp = test_client.get("/v1/trials/NCT04245644")
    assert detail_resp.status_code == 200
    detail_body = detail_resp.text

    assert "inquiries-pii" not in detail_body
    assert "415-555-0123" not in detail_body
    assert "site-pii" not in detail_body
    assert "Dr. Lead Coordinator" not in detail_body
    assert "Dr. Principal Investigator" not in detail_body

    # Verified official registry contact link IS present
    data = detail_resp.json()
    assert (
        data["registry_contact_url"]
        == "https://clinicaltrials.gov/study/NCT04245644#contacts-and-locations"
    )


# --- API Endpoints Success & Header Verification ---


def test_search_trials_endpoint_success(test_client: TestClient) -> None:
    res = test_client.get(
        "/v1/trials?cond=Alzheimer+Disease&lat=37.7749&lon=-122.4194&radius_mi=50&limit=2"
    )
    assert res.status_code == 200
    data = res.json()

    assert "items" in data
    assert "pagination" in data
    assert len(data["items"]) == 2
    assert res.headers["Data-Snapshot"] == "live-api"
    assert "Data-Updated" in res.headers
    assert "ETag" in res.headers
    assert "Cache-Control" in res.headers
    assert res.headers["X-Cache-Status"] in ("HIT", "MISS", "STALE")

    # Contract model validation
    parsed = TrialsListResponse.model_validate(data)
    assert parsed.pagination.limit == 2


def test_get_trial_detail_endpoint_success(test_client: TestClient) -> None:
    res = test_client.get("/v1/trials/NCT04245644?lat=37.7749&lon=-122.4194")
    assert res.status_code == 200
    data = res.json()

    parsed = TrialDetail.model_validate(data)
    assert parsed.nct_id == "NCT04245644"
    assert parsed.registry_url == "https://clinicaltrials.gov/study/NCT04245644"
    assert res.headers["X-Cache-Status"] in ("HIT", "MISS", "STALE")


# --- RFC 7807 Error Handling Scenarios ---


def test_error_400_invalid_coordinates(test_client: TestClient) -> None:
    # lat without lon
    res1 = test_client.get("/v1/trials?lat=37.7749")
    assert res1.status_code == 400
    assert res1.headers["content-type"].startswith("application/problem+json")
    body1 = res1.json()
    assert body1["code"] == "INVALID_COORDINATES"
    assert body1["status"] == 400

    # lat out of range
    res2 = test_client.get("/v1/trials?lat=95.5&lon=-122.4")
    assert res2.status_code == 400
    body2 = res2.json()
    assert body2["code"] == "INVALID_COORDINATES"
    assert body2["invalid_params"][0]["name"] == "lat"


def test_error_400_invalid_age_range(test_client: TestClient) -> None:
    res = test_client.get("/v1/trials?min_age=80&max_age=50")
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "INVALID_AGE_RANGE"


def test_error_400_invalid_nct_id(test_client: TestClient) -> None:
    res = test_client.get("/v1/trials/INVALID_ID")
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "INVALID_NCT_ID"


def test_error_404_study_not_found(test_container: APIContainer) -> None:
    # Mock CTGClient raising 404 HTTPError
    mock_resp = requests.Response()
    mock_resp.status_code = 404
    test_container.ctg_client.fetch_study.side_effect = requests.HTTPError(  # type: ignore[attr-defined]
        "Not Found", response=mock_resp
    )

    test_app = create_app(container=test_container)
    with TestClient(test_app) as client:
        res = client.get("/v1/trials/NCT99999999")
        assert res.status_code == 404
        assert res.headers["content-type"].startswith("application/problem+json")
        body = res.json()
        assert body["code"] == "STUDY_NOT_FOUND"
        assert "NCT99999999" in body["detail"]


def test_error_422_sort_distance_without_coords(test_client: TestClient) -> None:
    res = test_client.get("/v1/trials?sort=distance")
    assert res.status_code == 422
    assert res.headers["content-type"].startswith("application/problem+json")
    body = res.json()
    assert body["code"] == "MISSING_COORDINATES_FOR_SORT"
    assert body["invalid_params"][0]["value"] == "distance"


def test_error_503_upstream_unavailable_without_cache(test_container: APIContainer) -> None:
    err = RuntimeError("Upstream connection dropped")
    test_container.ctg_client.fetch_studies.side_effect = err  # type: ignore[attr-defined]

    test_app = create_app(container=test_container)
    with TestClient(test_app) as client:
        res = client.get("/v1/trials?cond=Alzheimer")
        assert res.status_code == 503
        assert res.headers["content-type"].startswith("application/problem+json")
        body = res.json()
        assert body["code"] == "UPSTREAM_UNAVAILABLE"


def test_stale_cache_served_on_upstream_failure(
    test_container: APIContainer, live_search_fixture: dict[str, Any]
) -> None:
    """When upstream fails but cache has expired item, serve stale response with warning header."""
    test_app = create_app(container=test_container)
    with TestClient(test_app) as client:
        # 1. Warm cache
        res1 = client.get("/v1/trials?cond=Alzheimer")
        assert res1.status_code == 200
        assert res1.headers["X-Cache-Status"] == "MISS"

        # 2. Expire cache entry by resetting TTL in SQLite
        test_container.cache._conn.execute(
            "UPDATE api_cache SET ttl_seconds = 0.001, created_at = created_at - 100;"
        )

        # 3. Simulate upstream outage
        test_container.ctg_client.fetch_studies.side_effect = RuntimeError(  # type: ignore[attr-defined]
            "ClinicalTrials.gov 503 Outage"
        )

        # 4. Request should return 200 STALE with Warning header
        res2 = client.get("/v1/trials?cond=Alzheimer")
        assert res2.status_code == 200
        assert res2.headers["X-Cache-Status"] == "STALE"
        assert '110 "Response is Stale"' in res2.headers.get("Warning", "")


def test_health_check_endpoint(test_client: TestClient) -> None:
    res = test_client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "cache" in data
    assert "circuit_breaker" in data
    assert data["circuit_breaker"]["state"] == CircuitState.CLOSED.value

    # Metadata alias
    res_meta = test_client.get("/v1/metadata/status")
    assert res_meta.status_code == 200
    assert res_meta.json()["status"] == "ok"
