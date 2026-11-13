"""Automated contract tests for OpenAPI 3.1 specification and companion documentation.

Validates:
1. Valid OpenAPI 3.1 syntax and parsing via PyYAML.
2. Complete route coverage across the 6 core API endpoints.
3. Internal schema reference ($ref) resolution integrity across all nodes.
4. ADR 0065 compliance: prohibition of investigator/facility personal PII and
   inclusion of official registry contact URL.
5. Issue #62 compliance: presence of structured eligibility fields (age, sex,
   healthy volunteers, standard age groups, unstated age handling).
6. RFC 7807 Problem Details schema conformance.
7. Verification that example payloads in `docs/api/examples.md` conform to
   OpenAPI component schemas.
8. Standard response headers (`Data-Snapshot`, `Data-Updated`, `ETag`,
   `Cache-Control`) on all endpoints.
9. Traceability documentation integrity in `docs/api/source-field-map.md`.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "docs" / "api" / "openapi.yaml"
EXAMPLES_PATH = ROOT / "docs" / "api" / "examples.md"
SOURCE_MAP_PATH = ROOT / "docs" / "api" / "source-field-map.md"


@pytest.fixture(scope="module")
def openapi_doc() -> dict[str, Any]:
    assert OPENAPI_PATH.exists(), f"OpenAPI specification not found at {OPENAPI_PATH}"
    content = OPENAPI_PATH.read_text(encoding="utf-8")
    doc = yaml.safe_load(content)
    assert isinstance(doc, dict), "OpenAPI document must be a YAML dictionary"
    return doc


def _resolve_ref(doc: dict[str, Any], ref: str) -> dict[str, Any]:
    """Resolve an internal #/components/... reference."""
    if not ref.startswith("#/"):
        raise ValueError(f"Only local document references supported: {ref}")
    parts = ref.lstrip("#/").split("/")
    curr: Any = doc
    for part in parts:
        assert isinstance(curr, dict), f"Failed resolving ref part {part} in {ref}"
        assert part in curr, f"Reference token '{part}' not found in {ref}"
        curr = curr[part]
    return curr


def test_eligibility_sections_null_is_valid_under_openapi_31_semantics(
    openapi_doc: dict[str, Any],
) -> None:
    """Keywords beside $ref all apply in 3.1, so $ref-preserving validation must accept null."""
    validator = jsonschema.Draft202012Validator(
        {
            **openapi_doc,
            "$ref": "#/components/schemas/StructuredEligibility/properties/eligibility_sections",
        }
    )

    assert list(validator.iter_errors(None)) == []
    assert list(validator.iter_errors({"parsed": True})) == []
    assert not validator.is_valid("not an object")


def _dereference_schema(doc: dict[str, Any], schema: Any) -> Any:
    """Recursively dereference $ref nodes for validation with jsonschema."""
    if isinstance(schema, dict):
        if "$ref" in schema:
            target = _resolve_ref(doc, schema["$ref"])
            # Merge target while allowing overrides (excluding $ref itself)
            merged = {k: v for k, v in schema.items() if k != "$ref"}
            resolved_target = _dereference_schema(doc, target)
            if isinstance(resolved_target, dict):
                return {**resolved_target, **merged}
            return resolved_target
        return {k: _dereference_schema(doc, v) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [_dereference_schema(doc, item) for item in schema]
    return schema


def test_openapi_spec_structure(openapi_doc: dict[str, Any]) -> None:
    """Validate OpenAPI root metadata and required 3.1.0 structure."""
    version = openapi_doc.get("openapi", "")
    assert version.startswith("3.1"), f"Expected OpenAPI 3.1.x, found {version}"
    assert "info" in openapi_doc, "Missing info object"
    info = openapi_doc["info"]
    assert info.get("title") == "Clinical Trial Intelligence Read API"
    assert "version" in info
    assert "paths" in openapi_doc, "Missing paths object"
    assert "components" in openapi_doc, "Missing components object"


def test_core_endpoints_declared(openapi_doc: dict[str, Any]) -> None:
    """Ensure all 6 contracted endpoints are defined with GET operations."""
    paths = openapi_doc.get("paths", {})
    expected_endpoints = [
        "/v1/trials",
        "/v1/trials/{nct_id}",
        "/v1/conditions",
        "/v1/trials/{nct_id}/related",
        "/v1/geo/access",
        "/v1/meta/freshness",
    ]
    for endpoint in expected_endpoints:
        assert endpoint in paths, f"Expected endpoint {endpoint} not declared in paths"
        path_item = paths[endpoint]
        assert "get" in path_item, f"Expected GET operation for {endpoint}"


def test_all_internal_refs_resolve(openapi_doc: dict[str, Any]) -> None:
    """Traverse entire OpenAPI document and assert all $ref targets exist."""

    def check_refs(node: Any, breadcrumbs: str = "") -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                try:
                    resolved = _resolve_ref(openapi_doc, ref)
                    assert isinstance(resolved, dict), (
                        f"Ref {ref} did not resolve to a dict at {breadcrumbs}"
                    )
                except Exception as err:
                    pytest.fail(f"Broken $ref '{ref}' at {breadcrumbs}: {err}")
            for k, v in node.items():
                check_refs(v, f"{breadcrumbs}/{k}")
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                check_refs(item, f"{breadcrumbs}[{idx}]")

    check_refs(openapi_doc)


def test_adr_0065_privacy_guardrail(openapi_doc: dict[str, Any]) -> None:
    """Ensure schemas strictly adhere to ADR 0065 contact policy:

    1. No personal contact fields (investigator names, email, phone).
    2. Deep link to official registry record (registry_contact_url) is required on trial detail.
    """
    schemas = openapi_doc.get("components", {}).get("schemas", {})
    assert "TrialDetail" in schemas, "TrialDetail schema must be defined"
    assert "TrialSite" in schemas, "TrialSite schema must be defined"

    trial_detail = schemas["TrialDetail"]
    detail_props = trial_detail.get("properties", {})
    assert "registry_contact_url" in detail_props, "TrialDetail must expose registry_contact_url"
    assert "registry_contact_url" in trial_detail.get("required", []), (
        "registry_contact_url must be a required field per ADR 0065"
    )

    # Site schema checks: forbidden personal PII fields
    site_schema = schemas["TrialSite"]
    site_props = site_schema.get("properties", {})
    forbidden_keys = [
        "investigator_name",
        "investigator_email",
        "investigator_phone",
        "contact_email",
        "contact_phone",
        "contact_name",
        "email",
        "phone",
    ]
    for forbidden in forbidden_keys:
        assert forbidden not in site_props, (
            f"ADR 0065 violation: forbidden PII property '{forbidden}' found in TrialSite"
        )


def test_issue_62_structured_eligibility_fields(openapi_doc: dict[str, Any]) -> None:
    """Validate structured eligibility fields defined in Issue #62:

    - minimum_age_years, maximum_age_years, age_not_stated
    - sex, accepts_healthy_volunteers, std_age_groups
    """
    schemas = openapi_doc.get("components", {}).get("schemas", {})
    assert "StructuredEligibility" in schemas, "StructuredEligibility schema must be defined"
    eligibility = schemas["StructuredEligibility"]
    props = eligibility.get("properties", {})

    expected_props = [
        "minimum_age_years",
        "maximum_age_years",
        "age_not_stated",
        "sex",
        "accepts_healthy_volunteers",
        "std_age_groups",
    ]
    for prop in expected_props:
        assert prop in props, f"Issue #62 field '{prop}' missing in StructuredEligibility"

    # Issue #67: eligibility section split (additive field)
    schemas_full = openapi_doc.get("components", {}).get("schemas", {})
    assert "EligibilitySections" in schemas_full, "EligibilitySections schema must be defined"
    sections_schema = schemas_full["EligibilitySections"]
    sections_props = sections_schema.get("properties", {})
    for prop in ["parsed", "sections", "inclusion", "exclusion", "unclassified"]:
        assert prop in sections_props, f"Issue #67 field '{prop}' missing in EligibilitySections"
    assert "EligibilitySection" in schemas_full, "EligibilitySection schema must be defined"
    section_props = schemas_full["EligibilitySection"].get("properties", {})
    for prop in ["kind", "heading", "items"]:
        assert prop in section_props, f"Issue #67 field '{prop}' missing in EligibilitySection"
    assert "eligibility_sections" in props, (
        "Issue #67 field 'eligibility_sections' missing in StructuredEligibility"
    )
    # Finding 8: wording must cover blank, not just absent, criteria_text.
    assert "absent or blank" in props["eligibility_sections"].get("description", "")

    # Round 3 finding 6: criteria_text example must parse to match eligibility_sections example.
    from src.transform.eligibility_sections import split_eligibility_sections

    example_text = props["criteria_text"]["example"]
    parsed_example = split_eligibility_sections(example_text)
    assert parsed_example is not None
    assert parsed_example.inclusion == sections_props["inclusion"]["example"]
    assert parsed_example.exclusion == sections_props["exclusion"]["example"]
    assert parsed_example.unclassified == sections_props["unclassified"]["example"]

    # Search filter parameters check in GET /v1/trials
    trials_op = openapi_doc["paths"]["/v1/trials"]["get"]
    params = {p["name"]: p for p in trials_op.get("parameters", []) if "name" in p}
    for filter_param in ["min_age", "max_age", "sex", "healthy_volunteers", "std_age_group"]:
        assert filter_param in params, (
            f"Search filter parameter '{filter_param}' missing in GET /v1/trials"
        )


def test_rfc_7807_problem_details_schema(openapi_doc: dict[str, Any]) -> None:
    """Ensure RFC 7807 Problem Details compliant error schema is defined."""
    schemas = openapi_doc.get("components", {}).get("schemas", {})
    assert "ProblemDetails" in schemas, "ProblemDetails schema missing"
    problem = schemas["ProblemDetails"]
    props = problem.get("properties", {})
    for standard_field in ["type", "title", "status", "detail", "instance"]:
        assert standard_field in props, (
            f"RFC 7807 field '{standard_field}' missing in ProblemDetails"
        )

    # Check error response references on core endpoint
    responses = openapi_doc["paths"]["/v1/trials"]["get"].get("responses", {})
    for status_code in ["400", "422", "429", "500"]:
        assert status_code in responses, f"Response {status_code} missing in GET /v1/trials"
        resp_obj = responses[status_code]
        if "$ref" in resp_obj:
            resp_obj = _resolve_ref(openapi_doc, resp_obj["$ref"])
        content = resp_obj.get("content", {})
        assert "application/problem+json" in content, (
            f"Expected application/problem+json response for HTTP {status_code}"
        )


def test_provenance_and_caching_headers_on_all_endpoints(openapi_doc: dict[str, Any]) -> None:
    """Verify Data-Snapshot, Data-Updated, ETag, and Cache-Control headers on 200 responses."""
    expected_headers = ["Data-Snapshot", "Data-Updated", "ETag", "Cache-Control"]
    paths = openapi_doc.get("paths", {})
    for path, path_item in paths.items():
        if "get" not in path_item:
            continue
        responses = path_item["get"].get("responses", {})
        assert "200" in responses, f"Missing 200 response for {path}"
        headers = responses["200"].get("headers", {})
        for header_name in expected_headers:
            assert header_name in headers, (
                f"Missing header '{header_name}' in 200 response for {path}"
            )


def test_source_field_map_exists_and_detailed() -> None:
    """Verify source field map documentation covers all core layers."""
    assert SOURCE_MAP_PATH.exists(), f"Source field map missing at {SOURCE_MAP_PATH}"
    content = SOURCE_MAP_PATH.read_text(encoding="utf-8")
    assert "ClinicalTrials.gov API v2" in content
    assert "silver_trials" in content
    assert "dim_trial" in content
    assert "mart_trial_similarity" in content
    assert "ADR 0065" in content
    assert "Issue #62" in content


def _extract_json_blocks_from_markdown(md_text: str) -> dict[str, Any]:
    """Extract tagged JSON code blocks from examples markdown."""
    pattern = re.compile(
        r"<!--\s*example:\s*([a-zA-Z0-9_\-]+)\s*-->\s*```(?:json)?\s*([\s\S]*?)```"
    )
    examples = {}
    for match in pattern.finditer(md_text):
        label = match.group(1)
        raw_json = match.group(2).strip()
        try:
            data = json.loads(raw_json)
            examples[label] = data
        except json.JSONDecodeError as err:
            pytest.fail(f"Invalid JSON in example '{label}': {err}")
    return examples


def test_examples_payload_schema_conformity(openapi_doc: dict[str, Any]) -> None:
    """Validate all example payloads against corresponding OpenAPI component schemas."""
    assert EXAMPLES_PATH.exists(), f"Examples document missing at {EXAMPLES_PATH}"
    md_content = EXAMPLES_PATH.read_text(encoding="utf-8")
    examples = _extract_json_blocks_from_markdown(md_content)

    assert len(examples) >= 8, f"Expected at least 8 structured examples, found {len(examples)}"

    schema_mappings = {
        "trials_search_success": "TrialsListResponse",
        "trials_search_empty": "TrialsListResponse",
        "trials_search_live_postfiltered_empty": "TrialsListResponse",
        "trial_detail_success": "TrialDetail",
        "trial_detail_unknown_criteria": "TrialDetail",
        "conditions_autocomplete": "ConditionsListResponse",
        "trial_related_success": "RelatedTrialsResponse",
        "geo_access_success": "GeoAccessResponse",
        "meta_freshness_success": "MetaFreshnessResponse",
        "error_400_bad_request": "ProblemDetails",
        "error_404_not_found": "ProblemDetails",
        "error_422_validation": "ProblemDetails",
        "error_429_rate_limited": "ProblemDetails",
    }

    schemas = openapi_doc.get("components", {}).get("schemas", {})

    for example_key, schema_name in schema_mappings.items():
        assert example_key in examples, f"Missing example '{example_key}' in {EXAMPLES_PATH}"
        assert schema_name in schemas, f"Missing schema '{schema_name}' in OpenAPI document"

        raw_schema = schemas[schema_name]
        dereferenced_schema = _dereference_schema(openapi_doc, raw_schema)

        payload = examples[example_key]
        try:
            jsonschema.validate(instance=payload, schema=dereferenced_schema)
        except jsonschema.ValidationError as err:
            pytest.fail(
                f"Example '{example_key}' failed schema validation against "
                f"'{schema_name}': {err.message}"
            )


def test_warehouse_endpoint_responses_conform_to_openapi(
    openapi_doc: dict[str, Any], tmp_path: Path
) -> None:
    """Validate that responses from the DuckDB warehouse service conform to OpenAPI schemas."""
    from fastapi.testclient import TestClient

    from src.api.app import APIContainer, create_app
    from tests.test_api_warehouse import _init_fixture_database

    db_path = tmp_path / "contract_warehouse.duckdb"
    _init_fixture_database(db_path)

    container = APIContainer(warehouse_path=db_path)
    client = TestClient(create_app(container=container))

    schemas = openapi_doc.get("components", {}).get("schemas", {})
    trials_schema = _dereference_schema(openapi_doc, schemas["TrialsListResponse"])
    detail_schema = _dereference_schema(openapi_doc, schemas["TrialDetail"])
    problem_schema = _dereference_schema(openapi_doc, schemas["ProblemDetails"])

    # 1. GET /v1/trials conforms to TrialsListResponse schema
    resp = client.get("/v1/trials")
    assert resp.status_code == 200
    assert resp.headers.get("Data-Snapshot") is not None
    assert resp.headers.get("Data-Updated") is not None
    assert resp.headers.get("ETag") is not None
    assert resp.headers.get("Cache-Control") is not None
    jsonschema.validate(instance=resp.json(), schema=trials_schema)

    # 2. GET /v1/trials/{nct_id} conforms to TrialDetail schema
    resp = client.get("/v1/trials/NCT00000001")
    assert resp.status_code == 200
    assert resp.headers.get("Data-Snapshot") is not None
    assert resp.headers.get("ETag") is not None
    detail_data = resp.json()
    jsonschema.validate(instance=detail_data, schema=detail_schema)
    assert (
        detail_data.get("registry_contact_url")
        == "https://clinicaltrials.gov/study/NCT00000001#contacts-locations"
    )

    # ADR 0065 privacy check on actual response
    sites = detail_data.get("sites", [])
    assert len(sites) > 0
    for site in sites:
        for pii in ("investigator_name", "contact_name", "email", "phone"):
            assert pii not in site

    # 3. 404 Problem Details conforms to ProblemDetails schema
    resp_404 = client.get("/v1/trials/NCT99999999")
    assert resp_404.status_code == 404
    assert resp_404.headers["content-type"].startswith("application/problem+json")
    jsonschema.validate(instance=resp_404.json(), schema=problem_schema)

    # 4. 400 Problem Details conforms to ProblemDetails schema
    resp_400 = client.get("/v1/trials?min_age=90&max_age=10")
    assert resp_400.status_code == 400
    assert resp_400.headers["content-type"].startswith("application/problem+json")
    jsonschema.validate(instance=resp_400.json(), schema=problem_schema)

    # 5. GET /v1/trials/{nct_id}/related conforms to RelatedTrialsResponse schema
    related_schema = _dereference_schema(openapi_doc, schemas["RelatedTrialsResponse"])
    resp_rel = client.get("/v1/trials/NCT00000001/related")
    assert resp_rel.status_code == 200
    assert resp_rel.headers.get("Data-Snapshot") is not None
    assert resp_rel.headers.get("ETag") is not None
    assert resp_rel.headers.get("Cache-Control") is not None
    rel_data = resp_rel.json()
    jsonschema.validate(instance=rel_data, schema=related_schema)


@pytest.fixture
def freshness_client(project_root_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Warehouse-less app whose `.freshness.json` marker lives under the temp project root."""
    from fastapi.testclient import TestClient

    from src.api.app import APIContainer, create_app

    monkeypatch.setenv("CTI_REFRESHABLE_PROFILES", "adrd,oncology_nsclc")
    monkeypatch.delenv("CTI_FRESHNESS_MAX_AGE_DAYS", raising=False)
    container = APIContainer(cache_path=project_root_tmp / "cache.db")
    return TestClient(create_app(container=container))


def _write_freshness_marker(captured_at: datetime, **overrides: Any) -> None:
    from src.quality.freshness import marker_path

    now = datetime.now(UTC)
    capture = {
        "run_id": "20261020T120000Z_p",
        "captured_at_utc": captured_at.isoformat(),
        "row_counts": {"dim_trial": 3, "fct_trial_snapshot": 3, "mart_trial_similarity": 0},
    }
    marker = {
        "schema_version": 1,
        "attempt_at_utc": now.isoformat(),
        "attempt_status": "success",
        "error": None,
        "last_success": {
            "pipeline_completed_at_utc": (now - timedelta(seconds=1)).isoformat(),
            "source_api_version": "v2",
            "source_release_at_utc": None,
            "profiles": {pid: capture for pid in ("adrd", "oncology_nsclc")},
            "reconciliation": {"checks_total": 10, "checks_failed": 0},
        },
    } | overrides
    marker_path().parent.mkdir(parents=True, exist_ok=True)
    marker_path().write_text(json.dumps(marker), encoding="utf-8")


def test_meta_freshness_fresh_marker_conforms_to_openapi(
    openapi_doc: dict[str, Any], freshness_client: Any
) -> None:
    _write_freshness_marker(datetime.now(UTC) - timedelta(days=1))
    schema = _dereference_schema(
        openapi_doc, openapi_doc["components"]["schemas"]["MetaFreshnessResponse"]
    )

    resp = freshness_client.get("/v1/meta/freshness")

    assert resp.status_code == 200
    jsonschema.validate(
        instance=resp.json(), schema=schema, format_checker=jsonschema.FormatChecker()
    )
    body = resp.json()
    assert body["system_status"] == "HEALTHY"
    assert {p["indication_profile_id"] for p in body["profiles"]} == {"adrd", "oncology_nsclc"}
    assert body["profiles"][0]["total_studies_tracked"] == 3
    assert body["reconciliation"] == {"checks_total": 10, "checks_failed": 0}
    for header in ("Data-Snapshot", "Data-Updated", "ETag", "Cache-Control"):
        assert resp.headers.get(header)

    one = freshness_client.get("/v1/meta/freshness", params={"indication_profile_id": "adrd"})
    assert [p["indication_profile_id"] for p in one.json()["profiles"]] == ["adrd"]
    unknown = freshness_client.get("/v1/meta/freshness", params={"indication_profile_id": "nope"})
    assert unknown.status_code == 422
    assert unknown.headers["content-type"].startswith("application/problem+json")


def test_meta_freshness_stale_marker_reports_stale_status(
    openapi_doc: dict[str, Any], freshness_client: Any
) -> None:
    _write_freshness_marker(datetime.now(UTC) - timedelta(days=30))
    schema = _dereference_schema(
        openapi_doc, openapi_doc["components"]["schemas"]["MetaFreshnessResponse"]
    )

    resp = freshness_client.get("/v1/meta/freshness")

    assert resp.status_code == 200
    jsonschema.validate(
        instance=resp.json(), schema=schema, format_checker=jsonschema.FormatChecker()
    )
    assert resp.json()["system_status"] == "STALE"


def test_meta_freshness_profile_filter_scopes_staleness_to_requested_profile(
    freshness_client: Any,
) -> None:
    from src.quality.freshness import marker_path

    now = datetime.now(UTC)
    _write_freshness_marker(now)
    path = marker_path()
    marker = json.loads(path.read_text(encoding="utf-8"))
    profiles = marker["last_success"]["profiles"]
    profiles["adrd"]["captured_at_utc"] = (now - timedelta(days=1)).isoformat()
    profiles["oncology_nsclc"]["captured_at_utc"] = (now - timedelta(days=30)).isoformat()
    path.write_text(json.dumps(marker), encoding="utf-8")

    def status(**params: str) -> str:
        return freshness_client.get("/v1/meta/freshness", params=params).json()["system_status"]

    assert status() == "STALE"
    assert status(indication_profile_id="adrd") == "HEALTHY"
    assert status(indication_profile_id="oncology_nsclc") == "STALE"


def test_meta_freshness_running_attempt_reports_degraded_status(
    openapi_doc: dict[str, Any], freshness_client: Any
) -> None:
    _write_freshness_marker(datetime.now(UTC) - timedelta(days=1), attempt_status="running")
    schema = _dereference_schema(
        openapi_doc, openapi_doc["components"]["schemas"]["MetaFreshnessResponse"]
    )

    resp = freshness_client.get("/v1/meta/freshness")

    assert resp.status_code == 200
    jsonschema.validate(
        instance=resp.json(), schema=schema, format_checker=jsonschema.FormatChecker()
    )
    assert resp.json()["system_status"] == "DEGRADED"


def test_meta_freshness_is_not_publicly_cacheable_and_etag_revalidates(
    freshness_client: Any,
) -> None:
    _write_freshness_marker(datetime.now(UTC) - timedelta(days=1))

    first = freshness_client.get("/v1/meta/freshness")
    second = freshness_client.get(
        "/v1/meta/freshness", headers={"If-None-Match": first.headers["ETag"]}
    )

    assert first.headers["Cache-Control"] == "private, no-cache"
    assert second.status_code == 304


@pytest.mark.parametrize("marker", [None, "{not json", "failed"])
def test_meta_freshness_missing_malformed_or_failed_marker_is_503_problem(
    openapi_doc: dict[str, Any], freshness_client: Any, marker: str | None
) -> None:
    from src.quality.freshness import marker_path

    if marker == "failed":
        _write_freshness_marker(
            datetime.now(UTC), attempt_status="failed", error="reconciliation failed"
        )
    elif marker is not None:
        marker_path().parent.mkdir(parents=True, exist_ok=True)
        marker_path().write_text(marker, encoding="utf-8")
    schema = _dereference_schema(
        openapi_doc, openapi_doc["components"]["schemas"]["ProblemDetails"]
    )

    resp = freshness_client.get("/v1/meta/freshness")

    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")
    jsonschema.validate(instance=resp.json(), schema=schema)


def test_openapi_server_base_composition(openapi_doc: dict[str, Any]) -> None:
    """Server base URLs must compose cleanly with /v1 paths without doubling /v1."""
    from urllib.parse import urlsplit

    servers = openapi_doc.get("servers", [])
    assert isinstance(servers, list) and len(servers) >= 2
    by_url = {entry["url"]: entry for entry in servers if isinstance(entry, dict)}

    local_base = next(url for url in by_url if "localhost" in url)
    placeholder_base = next(url for url in by_url if "cti.example.org" in url)
    assert local_base == "http://localhost:8000"
    assert placeholder_base == "https://api.cti.example.org"
    assert "placeholder" in by_url[placeholder_base].get("description", "").lower()

    for url in by_url:
        parsed = urlsplit(url)
        assert parsed.scheme in {"http", "https"}
        assert not parsed.path.rstrip("/").endswith("/v1"), (
            f"Server URL {url!r} must not include '/v1' because paths already start with '/v1'"
        )

    paths = openapi_doc.get("paths", {})
    assert "/v1/trials" in paths and "get" in paths["/v1/trials"]
    assert "/v1/trials/{nct_id}" in paths and "get" in paths["/v1/trials/{nct_id}"]
    search_key = next(key for key in paths if key == "/v1/trials")
    detail_key = next(key for key in paths if key == "/v1/trials/{nct_id}")

    search_url = f"{local_base.rstrip('/')}{search_key}"
    detail_tmpl = detail_key.replace("{nct_id}", "NCT00000001")
    detail_url = f"{local_base.rstrip('/')}{detail_tmpl}"
    assert search_url == "http://localhost:8000/v1/trials"
    assert detail_url == "http://localhost:8000/v1/trials/NCT00000001"
    assert "/v1/v1" not in search_url
    assert "/v1/v1" not in detail_url

    placeholder_search = f"{placeholder_base.rstrip('/')}{search_key}"
    assert placeholder_search == "https://api.cti.example.org/v1/trials"
    assert "/v1/v1" not in placeholder_search


def test_selected_search_and_detail_get_handlers_with_composed_paths(
    openapi_doc: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Composed local paths reach implemented warehouse & mocked-live GET handlers."""
    from copy import deepcopy
    from unittest.mock import MagicMock
    from urllib.parse import urlsplit

    from fastapi.testclient import TestClient

    from src.api.app import APIContainer, create_app
    from src.ingest.ctg_client import CTGClient
    from tests.test_api_warehouse import _init_fixture_database

    servers = {entry["url"]: entry for entry in openapi_doc["servers"]}
    local_base = next(url for url in servers if "localhost" in url)
    paths = openapi_doc["paths"]
    assert "/v1/trials" in paths and "/v1/trials/{nct_id}" in paths

    composed_search = f"{local_base.rstrip('/')}/v1/trials"
    detail_path_tmpl = "/v1/trials/{nct_id}".replace("{nct_id}", "NCT00000001")
    composed_detail = f"{local_base.rstrip('/')}{detail_path_tmpl}"
    assert composed_search == "http://localhost:8000/v1/trials"
    assert composed_detail == "http://localhost:8000/v1/trials/NCT00000001"
    search_path = urlsplit(composed_search).path
    detail_path = urlsplit(composed_detail).path

    schemas = openapi_doc["components"]["schemas"]
    trials_schema = _dereference_schema(openapi_doc, schemas["TrialsListResponse"])
    detail_schema = _dereference_schema(openapi_doc, schemas["TrialDetail"])

    # 1. Warehouse backend with explicitly isolated SQLite cache path
    db_path = tmp_path / "contract_composed.duckdb"
    _init_fixture_database(db_path)
    wh_container = APIContainer(
        warehouse_path=db_path,
        cache_path=tmp_path / "warehouse_cache.sqlite",
    )
    with TestClient(create_app(container=wh_container)) as wh_client:
        wh_search = wh_client.get(search_path, params={"status": "RECRUITING"})
        assert wh_search.status_code == 200
        assert wh_search.headers["Data-Snapshot"] != "live-api"
        assert wh_search.headers["Cache-Control"] == "public, max-age=300"
        assert "stale-while-revalidate" not in wh_search.headers["Cache-Control"]
        assert "ratelimit-limit" not in {k.lower() for k in wh_search.headers}
        wh_payload = wh_search.json()
        jsonschema.validate(instance=wh_payload, schema=trials_schema)
        assert wh_payload["items"]
        assert all(item["overall_status"] == "RECRUITING" for item in wh_payload["items"])

        # Warehouse conditional 304 requires If-None-Match against prior ETag
        wh_304 = wh_client.get(
            search_path,
            params={"status": "RECRUITING"},
            headers={"If-None-Match": wh_search.headers["ETag"]},
        )
        assert wh_304.status_code == 304

        wh_detail = wh_client.get(detail_path)
        assert wh_detail.status_code == 200
        wh_detail_data = wh_detail.json()
        jsonschema.validate(instance=wh_detail_data, schema=detail_schema)
        assert wh_detail_data["nct_id"] == "NCT00000001"
        assert wh_detail_data["overall_status"] == "RECRUITING"

    # 2. Mocked-live backend with stubbed CTGClient and isolated cache path (zero network calls)
    monkeypatch.delenv("CTI_WAREHOUSE_PATH", raising=False)
    fixtures_dir = Path(__file__).parent / "fixtures" / "api"
    live_search_fixture = json.loads((fixtures_dir / "live_search.json").read_text("utf-8"))
    live_detail_fixture = deepcopy(
        json.loads((fixtures_dir / "live_detail.json").read_text("utf-8"))
    )
    live_detail_fixture["protocolSection"]["identificationModule"]["nctId"] = "NCT00000001"

    stub_ctg = MagicMock(spec=CTGClient)
    stub_ctg.fetch_studies.return_value = (live_search_fixture, json.dumps(live_search_fixture))
    stub_ctg.fetch_study.return_value = (live_detail_fixture, json.dumps(live_detail_fixture))

    live_container = APIContainer(
        ctg_client=stub_ctg,
        cache_path=tmp_path / "live_cache.sqlite",
    )
    with TestClient(create_app(container=live_container)) as live_client:
        live_search = live_client.get(
            search_path, params={"cond": "Alzheimer Disease", "status": "RECRUITING"}
        )
        assert live_search.status_code == 200
        assert live_search.headers["Data-Snapshot"] == "live-api"
        assert live_search.headers["X-Cache-Status"] == "MISS"
        assert live_search.headers["Cache-Control"] == "public, max-age=300"
        assert "stale-while-revalidate" not in live_search.headers["Cache-Control"]
        assert "ratelimit-limit" not in {k.lower() for k in live_search.headers}
        jsonschema.validate(instance=live_search.json(), schema=trials_schema)

        # Live repeat returns 200 HIT even when If-None-Match is sent
        live_repeat = live_client.get(
            search_path,
            params={"cond": "Alzheimer Disease", "status": "RECRUITING"},
            headers={"If-None-Match": live_search.headers["ETag"]},
        )
        assert live_repeat.status_code == 200
        assert live_repeat.headers["X-Cache-Status"] == "HIT"
        assert stub_ctg.fetch_studies.call_count == 1
        assert stub_ctg.fetch_studies.call_args.args[0]["filter.overallStatus"] == "RECRUITING"

        live_detail = live_client.get(detail_path)
        assert live_detail.status_code == 200
        assert live_detail.headers["Data-Snapshot"] == "live-api"
        live_detail_data = live_detail.json()
        jsonschema.validate(instance=live_detail_data, schema=detail_schema)
        assert live_detail_data["nct_id"] == "NCT00000001"
        stub_ctg.fetch_study.assert_called_once_with("NCT00000001")


def test_walkthrough_examples_headers_and_references() -> None:
    """Repaired search/detail walkthrough avoids unimplemented headers and links evidence."""
    content = EXAMPLES_PATH.read_text(encoding="utf-8")
    walkthrough_end = content.index("## 3. Conditions Autocomplete")
    walkthrough = content[:walkthrough_end]

    http_blocks = re.findall(r"```http\s*([\s\S]*?)```", walkthrough)
    assert len(http_blocks) >= 4
    http_text = "\n".join(http_blocks)

    assert "http://localhost:8000/v1/trials" in walkthrough
    assert "http://localhost:8000/v1/trials/NCT00000001" in walkthrough
    assert "status=RECRUITING" in walkthrough
    assert "stale-while-revalidate" not in http_text
    for header in ("RateLimit-Limit:", "RateLimit-Remaining:", "RateLimit-Reset:"):
        assert header not in http_text
    assert "overallStatus" in walkthrough
    assert "source-field-map.md" in walkthrough
    assert "feasibility_case_study.md" in walkthrough
    assert "20261003T080013Z_c1b234b8" in walkthrough
    assert "/v1/geo/access" in walkthrough
