"""Test suite for live ClinicalTrials.gov search and protocol detail API.

Verifies:
- Upstream parameter translation (conditions, geo filter distance syntax, pagination).
- Complete response normalization against OpenAPI 3.1 Pydantic models.
- Issue #62 structured eligibility handling & preservation of unstated age bounds.
- Great-circle Haversine distance computation for proximate study sites.
- Strict ADR 0065 contact privacy enforcement (zero coordinator/investigator PII; deep links only).
- RFC 7807 problem details error envelopes (400, 404, 422, 500, 503).
- Synchronous stale-if-error fallback when upstream is down or degraded.
- Strictly ZERO live network calls.
"""

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Generator
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

from src.api.app import APIContainer, create_app
from src.api.cache import CircuitBreaker, CircuitState, SQLiteCache
from src.api.live import (
    build_geo_filter,
    build_upstream_search_params,
    filter_and_sort_summaries,
    haversine_distance_miles,
    normalize_study_detail,
    normalize_study_summary,
)
from src.api.models import NearestSiteSummary, TrialDetail, TrialsListResponse, TrialSummary
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


def _study_with_site_statuses() -> dict[str, Any]:
    """Study with a COMPLETED site ~0.2 mi from the user and a RECRUITING
    site ~15 mi away (Issue #63: only the latter may be the nearest site)."""
    return {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT99999999"},
            "statusModule": {"overallStatus": "RECRUITING"},
            "designModule": {"studyType": "INTERVENTIONAL", "phases": ["PHASE3"]},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Test Sponsor"}},
            "conditionsModule": {"conditions": ["Dementia"]},
            "eligibilityModule": {},
            "contactsLocationsModule": {
                "locations": [
                    {
                        "facility": "Closed Site",
                        "city": "Seattle",
                        "state": "WA",
                        "status": "COMPLETED",
                        "geoPoint": {"lat": 47.609, "lon": -122.3321},
                    },
                    {
                        "facility": "Open Site",
                        "city": "Bellevue",
                        "state": "WA",
                        "status": "RECRUITING",
                        "geoPoint": {"lat": 47.61, "lon": -122.0},
                    },
                ]
            },
        }
    }


def test_live_nearest_site_counts_only_recruiting_statuses() -> None:
    summary = normalize_study_summary(_study_with_site_statuses(), user_lat=47.61, user_lon=-122.33)

    assert summary.nearest_site is not None
    assert summary.nearest_site.facility_name == "Open Site"
    assert summary.nearest_site.location_status == "RECRUITING"
    assert summary.nearest_site.distance_miles is not None
    assert summary.nearest_site.distance_miles > 1.0


def test_live_nearest_site_none_when_only_inactive_sites() -> None:
    study = _study_with_site_statuses()
    study["protocolSection"]["contactsLocationsModule"]["locations"] = [
        {
            "facility": "Closed Site",
            "city": "Seattle",
            "state": "WA",
            "status": "COMPLETED",
            "geoPoint": {"lat": 47.609, "lon": -122.3321},
        }
    ]
    summary = normalize_study_summary(study, user_lat=47.61, user_lon=-122.33)

    assert summary.nearest_site is None


def test_live_detail_still_lists_inactive_sites() -> None:
    detail = normalize_study_detail(_study_with_site_statuses(), user_lat=47.61, user_lon=-122.33)

    statuses = {s.facility_name: s.location_status for s in detail.sites}
    assert statuses == {"Closed Site": "COMPLETED", "Open Site": "RECRUITING"}
    closed = next(s for s in detail.sites if s.facility_name == "Closed Site")
    assert closed.distance_miles is not None


def _location_search_payload() -> dict[str, Any]:
    """Studies the upstream geo filter matches via a COMPLETED Seattle site
    (Issue #63 round 3: `filter.geo` matches any site of any status), so only
    the post-filter can tell recruiting availability apart."""

    def study(nct_id: str, locations: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "protocolSection": {
                "identificationModule": {"nctId": nct_id},
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"studyType": "INTERVENTIONAL", "phases": ["PHASE3"]},
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Test Sponsor"}},
                "conditionsModule": {"conditions": ["Dementia"]},
                "eligibilityModule": {},
                "contactsLocationsModule": {"locations": locations},
            }
        }

    completed_seattle = {
        "facility": "Closed Site",
        "city": "Seattle",
        "state": "WA",
        "status": "COMPLETED",
        "geoPoint": {"lat": 47.609, "lon": -122.3321},
    }
    return {
        "studies": [
            # Only recruiting site is ~2,500 mi away in Boston: dropped.
            study(
                "NCT10000001",
                [
                    completed_seattle,
                    {
                        "facility": "Boston Site",
                        "city": "Boston",
                        "state": "MA",
                        "status": "RECRUITING",
                        "geoPoint": {"lat": 42.3601, "lon": -71.0589},
                    },
                ],
            ),
            # Only site is COMPLETED: dropped.
            study("NCT10000002", [completed_seattle]),
            # Nearby recruiting site (~15 mi): kept.
            study(
                "NCT10000003",
                [
                    {
                        "facility": "Open Site",
                        "city": "Bellevue",
                        "state": "WA",
                        "status": "RECRUITING",
                        "geoPoint": {"lat": 47.61, "lon": -122.0},
                    }
                ],
            ),
            # Nearby not-yet-recruiting site (~25 mi): kept.
            study(
                "NCT10000004",
                [
                    {
                        "facility": "Tacoma Site",
                        "city": "Tacoma",
                        "state": "WA",
                        "status": "NOT_YET_RECRUITING",
                        "geoPoint": {"lat": 47.2529, "lon": -122.4443},
                    }
                ],
            ),
        ],
        "totalCount": 4,
        "nextPageToken": None,
    }


def test_live_max_distance_filter_keeps_only_in_radius_trials() -> None:
    payload = _location_search_payload()
    summaries = [
        normalize_study_summary(study, user_lat=47.61, user_lon=-122.33)
        for study in payload["studies"]
    ]

    kept = filter_and_sort_summaries(summaries, sort="distance", max_distance_miles=50.0)

    assert [s.nct_id for s in kept] == ["NCT10000003", "NCT10000004"]
    first, second = kept
    assert first.nearest_site is not None and second.nearest_site is not None
    assert first.nearest_site.distance_miles is not None
    assert second.nearest_site.distance_miles is not None
    assert first.nearest_site.distance_miles <= second.nearest_site.distance_miles


def test_live_location_search_drops_trials_without_recruiting_site_in_radius(
    test_client: TestClient, test_container: APIContainer
) -> None:
    payload = _location_search_payload()
    test_container.ctg_client.fetch_studies.return_value = (payload, json.dumps(payload))  # type: ignore[attr-defined]

    result = test_client.get("/v1/trials?lat=47.61&lon=-122.33&radius_mi=50")
    assert result.status_code == 200
    data = result.json()

    assert [item["nct_id"] for item in data["items"]] == ["NCT10000003", "NCT10000004"]
    distances = [item["nearest_site"]["distance_miles"] for item in data["items"]]
    assert distances == sorted(distances)
    # The upstream totalCount (4) describes the any-status geo match, not the
    # recruiting-only result set.
    assert data["pagination"]["total_count"] is None


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


def test_normalize_study_detail_splits_eligibility_sections(
    live_detail_fixture: dict[str, Any],
) -> None:
    """Issue #67: live detail responses carry the lossless section split."""
    detail = normalize_study_detail(live_detail_fixture)

    sections = detail.eligibility.eligibility_sections
    assert sections is not None
    assert sections.parsed is True
    assert sections.inclusion == [
        "Age 55 to 85 years at screening.",
        "Meets NIA-AA criteria for probable Alzheimer disease.",
    ]
    assert sections.exclusion == ["Non-Alzheimer neurodegenerative disorder."]
    assert sections.unclassified == []
    # Ordered groups carry the verbatim headings on the live backend too.
    assert [(s.kind, s.heading) for s in sections.sections] == [
        ("inclusion", "Inclusion Criteria:"),
        ("exclusion", "Exclusion Criteria:"),
    ]


def test_normalize_study_detail_without_criteria_has_null_sections(
    source_capture_fixture: dict[str, Any],
) -> None:
    """Absent criteria text yields null sections instead of empty lists."""
    study = source_capture_fixture["edge_case_studies"][1]
    detail = normalize_study_detail(study)

    assert detail.eligibility.criteria_text is None
    assert detail.eligibility.eligibility_sections is None


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
    err = requests.ConnectionError("Upstream connection dropped")
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
        test_container.ctg_client.fetch_studies.side_effect = requests.Timeout(  # type: ignore[attr-defined]
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


def test_contact_pii_never_reaches_sqlite(tmp_path: Path, mock_ctg_client: CTGClient) -> None:
    path = tmp_path / "cache.db"
    container = APIContainer(ctg_client=mock_ctg_client, cache=SQLiteCache(path))
    with TestClient(create_app(container)) as client:
        assert client.get("/v1/trials").status_code == 200
        assert client.get("/v1/trials/NCT04245644").status_code == 200
        with closing(sqlite3.connect(path)) as conn:
            rows = conn.execute("SELECT payload FROM api_cache").fetchall()
        assert len(rows) == 2
        payloads = " ".join(row[0] for row in rows)
        for forbidden in (
            "centralContacts",
            "overallOfficials",
            '"contacts"',
            "@",
            "555-",
            "Dr. Lead Coordinator",
            "Dr. Principal Investigator",
            "Dr. Central Coordinator",
            "Local Site Coordinator",
        ):
            assert forbidden not in payloads


@pytest.mark.parametrize(
    "upstream_status,expected", [(400, 400), (404, 404), (422, 422), (403, 400)]
)
@pytest.mark.parametrize("endpoint", ["/v1/trials", "/v1/trials/NCT04245644"])
@pytest.mark.parametrize("cached", [False, True])
def test_upstream_client_errors_do_not_trip_breaker(
    test_client: TestClient,
    test_container: APIContainer,
    upstream_status: int,
    expected: int,
    endpoint: str,
    cached: bool,
) -> None:
    if cached:
        assert test_client.get(endpoint).status_code == 200
        test_container.cache._conn.execute("UPDATE api_cache SET created_at = 0")
    response = requests.Response()
    response.status_code = upstream_status
    error = requests.HTTPError("private upstream diagnostic", response=response)
    test_container.ctg_client.fetch_studies.side_effect = error  # type: ignore[attr-defined]
    test_container.ctg_client.fetch_study.side_effect = error  # type: ignore[attr-defined]
    for i in range(6):
        result = test_client.get(endpoint, params={} if cached else {"cursor": f"bad-{i}"})
        assert result.status_code == expected
        assert result.headers["content-type"].startswith("application/problem+json")
        assert "private upstream diagnostic" not in result.text
    assert test_container.circuit_breaker.state == CircuitState.CLOSED
    assert test_container.circuit_breaker.get_metrics()["total_failures"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"status": "BOGUS"},
        {"phase": "PHASE0"},
        {"sex": "banana"},
        {"std_age_group": "INFANT"},
        {"sponsor_class": "BOGUS"},
        {"radius_mi": 0.5},
        {"radius_mi": 501},
        {"min_age": -1},
        {"max_age": 131},
        {"limit": 101},
        {"sort": "banana"},
        {"lat": "nan", "lon": 0},
        {"updated_since": "garbage"},
    ],
)
def test_query_schema_validation(test_client: TestClient, params: dict[str, Any]) -> None:
    result = test_client.get("/v1/trials", params=params)
    assert result.status_code in (400, 422)
    assert result.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    "params",
    [
        {"intervention_type": "DRUG"},
        {"updated_since": "2026-01-01"},
    ],
)
def test_unsupported_filters_are_explicitly_rejected(
    test_client: TestClient, params: dict[str, Any]
) -> None:
    result = test_client.get("/v1/trials", params=params)
    assert result.status_code == 422
    assert result.json()["code"] == "UNSUPPORTED_FILTER"


@pytest.mark.parametrize(
    "params,expected_ids",
    [
        ({"sponsor_class": "NIH"}, []),
        ({"std_age_group": "CHILD"}, []),
        ({"phase": "PHASE3"}, ["NCT04245644", "NCT03887455"]),
        ({"min_age": 90}, ["NCT03887455"]),
        ({"sex": "FEMALE"}, ["NCT04245644", "NCT03887455"]),
        ({"healthy_volunteers": True}, []),
    ],
)
def test_page_filters_do_not_claim_unfiltered_total(
    test_client: TestClient, params: dict[str, Any], expected_ids: list[str]
) -> None:
    result = test_client.get("/v1/trials", params=params)
    assert result.status_code == 200
    data = result.json()
    assert sorted(item["nct_id"] for item in data["items"]) == sorted(expected_ids)
    assert data["pagination"]["total_count"] is None
    assert data["pagination"]["has_more"] == bool(data["pagination"]["next_cursor"])


def test_missing_fields_remain_unknown(live_detail_fixture: dict[str, Any]) -> None:
    study = deepcopy(live_detail_fixture)
    protocol = study["protocolSection"]
    protocol["identificationModule"].pop("briefTitle")
    protocol["designModule"].pop("studyType")
    protocol["sponsorCollaboratorsModule"]["leadSponsor"] = {}
    protocol["eligibilityModule"].pop("stdAges")
    protocol["eligibilityModule"].pop("sex")
    protocol["statusModule"]["startDateStruct"] = {"date": "invalid-date"}
    protocol["contactsLocationsModule"]["locations"] = [{}]
    summary = normalize_study_summary(study)
    detail = normalize_study_detail(study)
    assert summary.brief_title == "UNKNOWN"
    assert summary.study_type == "UNKNOWN"
    assert summary.lead_sponsor == "UNKNOWN"
    assert detail.sponsors.lead_sponsor.name == "UNKNOWN"
    assert summary.structured_eligibility.sex == detail.eligibility.sex == "UNKNOWN"
    assert summary.structured_eligibility.std_age_groups == detail.eligibility.std_age_groups == []
    assert detail.sites[0].country == detail.sites[0].city == "UNKNOWN"
    assert summary.start_date is None
    assert detail.conditions[0].condition_group is None
    assert detail.provenance.record_quality_flag != "ok"


def test_malformed_studies_are_skipped(
    test_client: TestClient, test_container: APIContainer, live_search_fixture: dict[str, Any]
) -> None:
    payload = deepcopy(live_search_fixture)
    payload["studies"] += [
        {},
        {"protocolSection": None},
        {"protocolSection": {"identificationModule": {"nctId": "invalid"}}},
    ]
    test_container.ctg_client.fetch_studies.return_value = (payload, json.dumps(payload))  # type: ignore[attr-defined]
    result = test_client.get("/v1/trials")
    assert result.status_code == 200
    assert len(result.json()["items"]) == 2
    assert result.json()["pagination"]["total_count"] is None


@pytest.mark.parametrize(
    "method,path,expected", [("GET", "/unknown", 404), ("POST", "/v1/trials", 405)]
)
def test_router_errors_are_problem_details(
    test_client: TestClient, method: str, path: str, expected: int
) -> None:
    result = test_client.request(method, path)
    assert result.status_code == expected
    assert result.headers["content-type"].startswith("application/problem+json")


def test_internal_errors_are_generic(test_container: APIContainer) -> None:
    test_container.ctg_client.fetch_studies.side_effect = ValueError(  # type: ignore[attr-defined]
        "private /srv/secret/cache.db diagnostic"
    )
    with TestClient(create_app(test_container), raise_server_exceptions=False) as client:
        result = client.get("/v1/trials")
    assert result.status_code == 500
    assert result.headers["content-type"].startswith("application/problem+json")
    assert "private" not in result.text
    assert "cache.db" not in result.text


def test_http_500_does_not_expose_exception_detail(test_container: APIContainer) -> None:
    app = create_app(test_container)

    @app.get("/fault")
    def fault() -> None:
        raise HTTPException(500, "private /srv/secret/cache.db diagnostic")

    with TestClient(app) as client:
        result = client.get("/fault")
    assert result.status_code == 500
    assert result.headers["content-type"].startswith("application/problem+json")
    assert "private" not in result.text
    assert "cache.db" not in result.text


@pytest.mark.parametrize("corruption", ["file", "row", "closed"])
def test_cache_errors_fall_back_to_upstream(
    tmp_path: Path, mock_ctg_client: CTGClient, corruption: str
) -> None:
    path = tmp_path / "cache.db"
    if corruption == "file":
        path.write_bytes(b"not a sqlite database")
    cache = SQLiteCache(path)
    container = APIContainer(ctg_client=mock_ctg_client, cache=cache)
    with TestClient(create_app(container)) as client:
        if corruption == "row":
            assert client.get("/v1/trials").status_code == 200
            cache._conn.execute("UPDATE api_cache SET payload = 'not json'")
        elif corruption == "closed":
            cache.close()
        result = client.get("/v1/trials")
        assert result.status_code == 200
        assert len(result.json()["items"]) == 2
        assert result.headers["X-Cache-Status"] == "MISS"
        assert client.get("/health").status_code == 200


def test_stale_headers_use_cached_timestamp(
    test_client: TestClient, test_container: APIContainer
) -> None:
    assert test_client.get("/v1/trials").status_code == 200
    timestamp = 1700000000
    test_container.cache._conn.execute("UPDATE api_cache SET created_at = ?", (timestamp,))
    test_container.circuit_breaker.record_failure()
    for _ in range(5):
        test_container.circuit_breaker.record_failure()
    result = test_client.get("/v1/trials")
    assert result.headers["X-Cache-Status"] == "STALE"
    assert result.headers["Data-Updated"] == datetime.fromtimestamp(timestamp, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert result.headers["Cache-Control"] == "no-cache"
    result = test_client.get("/v1/trials", params={"cond": "new"})
    assert result.status_code == 503
    assert result.json()["code"] == "UPSTREAM_UNAVAILABLE"


def test_api_import_does_not_create_cache(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import src.api; import src.api.live; import src.api.app"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_cache_path_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "custom.db"
    monkeypatch.setenv("CTI_CACHE_PATH", str(path))
    with TestClient(create_app()):
        assert path.exists()


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"min_age": 90}, ["NCT00000002"]),
        ({"max_age": 10}, ["NCT00000002"]),
        ({"sex": "MALE"}, ["NCT00000002"]),
        ({"healthy_volunteers": False}, ["NCT00000002"]),
        ({"phase": "PHASE1"}, ["NCT00000002"]),
        ({"sort": "updated"}, ["NCT00000001", "NCT00000002"]),
        ({"sort": "-updated"}, ["NCT00000002", "NCT00000001"]),
        ({"sort": "start_date"}, ["NCT00000002", "NCT00000001"]),
        ({"sort": "-start_date"}, ["NCT00000001", "NCT00000002"]),
        ({"sort": "distance"}, ["NCT00000002", "NCT00000001"]),
    ],
)
def test_eligibility_filters_and_sorts(
    live_search_fixture: dict[str, Any], filters: dict[str, Any], expected: list[str]
) -> None:
    base = normalize_study_summary(live_search_fixture["studies"][0])
    first = base.model_copy(
        update={
            "nct_id": "NCT00000001",
            "phase": "EARLY_PHASE1",
            "last_update_post_date": "2025-01-01",
            "start_date": "2026-01-01",
            "structured_eligibility": base.structured_eligibility.model_copy(
                update={
                    "sex": "FEMALE",
                    "accepts_healthy_volunteers": True,
                }
            ),
        }
    )
    second = base.model_copy(
        update={
            "nct_id": "NCT00000002",
            "phase": "PHASE1/PHASE2",
            "last_update_post_date": "2026-01-01",
            "start_date": "2025-01-01",
            "nearest_site": NearestSiteSummary(facility_name="Site", city="City", distance_miles=2),
            "structured_eligibility": base.structured_eligibility.model_copy(
                update={
                    "minimum_age_years": None,
                    "maximum_age_years": None,
                    "age_not_stated": True,
                    "sex": "UNKNOWN",
                    "accepts_healthy_volunteers": None,
                }
            ),
        }
    )
    second = TrialSummary.model_validate(second.model_dump())
    result = filter_and_sort_summaries([first, second], **filters)
    assert [item.nct_id for item in result] == expected


def test_interactive_transport_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    container = APIContainer(cache=SQLiteCache(":memory:"))
    timeouts: list[float] = []

    def timeout(*args: Any, **kwargs: Any) -> None:
        timeouts.append(kwargs["timeout"])
        raise requests.Timeout("upstream timeout")

    monkeypatch.setattr(container.ctg_client._client, "get", timeout)
    with TestClient(create_app(container)) as client:
        result = client.get("/v1/trials")
    assert result.status_code == 503
    assert 1 <= len(timeouts) <= 2
    assert all(value <= 10 for value in timeouts)


@pytest.mark.parametrize("error", [requests.Timeout(), requests.ConnectionError(), 429, 500, 503])
def test_only_outages_count_toward_breaker(
    test_client: TestClient, test_container: APIContainer, error: Exception | int
) -> None:
    if isinstance(error, int):
        response = requests.Response()
        response.status_code = error
        error = requests.HTTPError(response=response)
    test_container.ctg_client.fetch_studies.side_effect = error  # type: ignore[attr-defined]
    result = test_client.get("/v1/trials")
    assert result.status_code == 503
    assert test_container.circuit_breaker.get_metrics()["total_failures"] == 1
