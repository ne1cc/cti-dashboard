"""Patient HTML routes reuse the existing JSON search and protect form inputs."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import APIContainer, create_app
from src.api.cache import CircuitBreaker, SQLiteCache
from src.api.patient import PathOnlyAccessFilter
from src.ingest.ctg_client import CTGClient

FIXTURES = Path(__file__).parent / "fixtures" / "api"


@pytest.fixture
def patient_client():
    client = MagicMock(spec=CTGClient)
    search = json.loads((FIXTURES / "live_search.json").read_text())
    detail = json.loads((FIXTURES / "live_detail.json").read_text())
    client.fetch_studies.return_value = (search, json.dumps(search))
    client.fetch_study.return_value = (detail, json.dumps(detail))
    container = APIContainer(
        ctg_client=client, cache=SQLiteCache(":memory:"), circuit_breaker=CircuitBreaker()
    )
    with TestClient(create_app(container=container)) as test_client:
        yield test_client, client


def test_patient_home_has_landmarks_labels_and_privacy_notice(patient_client):
    client, api = patient_client
    result = client.get("/")
    assert result.status_code == 200
    for markup in (
        "<header>",
        "<nav",
        '<main id="main"',
        "<footer>",
        'href="#main"',
        'for="cond"',
        'for="postal_code"',
        'for="age"',
        'for="sex"',
        'for="status"',
    ):
        assert markup in result.text
    assert "browser history" in result.text
    assert "Data freshness unavailable" in result.text
    assert result.headers["cache-control"] == "no-store"
    api.fetch_studies.assert_not_called()


def test_search_matches_existing_json_api_and_reuses_upstream_cache(patient_client):
    client, api = patient_client
    result = client.get("/trials", params={"cond": "Alzheimer", "status": "RECRUITING"})
    assert result.status_code == 200
    json_response = client.get("/v1/trials", params={"cond": "Alzheimer", "status": "RECRUITING"})
    assert json_response.status_code == 200
    for trial in json_response.json()["items"]:
        assert trial["nct_id"] in result.text
        assert trial["brief_title"] in result.text
    assert api.fetch_studies.call_count == 1
    assert "Registry data retrieved:" in result.text


def test_age_maps_to_same_json_filters(patient_client):
    client, _ = patient_client
    page = client.get("/trials", params={"cond": "Alzheimer", "age": "70", "sex": "FEMALE"})
    api = client.get(
        "/v1/trials",
        params={"cond": "Alzheimer", "min_age": "70", "max_age": "70", "sex": "FEMALE"},
    )
    assert page.status_code == api.status_code == 200
    for item in api.json()["items"]:
        assert item["nct_id"] in page.text


def test_invalid_input_and_detail_are_safe_html(patient_client):
    client, _ = patient_client
    result = client.get("/trials", params={"postal_code": "wrong", "sex": "FEMALE", "age": "45"})
    assert result.status_code == 422
    assert "Please check your search details" in result.text
    assert "wrong" in result.text
    assert "<main" in result.text
    result = client.get("/trials/not-an-id")
    assert result.status_code == 400
    assert "not valid" in result.text
    result = client.get("/trials/NCT04245644")
    assert result.status_code == 200
    assert "https://clinicaltrials.gov/study/NCT04245644" in result.text
    assert "pii-coordinator" not in result.text


def test_error_page_and_escaping(patient_client):
    client, api = patient_client
    payload = api.fetch_studies.return_value[0]
    payload["studies"][0]["protocolSection"]["identificationModule"]["briefTitle"] = (
        "<script>alert(1)</script>"
    )
    api.fetch_studies.return_value = (payload, json.dumps(payload))
    result = client.get("/trials", params={"cond": "different"})
    assert "<script>alert(1)</script>" not in result.text
    assert "&lt;script&gt;" in result.text
    api.fetch_studies.side_effect = TimeoutError("sensitive 12345 47 FEMALE")
    result = client.get("/trials", params={"cond": "failure"})
    assert result.status_code in (500, 503, 504)
    assert "temporarily unavailable" in result.text
    assert "12345" not in result.text


def test_uvicorn_access_log_filter_removes_query():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %s',
        ("client", "GET", "/trials?postal_code=12345&age=47&sex=FEMALE", "1.1", 200),
        None,
    )
    assert PathOnlyAccessFilter().filter(record)
    assert record.getMessage() == 'client - "GET /trials HTTP/1.1" 200'


def test_warehouse_results_freshness_and_detail(tmp_path):
    from src.api.warehouse import DuckDBWarehouseManager
    from tests.test_api_warehouse import _init_fixture_database

    path = tmp_path / "warehouse.duckdb"
    _init_fixture_database(path)
    manager = DuckDBWarehouseManager(path)
    container = APIContainer(warehouse=manager, cache=SQLiteCache(":memory:"))
    with TestClient(create_app(container=container)) as client:
        home = client.get("/")
        assert "Warehouse snapshot:" in home.text
        page = client.get("/trials", params={"cond": "Alzheimer"})
        api = client.get("/v1/trials", params={"cond": "Alzheimer"})
        assert page.status_code == api.status_code == 200
        assert "Warehouse snapshot:" in page.text
        for item in api.json()["items"]:
            assert item["nct_id"] in page.text
        detail = client.get("/trials/NCT00000001")
        assert detail.status_code == 200
        assert "NCT00000001" in detail.text
        assert "Warehouse snapshot:" in detail.text
        assert client.get("/trials/NCT99999999").status_code == 404


def test_empty_results_conflicting_condition_and_pagination(patient_client):
    client, api = patient_client
    payload = {"studies": [], "nextPageToken": "next-token"}
    api.fetch_studies.return_value = (payload, json.dumps(payload))
    result = client.get("/trials", params={"condition": "cancer", "age": "55"})
    assert result.status_code == 200
    assert "No trials found on this page" in result.text
    assert "cursor=next-token" in result.text
    assert "age=55" in result.text
    assert (
        client.get("/trials", params={"condition": "cancer", "cond": "diabetes"}).status_code == 400
    )


def test_failure_logs_do_not_include_sensitive_values(patient_client, caplog, monkeypatch):
    client, api = patient_client
    from types import SimpleNamespace

    monkeypatch.setattr(
        "src.api.app.resolve_postal_code", lambda _: SimpleNamespace(lat=47.6, lon=-122.3)
    )
    api.fetch_studies.side_effect = TimeoutError("sentinel-98765 47 FEMALE query.secret")
    with caplog.at_level(logging.DEBUG):
        response = client.get(
            "/trials", params={"postal_code": "98101", "age": "47", "sex": "FEMALE"}
        )
    assert response.status_code >= 500
    logs = "\n".join(record.getMessage() for record in caplog.records if record.name != "httpx")
    for sensitive in ("sentinel-98765", "98101", "age=47", "FEMALE", "query.secret"):
        assert sensitive not in logs


def test_retry_logs_do_not_include_patient_query():
    import requests
    from loguru import logger

    from src.config import HttpConfig
    from src.ingest.retry_policy import build_retryer
    from src.utils.privacy import patient_request

    lines = []
    sink = logger.add(lines.append, format="{message}")
    token = patient_request.set(True)
    try:
        retryer = build_retryer(
            HttpConfig(max_retries=2, backoff_initial_seconds=0, backoff_max_seconds=0)
        )
        with pytest.raises(requests.Timeout):
            retryer(
                lambda: (_ for _ in ()).throw(
                    requests.Timeout("https://example.invalid/?zip=98101&age=47&sex=FEMALE")
                )
            )
    finally:
        patient_request.reset(token)
        logger.remove(sink)
    assert lines
    assert all("98101" not in str(line) and "FEMALE" not in str(line) for line in lines)


def test_ingestion_retry_module_imports_without_api_cycle():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "from src.ingest.retry_policy import build_retryer"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_stale_live_data_shows_cached_retrieval_time(patient_client, monkeypatch):
    from src.api.cache import CacheEntry, CacheState

    client, api = patient_client
    payload = api.fetch_studies.return_value[0]
    stale = CacheEntry(
        payload=json.dumps(payload),
        state=CacheState.STALE,
        etag='W/"stale"',
        age_seconds=3600,
        created_at=946684800,
        ttl_seconds=60,
    )
    monkeypatch.setattr(
        client.app.state.container.cache, "stale_if_error", lambda *args, **kwargs: stale
    )
    result = client.get("/trials", params={"cond": "test"})
    assert result.status_code == 200
    assert "2000-01-01T00:00:00Z" in result.text
    assert "cached data may be stale" in result.text
