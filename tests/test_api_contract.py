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
