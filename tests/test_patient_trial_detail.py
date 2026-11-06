"""Patient trial detail page (Issue #66): sections, guardrails, and sparse-data rendering."""

import re
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from src.api import patient_copy as copy
from src.api.app import APIContainer, create_app
from src.api.cache import SQLiteCache
from src.api.warehouse import DuckDBWarehouseManager
from tests.test_api_warehouse import _init_fixture_database

# --- Rendered page ------------------------------------------------------------------------


@pytest.fixture
def warehouse_path(tmp_path: Path) -> Path:
    path = tmp_path / "warehouse.duckdb"
    _init_fixture_database(path)
    return path


@pytest.fixture
def client(warehouse_path: Path) -> Generator[TestClient, None, None]:
    manager = DuckDBWarehouseManager(warehouse_path)
    container = APIContainer(warehouse=manager, cache=SQLiteCache(":memory:"))
    with TestClient(create_app(container=container)) as test_client:
        yield test_client
    manager.close()


def _set_criteria(path: Path, text: str) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "UPDATE stg_trials SET eligibility_criteria_text = ? WHERE nct_id = 'NCT00000001'", [text]
    )
    con.close()


def test_detail_page_answers_what_who_where_contact(client):
    page = client.get("/trials/NCT00000001")
    assert page.status_code == 200
    text = page.text
    for heading in (
        "What is being tested",
        "What taking part involves",
        "Who can join",
        "Where",
        "How to get in touch",
    ):
        assert f">{heading}</h2>" in text
    assert text.count("<h1>") == 1
    assert 'class="status-badge">Recruiting<' in text
    assert "Large study to confirm a treatment works (Phase 3)" in text
    assert "Blinded: two groups involved" in text
    assert "This study has a placebo group" in text
    assert "78 weeks" in text
    assert "Phase 3 trial of an investigational tau-targeted therapy." in text
    assert "Last updated on the registry:" in text and "Last verified by the study team:" in text
    # inclusion list is collapsible, original registry text is one click away
    assert "<details><summary>The study&#39;s join rules" in text
    assert "<details><summary>Original registry text for all rules" in text
    # registry text is labelled as verbatim
    assert copy.REGISTRY_TEXT_LABEL in text
    # contact: registry link, never stored contact data
    assert 'href="https://clinicaltrials.gov/study/NCT00000001' in text
    assert copy.CONTACT_TEXT in text
    assert "mailto:" not in text and "tel:" not in text
    # standard text and freshness stamp
    assert copy.PAGE_NOTICE in text
    assert escape(copy.ELIGIBILITY_NOTE) in text
    assert "Warehouse snapshot:" in text
    assert "Open in maps" in text and "openstreetmap.org/search?query=" in text
    assert "<iframe" not in text


BANNED_WORDING = (
    "score",
    "priority",
    "rank",
    "you qualify",
    "you are eligible",
    "you may qualify",
    "good match",
    "suitab",
    "compatib",
    "comparable eligibility",
    "recommended for you",
)


def _assert_no_banned_wording(text: str) -> None:
    assert 'id="related-trials-heading"' in text  # related block must be in the checked page
    body = re.sub(r"<[^>]+>", " ", text).lower()
    for banned in BANNED_WORDING:
        assert banned not in body, banned


def test_detail_page_has_no_score_or_suitability_wording(client):
    _assert_no_banned_wording(client.get("/trials/NCT00000001").text)


def test_detail_page_has_no_eligibility_claim_when_related_trials_are_eligibility_compatible(
    warehouse_path,
):
    con = duckdb.connect(str(warehouse_path))
    con.execute(
        "UPDATE stg_trials SET sex = 'ALL', healthy_volunteers = false, "
        "minimum_age_years = 18, maximum_age_years = 80"
    )
    con.close()
    # The API field still carries the clause; the patient page must not render it.
    manager = DuckDBWarehouseManager(warehouse_path)
    container = APIContainer(warehouse=manager, cache=SQLiteCache(":memory:"))
    with TestClient(create_app(container=container)) as test_client:
        related = test_client.get("/v1/trials/NCT00000001/related").json()
        text = test_client.get("/trials/NCT00000001").text
    manager.close()
    assert "compatible eligibility criteria" in str(related["related_trials"])
    _assert_no_banned_wording(text)


def test_sites_sorted_by_distance_when_zip_known(client, monkeypatch):
    monkeypatch.setattr(
        "src.api.patient.resolve_postal_code", lambda _: SimpleNamespace(lat=37.7749, lon=-122.4194)
    )
    text = client.get("/trials/NCT00000001", params={"postal_code": "94103"}).text
    miles = [float(m) for m in re.findall(r"([\d.]+) miles away", text)]
    assert miles and miles == sorted(miles)
    assert "Distances are straight-line miles" in text


def test_unknown_zip_is_reported_not_guessed(client, monkeypatch):
    monkeypatch.setattr("src.api.patient.resolve_postal_code", lambda _: None)
    text = client.get("/trials/NCT00000001", params={"postal_code": "00000"}).text
    assert copy.DISTANCE_UNKNOWN_ZIP in text
    assert "miles away" not in text


def test_unclassified_text_is_visible_not_collapsed(warehouse_path):
    _set_criteria(warehouse_path, "Adults who can travel to the clinic every month.")
    manager = DuckDBWarehouseManager(warehouse_path)
    container = APIContainer(warehouse=manager, cache=SQLiteCache(":memory:"))
    with TestClient(create_app(container=container)) as test_client:
        text = test_client.get("/trials/NCT00000001").text
    manager.close()
    block = text.split('id="unclassified-heading"')[1].split("</div>")[0]
    assert "Adults who can travel to the clinic every month." in block
    assert escape(copy.UNCLASSIFIED_NOTE) in block
    assert "<details" not in block  # shown prominently, never hidden in a collapsible


def test_inclusion_and_exclusion_are_separate_collapsibles(warehouse_path):
    _set_criteria(
        warehouse_path,
        "Inclusion Criteria:\n* Age 55-85\n\nExclusion Criteria:\n* Prior tau therapy\n",
    )
    manager = DuckDBWarehouseManager(warehouse_path)
    container = APIContainer(warehouse=manager, cache=SQLiteCache(":memory:"))
    with TestClient(create_app(container=container)) as test_client:
        text = test_client.get("/trials/NCT00000001").text
    manager.close()
    inclusion = text.split("inclusion criteria (1)</summary>")[1].split("</details>")[0]
    exclusion = text.split("exclusion criteria (1)</summary>")[1].split("</details>")[0]
    assert "Age 55-85" in inclusion and "Prior tau therapy" not in inclusion
    assert "Prior tau therapy" in exclusion and "Age 55-85" not in exclusion
    assert "Rules we could not sort" not in text


def test_live_detail_page_renders_with_sparse_data(patient_client_live):
    client = patient_client_live
    page = client.get("/trials/NCT04245644")
    assert page.status_code == 200
    assert "How to get in touch" in page.text
    assert "https://clinicaltrials.gov/study/NCT04245644" in page.text
    assert "Registry data retrieved:" in page.text


@pytest.fixture
def patient_client_live() -> Generator[TestClient, None, None]:
    import json
    from unittest.mock import MagicMock

    from src.api.cache import CircuitBreaker
    from src.ingest.ctg_client import CTGClient

    fixtures = Path(__file__).parent / "fixtures" / "api"
    detail = json.loads((fixtures / "live_detail.json").read_text())
    ctg = MagicMock(spec=CTGClient)
    ctg.fetch_study.return_value = (detail, json.dumps(detail))
    container = APIContainer(
        ctg_client=ctg, cache=SQLiteCache(":memory:"), circuit_breaker=CircuitBreaker()
    )
    with TestClient(create_app(container=container)) as test_client:
        yield test_client


def test_results_links_carry_zip_only_when_given(client, monkeypatch):
    plain = client.get("/trials", params={"cond": "Alzheimer"}).text
    assert "/trials/NCT00000001" in plain and "?postal_code=" not in plain
    monkeypatch.setattr(
        "src.api.app.resolve_postal_code", lambda _: SimpleNamespace(lat=37.7749, lon=-122.4194)
    )
    linked = client.get("/trials", params={"cond": "Alzheimer", "postal_code": "94103"}).text
    assert "/trials/NCT00000001?postal_code=94103" in linked
