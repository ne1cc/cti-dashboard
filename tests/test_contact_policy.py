"""Regression tests verifying contact exclusion across transform, schemas, and staging.

Per ADR 0065 (Contact Policy), the platform enforces an 'Official registry contact link
plus site list; store or expose no contact details' policy. These tests mechanically guard
against accidental ingestion, retention, or schema inclusion of personal contact details
(central contacts, overall officials, site contacts, phone numbers, email addresses).
"""

import json
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq
import yaml

from src.config import ApiConfig, IngestionConfig, PathsConfig, ProjectConfig
from src.ingest.snapshot_manifest import IngestionManifest, write_manifest
from src.transform.build_silver_entities import build_silver_for_run
from src.transform.export_parquet import ENTITY_COLUMNS
from src.transform.flatten_studies import flatten_study
from src.transform.normalize_conditions import get_taxonomy
from src.transform.normalize_locations import get_geography_rules
from src.utils.dates import utc_now

FORBIDDEN_CONTACT_TERMS = {
    "phone",
    "email",
    "contact",
    "contacts",
    "investigator",
    "official",
    "central_contact",
    "site_contact",
}


def _make_study_with_contacts(nct_id: str = "NCT09999999") -> dict[str, Any]:
    """Synthetic study payload populated with full contact details across modules."""
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
                "briefTitle": "A Study With Comprehensive Contact Information",
                "officialTitle": (
                    "A Study With Comprehensive Contact Information Across All Modules"
                ),
            },
            "statusModule": {
                "overallStatus": "RECRUITING",
                "startDateStruct": {"date": "2026-01-01"},
            },
            "designModule": {
                "studyType": "INTERVENTIONAL",
                "phases": ["PHASE2"],
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "Test Sponsor Inc", "class": "INDUSTRY"},
            },
            "contactsLocationsModule": {
                "centralContacts": [
                    {
                        "name": "Dr. Sarah Smith",
                        "role": "Study Chair",
                        "phone": "1-800-555-0199",
                        "phoneExt": "101",
                        "email": "sarah.smith@example.org",
                    },
                    {
                        "name": "Central Backup Contact",
                        "role": "Recruitment Coordinator",
                        "phone": "1-800-555-0200",
                        "email": "backup@example.org",
                    },
                ],
                "overallOfficials": [
                    {
                        "name": "Dr. John Doe",
                        "affiliation": "Major Academic Medical Center",
                        "role": "Principal Investigator",
                    }
                ],
                "locations": [
                    {
                        "facility": "Memorial Care Center",
                        "city": "Boston",
                        "state": "Massachusetts",
                        "zip": "02115",
                        "country": "United States",
                        "status": "RECRUITING",
                        "contacts": [
                            {
                                "name": "Jane Coordinator",
                                "role": "Site Study Coordinator",
                                "phone": "617-555-0100",
                                "email": "coordinator.boston@example.org",
                            },
                            {
                                "name": "Dr. Site Investigator",
                                "role": "Sub-Investigator",
                                "phone": "617-555-0101",
                                "email": "investigator.boston@example.org",
                            },
                        ],
                    }
                ],
            },
        }
    }


def test_flatten_study_excludes_central_and_site_contacts() -> None:
    """Verify flatten_study drops all centralContacts, overallOfficials, and location contacts."""
    study = _make_study_with_contacts()
    taxonomy = get_taxonomy()
    geography = get_geography_rules()

    result = flatten_study(
        study=study,
        ingestion_run_id="run_test_contact_01",
        snapshot_timestamp_utc="2026-10-24T12:00:00Z",
        taxonomy=taxonomy,
        geography=geography,
    )

    # 1. Inspect silver_trials
    trials = result["silver_trials"]
    assert len(trials) == 1
    trial = trials[0]
    for col in trial.keys():
        assert col not in FORBIDDEN_CONTACT_TERMS, f"Found contact column in silver_trials: {col}"

    # 2. Inspect silver_trial_locations
    locations = result["silver_trial_locations"]
    assert len(locations) == 1
    loc = locations[0]
    assert loc["facility_name"] == "Memorial Care Center"
    assert loc["city"] == "Boston"
    assert loc["state_normalized"] == "MA"
    assert loc["zip_code"] == "02115"
    assert "contacts" not in loc
    for col in loc.keys():
        assert col not in FORBIDDEN_CONTACT_TERMS, (
            f"Found contact column in silver_trial_locations: {col}"
        )

    # 3. Check that nowhere in any serialized silver entity do forbidden contact strings appear
    all_rows = []
    for _entity_name, rows in result.items():
        all_rows.extend(rows)

    serialized = json.dumps(all_rows)
    forbidden_values = [
        "Dr. Sarah Smith",
        "1-800-555-0199",
        "sarah.smith@example.org",
        "Central Backup Contact",
        "1-800-555-0200",
        "backup@example.org",
        "Dr. John Doe",
        "Jane Coordinator",
        "617-555-0100",
        "coordinator.boston@example.org",
        "Dr. Site Investigator",
        "617-555-0101",
        "investigator.boston@example.org",
    ]
    for val in forbidden_values:
        assert val not in serialized, (
            f"Forbidden personal contact value '{val}' leaked into silver entities"
        )


def test_parquet_entity_columns_manifest_contains_no_contact_fields() -> None:
    """Verify export_parquet.py's ENTITY_COLUMNS schema contains zero contact fields."""
    for entity_name, columns in ENTITY_COLUMNS.items():
        for col in columns:
            assert col not in FORBIDDEN_CONTACT_TERMS, (
                f"ENTITY_COLUMNS[{entity_name}] contains forbidden contact column '{col}'"
            )


def test_export_silver_parquet_has_no_contact_columns_or_leakage(tmp_path: Path) -> None:
    """End-to-end transform + Parquet export produces Parquet files without contact fields."""
    config = ProjectConfig(
        api=ApiConfig(base_url="https://ctg.test/api/v2"),
        paths=PathsConfig(
            bronze_api_responses=tmp_path / "bronze" / "api_responses",
            bronze_manifests=tmp_path / "bronze" / "manifests",
            silver=tmp_path / "silver",
            gold=tmp_path / "gold",
            duckdb=tmp_path / "warehouse" / "warehouse.duckdb",
            quarantine=tmp_path / "quarantine",
        ),
        ingestion=IngestionConfig(),
    )
    run_id = "run_contacts_leak_test"
    run_dir = config.paths.bronze_api_responses / f"run_id={run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    study = _make_study_with_contacts("NCT08888888")
    (run_dir / "page=00001.json").write_text(json.dumps({"studies": [study]}), encoding="utf-8")

    manifest = IngestionManifest(
        ingestion_run_id=run_id,
        query_hash="hash_test",
        endpoint="https://ctg.test/api/v2/studies",
        params={},
        status="success",
        started_at_utc=utc_now(),
        ended_at_utc=utc_now(),
        record_count=1,
    )
    write_manifest(config.paths.bronze_manifests, manifest)

    # Run build_silver_for_run
    build_silver_for_run(manifest, config)

    # Inspect all written Parquet files
    for entity in ENTITY_COLUMNS.keys():
        parquet_file = config.paths.silver / entity / f"run_id={run_id}.parquet"
        assert parquet_file.exists(), f"Expected Parquet file for entity {entity}"

        table = pq.read_table(parquet_file)
        schema = table.schema

        for col_name in schema.names:
            assert col_name not in FORBIDDEN_CONTACT_TERMS, (
                f"Parquet schema for {entity} contains contact column: {col_name}"
            )

        # Inspect data in trials and locations
        df = table.to_pandas()
        df_json = df.to_json()
        assert "sarah.smith@example.org" not in df_json
        assert "1-800-555-0199" not in df_json
        assert "coordinator.boston@example.org" not in df_json


def test_stg_trial_contacts_model_is_explicitly_empty() -> None:
    """Verify that stg_trial_contacts dbt model exists and is strictly empty (where 1 = 0)."""
    model_path = Path("dbt_clinical_trials/models/staging/stg_trial_contacts.sql")
    assert model_path.exists(), "stg_trial_contacts.sql must exist"

    sql = model_path.read_text(encoding="utf-8")
    assert "where 1 = 0" in sql.lower(), "stg_trial_contacts.sql must contain 'where 1 = 0'"

    # Execute SQL in DuckDB
    con = duckdb.connect()
    # Strip jinja comments or standard comments and execute
    clean_sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    result = con.execute(clean_sql).fetchall()
    assert len(result) == 0, f"stg_trial_contacts query returned rows: {result}"


def test_stg_trial_contacts_documented_in_staging_yml() -> None:
    """Verify stg_trial_contacts is documented in dbt staging schema as empty privacy guardrail."""
    staging_yml_path = Path("dbt_clinical_trials/models/staging/_staging.yml")
    assert staging_yml_path.exists(), "_staging.yml must exist"

    raw = yaml.safe_load(staging_yml_path.read_text(encoding="utf-8"))
    models = {m["name"]: m for m in raw.get("models", [])}
    assert "stg_trial_contacts" in models, "stg_trial_contacts must be documented in _staging.yml"
    desc = models["stg_trial_contacts"].get("description", "").lower()
    assert "empty" in desc
    assert "guardrail" in desc or "privacy" in desc


def test_bronze_fixture_flattening_extracts_zero_contacts() -> None:
    """Verify flattening the real bronze fixture extracts zero contact columns or details."""
    fixture_page = Path("tests/fixtures/bronze_snapshot/page=00001.json")
    if not fixture_page.exists():
        return

    payload = json.loads(fixture_page.read_text(encoding="utf-8"))
    studies = payload.get("studies", [])
    assert len(studies) > 0

    taxonomy = get_taxonomy()
    geography = get_geography_rules()

    for study in studies:
        res = flatten_study(
            study=study,
            ingestion_run_id="fixture_run",
            snapshot_timestamp_utc="2026-10-24T12:00:00Z",
            taxonomy=taxonomy,
            geography=geography,
        )
        for loc in res["silver_trial_locations"]:
            for col in loc.keys():
                assert col not in FORBIDDEN_CONTACT_TERMS
            assert "contacts" not in loc
