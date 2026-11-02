"""Integration tests for DuckDB warehouse adapter, parameterized SQL queries, and API endpoints.

Tests:
1. DuckDBConnectionPool concurrency, thread safety, bounded capacity, timeout, read-only mode.
2. Parameterized SQL query execution over all filters (cond, status, phase, age, sex, volunteers,
   std_age_group, sponsor_class, intervention_type, updated_since, lat/lon/radius).
3. Keyset cursor pagination over sort keys and tie-breaker (including NULL sort keys).
4. Detail endpoint (/v1/trials/{nct_id}) with site distances and study designs.
5. Strict ADR 0065 compliance: zero investigator/coordinator contact PII.
6. Liveness (/healthz) and readiness (/readyz) diagnostic endpoints.
7. Middlewares: Request correlation ID (X-Request-ID), ETag / 304, Data-Snapshot, Cache-Control.
8. Strictly zero live network calls.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Generator
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from src.api.app import APIContainer, create_app
from src.api.models import ReadinessStatus, TrialDetail, TrialsListResponse
from src.api.warehouse import (
    DuckDBConnectionPool,
    DuckDBWarehouseManager,
    WarehousePoolTimeoutError,
    encode_keyset_cursor,
)


def _init_fixture_database(db_path: Path) -> None:
    """Create sample warehouse database schema strictly matching real dbt models."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))

    # 1. Marts tables
    con.execute("""
    CREATE TABLE dim_trial (
        trial_key VARCHAR,
        nct_id VARCHAR,
        indication_profile_id VARCHAR,
        registry_url VARCHAR,
        current_brief_title VARCHAR,
        current_overall_status VARCHAR,
        current_phase VARCHAR,
        current_study_type VARCHAR,
        current_lead_sponsor VARCHAR,
        current_lead_sponsor_normalized VARCHAR,
        start_date DATE,
        primary_completion_date DATE,
        completion_date DATE,
        study_first_post_date DATE,
        enrollment_count INTEGER,
        enrollment_type VARCHAR,
        current_has_results_flag BOOLEAN,
        record_quality_flag VARCHAR,
        first_seen_snapshot_date DATE,
        latest_seen_snapshot_date DATE,
        active_in_latest_snapshot_flag BOOLEAN,
        minimum_age_raw VARCHAR,
        maximum_age_raw VARCHAR,
        min_age_years DOUBLE,
        max_age_years DOUBLE,
        std_age_groups VARCHAR[],
        sex_eligibility VARCHAR,
        gender_based BOOLEAN,
        gender_description VARCHAR,
        accepts_healthy_volunteers BOOLEAN,
        age_bounds_valid_flag BOOLEAN,
        age_bounds_contradictory_flag BOOLEAN,
        has_unspecified_min_age BOOLEAN,
        has_unspecified_max_age BOOLEAN,
        healthy_volunteers_unknown_flag BOOLEAN,
        sex_eligibility_unknown_flag BOOLEAN
    );

    CREATE TABLE fct_trial_snapshot (
        snapshot_key VARCHAR,
        trial_key VARCHAR,
        nct_id VARCHAR,
        indication_profile_id VARCHAR,
        ingestion_run_id VARCHAR,
        snapshot_date DATE,
        snapshot_timestamp_utc TIMESTAMP,
        overall_status VARCHAR,
        previous_status VARCHAR,
        status_changed_from_previous_snapshot_flag BOOLEAN,
        entered_recruiting_flag BOOLEAN,
        left_recruiting_flag BOOLEAN,
        phase_normalized VARCHAR,
        study_type VARCHAR,
        enrollment_count INTEGER,
        lead_sponsor_name VARCHAR,
        lead_sponsor_normalized VARCHAR,
        has_results_flag BOOLEAN,
        study_first_post_date DATE,
        record_quality_flag VARCHAR,
        first_seen_snapshot_date DATE,
        days_since_first_seen INTEGER,
        condition_group_count INTEGER,
        site_count_us INTEGER,
        record_hash VARCHAR,
        current_record_flag BOOLEAN
    );

    CREATE TABLE fct_trial_site (
        trial_site_key VARCHAR,
        trial_key VARCHAR,
        site_key VARCHAR,
        nct_id VARCHAR,
        indication_profile_id VARCHAR,
        ingestion_run_id VARCHAR,
        snapshot_date DATE,
        facility_name VARCHAR,
        facility_normalized VARCHAR,
        city VARCHAR,
        city_normalized VARCHAR,
        state_normalized VARCHAR,
        zip_code VARCHAR,
        location_status VARCHAR,
        trial_overall_status VARCHAR,
        phase_normalized VARCHAR,
        lead_sponsor_normalized VARCHAR
    );

    -- 2. Staging tables (snapshot_timestamp_utc and retrieved_at_utc are naive TIMESTAMP in dbt)
    CREATE TABLE stg_trials (
        ingestion_run_id VARCHAR,
        snapshot_id VARCHAR,
        retrieved_at_utc TIMESTAMP,
        raw_page_reference VARCHAR,
        raw_study_ordinal BIGINT,
        indication_profile_id VARCHAR,
        snapshot_timestamp_utc TIMESTAMP,
        snapshot_date DATE,
        nct_id VARCHAR,
        brief_title VARCHAR,
        official_title VARCHAR,
        study_type VARCHAR,
        overall_status VARCHAR,
        last_known_status VARCHAR,
        status_verified_date_raw VARCHAR,
        has_expanded_access BOOLEAN,
        start_date DATE,
        primary_completion_date DATE,
        completion_date DATE,
        study_first_post_date DATE,
        results_first_post_date DATE,
        last_update_post_date DATE,
        phase_raw VARCHAR,
        phase_normalized VARCHAR,
        enrollment_count INTEGER,
        enrollment_type VARCHAR,
        allocation VARCHAR,
        primary_purpose VARCHAR,
        lead_sponsor_name VARCHAR,
        lead_sponsor_normalized VARCHAR,
        responsible_party_type VARCHAR,
        healthy_volunteers BOOLEAN,
        minimum_age VARCHAR,
        minimum_age_raw VARCHAR,
        minimum_age_years DOUBLE,
        maximum_age VARCHAR,
        maximum_age_raw VARCHAR,
        maximum_age_years DOUBLE,
        sex VARCHAR,
        gender_based BOOLEAN,
        gender_description VARCHAR,
        std_age_groups VARCHAR[],
        eligibility_criteria_text VARCHAR,
        has_results_flag BOOLEAN,
        source_json_hash VARCHAR,
        record_quality_flag VARCHAR
    );

    CREATE TABLE stg_trial_locations (
        ingestion_run_id VARCHAR,
        snapshot_id VARCHAR,
        retrieved_at_utc TIMESTAMP,
        raw_page_reference VARCHAR,
        raw_study_ordinal BIGINT,
        indication_profile_id VARCHAR,
        nct_id VARCHAR,
        location_ordinal BIGINT,
        facility_name VARCHAR,
        facility_normalized VARCHAR,
        city VARCHAR,
        city_normalized VARCHAR,
        state_raw VARCHAR,
        state_normalized VARCHAR,
        zip_code VARCHAR,
        country VARCHAR,
        geo_scope VARCHAR,
        latitude DOUBLE,
        longitude DOUBLE,
        location_status VARCHAR,
        us_location_flag BOOLEAN,
        usable_geography_flag BOOLEAN,
        source_json_hash VARCHAR
    );

    CREATE TABLE stg_trial_conditions (
        ingestion_run_id VARCHAR,
        indication_profile_id VARCHAR,
        nct_id VARCHAR,
        condition_raw VARCHAR,
        condition_normalized VARCHAR,
        condition_group VARCHAR,
        dementia_relevance_flag BOOLEAN,
        mapping_confidence DOUBLE,
        source_json_hash VARCHAR
    );

    CREATE TABLE stg_trial_sponsors (
        ingestion_run_id VARCHAR,
        indication_profile_id VARCHAR,
        nct_id VARCHAR,
        sponsor_name VARCHAR,
        sponsor_role VARCHAR,
        sponsor_class VARCHAR,
        sponsor_normalized VARCHAR,
        source_json_hash VARCHAR
    );

    CREATE TABLE stg_trial_interventions (
        ingestion_run_id VARCHAR,
        indication_profile_id VARCHAR,
        nct_id VARCHAR,
        intervention_name VARCHAR,
        intervention_type VARCHAR,
        intervention_description VARCHAR,
        intervention_normalized VARCHAR,
        source_json_hash VARCHAR
    );
    """)

    # Populate Sample Trials in dim_trial (strictly matching real dbt schema)
    con.execute("""
    INSERT INTO dim_trial VALUES
    (
        'key1', 'NCT00000001', 'adrd', 'https://clinicaltrials.gov/study/NCT00000001',
        'Phase 3 Trial of Novel Tau Therapy in Alzheimer Disease',
        'RECRUITING', 'PHASE3', 'INTERVENTIONAL',
        'NeuroPharm Inc', 'neuropharm inc', '2024-01-10', '2027-01-10', '2027-06-10', '2024-01-01',
        350, 'ESTIMATED', false, 'ok', '2026-09-01', '2026-09-01', true,
        '55 Years', '85 Years', 55.0, 85.0, ['ADULT', 'OLDER_ADULT'], 'ALL',
        false, null, false, true, false, false, false, false, false
    ),
    (
        'key2', 'NCT00000002', 'adrd', 'https://clinicaltrials.gov/study/NCT00000002',
        'Phase 2 Monoclonal Antibody in Mild Cognitive Impairment',
        'ACTIVE_NOT_RECRUITING', 'PHASE2', 'INTERVENTIONAL',
        'Cedar Cognitive Institute', 'cedar cognitive institute',
        '2023-06-01', '2026-12-01', '2027-01-01', '2023-05-15',
        120, 'ACTUAL', false, 'ok', '2026-09-01', '2026-09-01', true,
        '60 Years', null, 60.0, null, ['OLDER_ADULT'], 'FEMALE',
        false, null, true, true, false, false, true, false, false
    ),
    (
        'key3', 'NCT00000003', 'oncology_nsclc', 'https://clinicaltrials.gov/study/NCT00000003',
        'Targeted Kinase Inhibitor in Advanced Non-Small Cell Lung Cancer',
        'COMPLETED', 'PHASE3', 'INTERVENTIONAL',
        'OncoTarget Corp', 'oncotarget corp',
        '2022-03-15', '2025-03-15', '2025-06-15', '2022-03-01',
        500, 'ACTUAL', true, 'ok', '2026-09-01', '2026-09-01', true,
        null, null, null, null, ['ADULT', 'OLDER_ADULT'], 'ALL',
        false, null, false, true, false, true, true, false, false
    ),
    (
        'key4', 'NCT00000004', 'adrd', 'https://clinicaltrials.gov/study/NCT00000004',
        'Early Safety of Neural Stimulation Device in Parkinson Disease',
        'RECRUITING', 'PHASE1', 'INTERVENTIONAL',
        'BioGenix Labs', 'biogenix labs',
        '2025-01-01', '2026-08-01', '2026-10-01', '2024-12-15',
        40, 'ESTIMATED', false, 'ok', '2026-09-01', '2026-09-01', true,
        '18 Years', '50 Years', 18.0, 50.0, ['ADULT'], 'MALE',
        false, null, true, true, false, false, false, false, false
    );
    """)

    # Populate fct_trial_snapshot
    con.execute("""
    INSERT INTO fct_trial_snapshot VALUES
    ('snap_k1', 'key1', 'NCT00000001', 'adrd', 'run_01', '2026-09-01', '2026-09-01 12:00:00Z',
     'RECRUITING', null, false, true, false, 'PHASE3', 'INTERVENTIONAL', 350,
     'NeuroPharm Inc', 'neuropharm inc', false, '2024-01-01', 'ok', '2026-09-01',
     0, 1, 1, 'h1', true),
    ('snap_k2', 'key2', 'NCT00000002', 'adrd', 'run_01', '2026-09-01', '2026-09-01 12:00:00Z',
     'ACTIVE_NOT_RECRUITING', null, false, false, false, 'PHASE2', 'INTERVENTIONAL', 120,
     'Cedar Cognitive Institute', 'cedar cognitive institute', false,
     '2023-05-15', 'ok', '2026-09-01',
     0, 1, 1, 'h2', true),
    ('snap_k3', 'key3', 'NCT00000003', 'oncology_nsclc', 'run_01',
     '2026-09-01', '2026-09-01 12:00:00Z',
     'COMPLETED', null, false, false, false, 'PHASE3', 'INTERVENTIONAL', 500,
     'OncoTarget Corp', 'oncotarget corp', true, '2022-03-01', 'ok', '2026-09-01',
     0, 1, 0, 'h3', true),
    ('snap_k4', 'key4', 'NCT00000004', 'adrd', 'run_01', '2026-09-01', '2026-09-01 12:00:00Z',
     'RECRUITING', null, false, true, false, 'PHASE1', 'INTERVENTIONAL', 40,
     'BioGenix Labs', 'biogenix labs', false, '2024-12-15', 'ok', '2026-09-01',
     0, 1, 1, 'h4', true);
    """)

    # Populate fct_trial_site
    con.execute("""
    INSERT INTO fct_trial_site VALUES
    ('site_k1', 'key1', 'site1', 'NCT00000001', 'adrd', 'run_01', '2026-09-01',
     'UCSF Memory and Aging Center', 'ucsf memory and aging center',
     'San Francisco', 'san francisco', 'CA', '94143', 'RECRUITING',
     'RECRUITING', 'PHASE3', 'neuropharm inc'),
    ('site_k2', 'key1', 'site2', 'NCT00000001', 'adrd', 'run_01', '2026-09-01',
     'Mass General Hospital', 'mass general hospital',
     'Boston', 'boston', 'MA', '02114', 'RECRUITING',
     'RECRUITING', 'PHASE3', 'neuropharm inc'),
    ('site_k3', 'key2', 'site3', 'NCT00000002', 'adrd', 'run_01', '2026-09-01',
     'Duke Health Clinic', 'duke health clinic',
     'Durham', 'durham', 'NC', '27710', 'ACTIVE_NOT_RECRUITING',
     'ACTIVE_NOT_RECRUITING', 'PHASE2', 'cedar cognitive institute'),
    ('site_k4', 'key4', 'site4', 'NCT00000004', 'adrd', 'run_01', '2026-09-01',
     'UCLA Health Center', 'ucla health center',
     'Los Angeles', 'los angeles', 'CA', '90095', 'RECRUITING',
     'RECRUITING', 'PHASE1', 'biogenix labs');
    """)

    # Populate stg_trials
    con.execute("""
    INSERT INTO stg_trials VALUES
    (
        'run_01', 'snap_20260901', '2026-09-01 12:00:00Z', 'page=00001.json', 1, 'adrd',
        '2026-09-01 12:00:00Z', '2026-09-01', 'NCT00000001',
        'Phase 3 Trial of Novel Tau Therapy in Alzheimer Disease',
        'A Randomized, Double-Blind, Placebo-Controlled Phase 3 Study'
        ' of Tau-Targeted Therapy',
        'INTERVENTIONAL', 'RECRUITING', null, '2026-06', false,
        '2024-01-10', '2027-01-10', '2027-06-10', '2024-01-01', null, '2026-06-15',
        'Phase 3', 'PHASE3', 350, 'ESTIMATED', 'RANDOMIZED', 'TREATMENT',
        'NeuroPharm Inc', 'neuropharm inc', 'SPONSOR', false,
        '55 Years', '55 Years', 55.0, '85 Years', '85 Years', 85.0, 'ALL',
        false, null, ['ADULT', 'OLDER_ADULT'],
        'Inclusion criteria: Age 55-85, confirmed early Alzheimer.',
        false, 'hash_nct01', 'ok'
    ),
    (
        'run_01', 'snap_20260901', '2026-09-01 12:00:00Z', 'page=00001.json', 2, 'adrd',
        '2026-09-01 12:00:00Z', '2026-09-01', 'NCT00000002',
        'Phase 2 Monoclonal Antibody in Mild Cognitive Impairment',
        'Evaluation of Cognitive Preservation with Monoclonal Antibody',
        'INTERVENTIONAL', 'ACTIVE_NOT_RECRUITING', null, '2026-05', false,
        '2023-06-01', '2026-12-01', '2027-01-01', '2023-05-15', null, '2026-05-10',
        'Phase 2', 'PHASE2', 120, 'ACTUAL', 'RANDOMIZED', 'TREATMENT',
        'Cedar Cognitive Institute', 'cedar cognitive institute', 'PRINCIPAL_INVESTIGATOR', true,
        '60 Years', '60 Years', 60.0, null, null, null, 'FEMALE',
        false, null, ['OLDER_ADULT'], 'Inclusion criteria: Women age 60+, MCI diagnosis.',
        false, 'hash_nct02', 'ok'
    ),
    (
        'run_01', 'snap_20260901', '2026-09-01 12:00:00Z', 'page=00001.json', 3, 'oncology_nsclc',
        '2026-09-01 12:00:00Z', '2026-09-01', 'NCT00000003',
        'Targeted Kinase Inhibitor in Advanced Non-Small Cell Lung Cancer',
        'Open-Label Phase 3 Study of Kinase Inhibitor in Stage IV NSCLC',
        'INTERVENTIONAL', 'COMPLETED', null, '2026-04', false,
        '2022-03-15', '2025-03-15', '2025-06-15', '2022-03-01', '2025-07-01', '2026-04-01',
        'Phase 3', 'PHASE3', 500, 'ACTUAL', 'NON_RANDOMIZED', 'TREATMENT',
        'OncoTarget Corp', 'oncotarget corp', 'SPONSOR', false,
        null, null, null, null, null, null, 'ALL',
        false, null, ['ADULT', 'OLDER_ADULT'], 'Inclusion: Histologically confirmed NSCLC.',
        true, 'hash_nct03', 'ok'
    ),
    (
        'run_01', 'snap_20260901', '2026-09-01 12:00:00Z', 'page=00001.json', 4, 'adrd',
        '2026-09-01 12:00:00Z', '2026-09-01', 'NCT00000004',
        'Early Safety of Neural Stimulation Device in Parkinson Disease',
        'First-in-Human Feasibility of Deep Brain Stimulation Array',
        'INTERVENTIONAL', 'RECRUITING', null, '2026-07', false,
        '2025-01-01', '2026-08-01', '2026-10-01', '2024-12-15', null, '2026-07-01',
        'Phase 1', 'PHASE1', 40, 'ESTIMATED', 'NON_RANDOMIZED', 'DEVICE_FEASIBILITY',
        'BioGenix Labs', 'biogenix labs', 'SPONSOR_INVESTIGATOR', true,
        '18 Years', '18 Years', 18.0, '50 Years', '50 Years', 50.0, 'MALE',
        false, null, ['ADULT'], 'Inclusion: Men aged 18 to 50.',
        false, 'hash_nct04', 'ok'
    );
    """)

    # Populate Locations (San Francisco, Boston, Durham, Los Angeles)
    con.execute("""
    INSERT INTO stg_trial_locations VALUES
    ('run_01', 'snap_20260901', '2026-09-01 12:00:00Z',
     'page=00001.json', 1, 'adrd', 'NCT00000001', 1,
     'UCSF Memory and Aging Center', 'ucsf memory and aging center',
     'San Francisco', 'san francisco',
     'CA', 'CA', '94143', 'United States', 'US',
     37.7631, -122.4578, 'RECRUITING', true, true, 'h1'),
    ('run_01', 'snap_20260901', '2026-09-01 12:00:00Z',
     'page=00001.json', 1, 'adrd', 'NCT00000001', 2,
     'Mass General Hospital', 'mass general hospital',
     'Boston', 'boston',
     'MA', 'MA', '02114', 'United States', 'US',
     42.3629, -71.0694, 'RECRUITING', true, true, 'h2'),
    ('run_01', 'snap_20260901', '2026-09-01 12:00:00Z',
     'page=00001.json', 2, 'adrd', 'NCT00000002', 1,
     'Duke Health Clinic', 'duke health clinic',
     'Durham', 'durham',
     'NC', 'NC', '27710', 'United States', 'US',
     35.9940, -78.8986, 'ACTIVE_NOT_RECRUITING', true, true, 'h3'),
    ('run_01', 'snap_20260901', '2026-09-01 12:00:00Z',
     'page=00001.json', 4, 'adrd', 'NCT00000004', 1,
     'UCLA Health Center', 'ucla health center',
     'Los Angeles', 'los angeles',
     'CA', 'CA', '90095', 'United States', 'US',
     34.0689, -118.4452, 'RECRUITING', true, true, 'h4');
    """)

    # Conditions
    con.execute("""
    INSERT INTO stg_trial_conditions VALUES
    ('run_01', 'adrd', 'NCT00000001', 'Alzheimer Disease',
     'alzheimer disease', 'Alzheimer Disease', true, 1.0, 'hc1'),
    ('run_01', 'adrd', 'NCT00000001', 'Dementia',
     'dementia', 'Alzheimer Disease', true, 0.9, 'hc2'),
    ('run_01', 'adrd', 'NCT00000002', 'Mild Cognitive Impairment',
     'mild cognitive impairment', 'Alzheimer Disease', true, 0.95, 'hc3'),
    ('run_01', 'oncology_nsclc', 'NCT00000003', 'Non-Small Cell Lung Cancer',
     'non-small cell lung cancer', 'Lung Cancer', false, 1.0, 'hc4'),
    ('run_01', 'adrd', 'NCT00000004', 'Parkinson Disease',
     'parkinson disease', 'Movement Disorders', false, 0.8, 'hc5');
    """)

    # Sponsors
    con.execute("""
    INSERT INTO stg_trial_sponsors VALUES
    ('run_01', 'adrd', 'NCT00000001', 'NeuroPharm Inc',
     'lead_sponsor', 'INDUSTRY', 'neuropharm inc', 'hs1'),
    ('run_01', 'adrd', 'NCT00000001', 'National Institute on Aging',
     'collaborator', 'NIH', 'national institute on aging', 'hs2'),
    ('run_01', 'adrd', 'NCT00000002', 'Cedar Cognitive Institute',
     'lead_sponsor', 'OTHER', 'cedar cognitive institute', 'hs3'),
    ('run_01', 'oncology_nsclc', 'NCT00000003', 'OncoTarget Corp',
     'lead_sponsor', 'INDUSTRY', 'oncotarget corp', 'hs4'),
    ('run_01', 'adrd', 'NCT00000004', 'BioGenix Labs',
     'lead_sponsor', 'NIH', 'biogenix labs', 'hs5');
    """)

    # Interventions
    con.execute("""
    INSERT INTO stg_trial_interventions VALUES
    ('run_01', 'adrd', 'NCT00000001', 'Tau-001', 'DRUG',
     'Investigational anti-tau small molecule', 'tau-001', 'hi1'),
    ('run_01', 'adrd', 'NCT00000002', 'CogniMab', 'BIOLOGICAL',
     'Monoclonal antibody targeting amyloid oligomers', 'cognimab', 'hi2'),
    ('run_01', 'oncology_nsclc', 'NCT00000003', 'Kinase-X', 'DRUG',
     'Oral tyrosine kinase inhibitor', 'kinase-x', 'hi3'),
    ('run_01', 'adrd', 'NCT00000004', 'NeuroStim DBS', 'DEVICE',
     'Deep brain stimulation micro-electrode array', 'neurostim dbs', 'hi4');
    """)

    con.close()


@pytest.fixture
def warehouse_db(tmp_path: Path) -> Path:
    """Fixture providing a populated DuckDB test warehouse."""
    db_file = tmp_path / "warehouse" / "clinical_trials.duckdb"
    _init_fixture_database(db_file)
    return db_file


@pytest.fixture
def warehouse_mgr(warehouse_db: Path) -> Generator[DuckDBWarehouseManager, None, None]:
    mgr = DuckDBWarehouseManager(warehouse_db, max_connections=4, timeout=2.0)
    yield mgr
    mgr.close()


@pytest.fixture
def client_warehouse(warehouse_mgr: DuckDBWarehouseManager) -> Generator[TestClient, None, None]:
    container = APIContainer(warehouse=warehouse_mgr)
    test_app = create_app(container=container)
    with TestClient(test_app) as client:
        yield client


# --- 1. Connection Pool & Concurrency Tests ---


def test_connection_pool_acquisition_and_release(warehouse_db: Path) -> None:
    pool = DuckDBConnectionPool(warehouse_db, max_connections=2, timeout=1.0)
    assert pool._created_count == 0

    with pool.connection() as conn1:
        res1 = conn1.execute("SELECT 42").fetchone()
        assert res1 == (42,)
        assert pool._created_count == 1

        with pool.connection() as conn2:
            res2 = conn2.execute("SELECT 100").fetchone()
            assert res2 == (100,)
            assert pool._created_count == 2

    # After exiting contexts, pool holds idle connections
    assert pool._pool.qsize() == 2
    pool.close()
    assert pool._closed is True


def test_connection_pool_timeout_when_exhausted(warehouse_db: Path) -> None:
    pool = DuckDBConnectionPool(warehouse_db, max_connections=1, timeout=0.2)

    conn = pool.acquire()
    try:
        with pytest.raises(WarehousePoolTimeoutError, match="Timed out after 0.2s"):
            pool.acquire()
    finally:
        pool.release(conn)
        pool.close()


def test_connection_pool_thread_safety(warehouse_db: Path) -> None:
    pool = DuckDBConnectionPool(warehouse_db, max_connections=3, timeout=3.0)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(5):
                with pool.connection() as conn:
                    row = conn.execute(
                        "SELECT nct_id FROM dim_trial ORDER BY nct_id LIMIT 1"
                    ).fetchone()
                    assert row is not None
                    time.sleep(0.01)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    pool.close()
    assert not errors, f"Thread safety errors encountered: {errors}"


def test_connection_pool_read_only_enforced(warehouse_db: Path) -> None:
    pool = DuckDBConnectionPool(warehouse_db, max_connections=1)
    with pool.connection() as conn:
        with pytest.raises(
            (duckdb.InvalidInputException, duckdb.PermissionException), match="read-only mode"
        ):
            conn.execute("CREATE TABLE forbidden (id INT);")
    pool.close()


# --- 2. Diagnostics: /healthz and /readyz ---


def test_liveness_healthz(client_warehouse: TestClient) -> None:
    resp = client_warehouse.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "timestamp" in body
    assert "X-Request-ID" in resp.headers


def test_readiness_readyz_success(client_warehouse: TestClient) -> None:
    resp = client_warehouse.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    status = ReadinessStatus(**body)
    assert status.status == "ready"
    assert status.backend == "warehouse"
    assert status.database_accessible is True
    assert status.snapshot_id == "snap_20260901"
    assert status.trials_count == 4
    # RFC 3339 UTC assertion
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", status.snapshot_timestamp_utc)


def test_readiness_readyz_missing_database(tmp_path: Path) -> None:
    missing_file = tmp_path / "non_existent.duckdb"
    container = APIContainer(warehouse_path=missing_file)
    with TestClient(create_app(container=container)) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["code"] == "WAREHOUSE_NOT_READY"


def test_readiness_readyz_missing_required_table(tmp_path: Path) -> None:
    db_file = tmp_path / "incomplete.duckdb"
    con = duckdb.connect(str(db_file))
    con.execute("CREATE TABLE dim_trial (nct_id VARCHAR);")
    con.close()

    container = APIContainer(warehouse_path=db_file)
    with TestClient(create_app(container=container)) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["code"] == "WAREHOUSE_NOT_READY"
        assert "missing required tables" in body["detail"]


# --- 3. Parameterized Search Tests ---


def test_warehouse_search_all_trials(client_warehouse: TestClient) -> None:
    resp = client_warehouse.get("/v1/trials")
    assert resp.status_code == 200
    data = resp.json()
    list_resp = TrialsListResponse(**data)
    assert len(list_resp.items) == 4
    assert list_resp.pagination.total_count == 4
    assert list_resp.pagination.has_more is False

    # Check response headers and RFC 3339 format
    assert resp.headers["Data-Snapshot"] == "snap_20260901"
    assert "Data-Updated" in resp.headers
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", resp.headers["Data-Updated"])
    assert "ETag" in resp.headers
    assert resp.headers["Cache-Control"] == "public, max-age=300"


def test_warehouse_search_filter_condition(client_warehouse: TestClient) -> None:
    # Match Alzheimer Disease
    resp = client_warehouse.get("/v1/trials?cond=Alzheimer")
    assert resp.status_code == 200
    items = resp.json()["items"]
    nct_ids = {item["nct_id"] for item in items}
    assert "NCT00000001" in nct_ids
    assert "NCT00000003" not in nct_ids  # Lung cancer excluded

    # Non-matching condition
    resp_empty = client_warehouse.get("/v1/trials?cond=NonExistentDiseaseXYZ")
    assert resp_empty.status_code == 200
    assert len(resp_empty.json()["items"]) == 0
    assert resp_empty.json()["pagination"]["total_count"] == 0


def test_warehouse_search_filter_phase(client_warehouse: TestClient) -> None:
    resp = client_warehouse.get("/v1/trials?phase=PHASE3")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert all(item["phase"] == "PHASE3" for item in items)


def test_warehouse_search_filter_status(client_warehouse: TestClient) -> None:
    resp = client_warehouse.get("/v1/trials?status=RECRUITING")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert {item["nct_id"] for item in items} == {"NCT00000001", "NCT00000004"}


def test_warehouse_search_filter_age_and_unstated_preservation(
    client_warehouse: TestClient,
) -> None:
    # min_age=70 should include:
    # NCT00000001 (max 85 >= 70)
    # NCT00000002 (max null -> unstated upper bound preserved)
    # NCT00000003 (both null -> unstated preserved)
    # NCT00000004 has max 50 < 70 -> excluded!
    resp = client_warehouse.get("/v1/trials?min_age=70")
    assert resp.status_code == 200
    nct_ids = {item["nct_id"] for item in resp.json()["items"]}
    assert "NCT00000001" in nct_ids
    assert "NCT00000002" in nct_ids
    assert "NCT00000003" in nct_ids
    assert "NCT00000004" not in nct_ids

    # max_age=30 should include:
    # NCT00000004 (min 18 <= 30)
    # NCT00000003 (both null -> preserved)
    # NCT00000001 (min 55 > 30 -> excluded)
    # NCT00000002 (min 60 > 30 -> excluded)
    resp_young = client_warehouse.get("/v1/trials?max_age=30")
    assert resp_young.status_code == 200
    young_ids = {item["nct_id"] for item in resp_young.json()["items"]}
    assert "NCT00000004" in young_ids
    assert "NCT00000003" in young_ids
    assert "NCT00000001" not in young_ids
    assert "NCT00000002" not in young_ids


def test_warehouse_search_filter_sex_and_healthy_volunteers(client_warehouse: TestClient) -> None:
    # Sex: FEMALE should match FEMALE and ALL
    resp_female = client_warehouse.get("/v1/trials?sex=FEMALE")
    assert resp_female.status_code == 200
    ids = {item["nct_id"] for item in resp_female.json()["items"]}
    assert "NCT00000001" in ids  # ALL
    assert "NCT00000002" in ids  # FEMALE
    assert "NCT00000004" not in ids  # MALE

    # Healthy volunteers: True
    resp_vol = client_warehouse.get("/v1/trials?healthy_volunteers=true")
    assert resp_vol.status_code == 200
    vol_ids = {item["nct_id"] for item in resp_vol.json()["items"]}
    assert "NCT00000002" in vol_ids
    assert "NCT00000004" in vol_ids
    assert "NCT00000001" not in vol_ids


def test_warehouse_search_filter_intervention_and_sponsor_class(
    client_warehouse: TestClient,
) -> None:
    # Intervention type: DEVICE
    resp_dev = client_warehouse.get("/v1/trials?intervention_type=DEVICE")
    assert resp_dev.status_code == 200
    items = resp_dev.json()["items"]
    assert len(items) == 1
    assert items[0]["nct_id"] == "NCT00000004"

    # Sponsor class: NIH
    resp_nih = client_warehouse.get("/v1/trials?sponsor_class=NIH")
    assert resp_nih.status_code == 200
    nih_ids = {item["nct_id"] for item in resp_nih.json()["items"]}
    assert "NCT00000001" in nih_ids  # Collaborator NIH
    assert "NCT00000004" in nih_ids  # Lead sponsor NIH


def test_warehouse_search_filter_updated_since(client_warehouse: TestClient) -> None:
    resp = client_warehouse.get("/v1/trials?updated_since=2026-06-01")
    assert resp.status_code == 200
    ids = {item["nct_id"] for item in resp.json()["items"]}
    assert "NCT00000001" in ids  # 2026-06-15
    assert "NCT00000004" in ids  # 2026-07-01
    assert "NCT00000002" not in ids  # 2026-05-10
    assert "NCT00000003" not in ids  # 2026-04-01


# --- 4. Geographic Distance & Proximity Tests ---


def test_warehouse_search_geo_proximity(client_warehouse: TestClient) -> None:
    # Query centered near San Francisco (37.7749, -122.4194) with 20 mi radius
    # UCSF Memory Center (37.7631, -122.4578) is ~2.3 mi away -> NCT00000001 matches
    # Other sites are in MA, NC, LA (>20 mi) -> excluded
    resp = client_warehouse.get("/v1/trials?lat=37.7749&lon=-122.4194&radius_mi=20")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["nct_id"] == "NCT00000001"
    nearest = items[0]["nearest_site"]
    assert nearest is not None
    assert nearest["facility_name"] == "UCSF Memory and Aging Center"
    assert nearest["city"] == "San Francisco"
    assert nearest["state"] == "CA"
    assert 1.0 <= nearest["distance_miles"] <= 5.0


def test_warehouse_search_sort_distance(client_warehouse: TestClient) -> None:
    # Query from California center (36.0, -120.0) with large radius
    resp = client_warehouse.get("/v1/trials?lat=36.0&lon=-120.0&radius_mi=500&sort=distance")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 2
    # Verify distance ordering ascending
    distances = [
        item["nearest_site"]["distance_miles"] for item in items if item.get("nearest_site")
    ]
    assert distances == sorted(distances)


def test_missing_coordinates_for_distance_sort(client_warehouse: TestClient) -> None:
    resp = client_warehouse.get("/v1/trials?sort=distance")
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "MISSING_COORDINATES_FOR_SORT"


# --- 5. Keyset Cursor Pagination Tests (Including NULL-safe Pagination) ---


def test_warehouse_keyset_cursor_pagination_updated(client_warehouse: TestClient) -> None:
    # Page 1: limit 2, default -updated sort
    resp1 = client_warehouse.get("/v1/trials?limit=2&sort=-updated")
    assert resp1.status_code == 200
    page1 = resp1.json()
    assert len(page1["items"]) == 2
    assert page1["pagination"]["has_more"] is True
    assert page1["pagination"]["total_count"] == 4
    cursor1 = page1["pagination"]["next_cursor"]
    assert cursor1 is not None

    # Page 2 using cursor
    resp2 = client_warehouse.get(f"/v1/trials?limit=2&sort=-updated&cursor={cursor1}")
    assert resp2.status_code == 200
    page2 = resp2.json()
    assert len(page2["items"]) == 2
    assert page2["pagination"]["has_more"] is False
    assert page2["pagination"]["next_cursor"] is None

    # Ensure no duplicate NCT IDs across pages
    page1_ids = [item["nct_id"] for item in page1["items"]]
    page2_ids = [item["nct_id"] for item in page2["items"]]
    assert set(page1_ids).isdisjoint(set(page2_ids))
    assert len(page1_ids + page2_ids) == 4


def test_keyset_pagination_with_null_dates_across_all_date_sorts(tmp_path: Path) -> None:
    """Test Critical C2: Pagination walking pages where sort dates are NULL across boundaries."""
    db_file = tmp_path / "null_dates_pagination.duckdb"
    con = duckdb.connect(str(db_file))

    con.execute("""
    CREATE TABLE dim_trial (
        nct_id VARCHAR,
        current_brief_title VARCHAR,
        current_overall_status VARCHAR,
        current_phase VARCHAR,
        current_study_type VARCHAR,
        current_lead_sponsor VARCHAR,
        start_date DATE,
        min_age_years DOUBLE,
        max_age_years DOUBLE,
        sex_eligibility VARCHAR,
        accepts_healthy_volunteers BOOLEAN,
        std_age_groups VARCHAR[],
        registry_url VARCHAR,
        latest_seen_snapshot_date DATE
    );

    CREATE TABLE fct_trial_snapshot (
        nct_id VARCHAR,
        snapshot_date DATE,
        snapshot_timestamp_utc TIMESTAMP,
        current_record_flag BOOLEAN
    );

    CREATE TABLE fct_trial_site (
        nct_id VARCHAR,
        facility_name VARCHAR
    );

    CREATE TABLE stg_trials (
        nct_id VARCHAR,
        ingestion_run_id VARCHAR,
        snapshot_id VARCHAR,
        snapshot_timestamp_utc TIMESTAMP,
        snapshot_date DATE,
        last_update_post_date DATE
    );

    CREATE TABLE stg_trial_locations (
        nct_id VARCHAR,
        ingestion_run_id VARCHAR,
        location_ordinal BIGINT,
        facility_name VARCHAR,
        city VARCHAR,
        city_normalized VARCHAR,
        state_raw VARCHAR,
        state_normalized VARCHAR,
        zip_code VARCHAR,
        latitude DOUBLE,
        longitude DOUBLE,
        location_status VARCHAR
    );

    CREATE TABLE stg_trial_conditions (
        nct_id VARCHAR,
        ingestion_run_id VARCHAR,
        condition_raw VARCHAR,
        condition_group VARCHAR,
        condition_normalized VARCHAR
    );

    CREATE TABLE stg_trial_sponsors (
        nct_id VARCHAR,
        ingestion_run_id VARCHAR,
        sponsor_name VARCHAR,
        sponsor_role VARCHAR,
        sponsor_class VARCHAR
    );

    CREATE TABLE stg_trial_interventions (
        nct_id VARCHAR,
        ingestion_run_id VARCHAR,
        intervention_name VARCHAR,
        intervention_type VARCHAR
    );
    """)

    # 5 trials: 2 with dates, 3 with NULL dates
    trials_meta = [
        ("NCT00000001", "2024-01-01", "2024-06-01"),
        ("NCT00000002", "2023-01-01", "2023-06-01"),
        ("NCT00000003", None, None),
        ("NCT00000004", None, None),
        ("NCT00000005", None, None),
    ]

    for nct, s_date, u_date in trials_meta:
        s_date_sql = f"'{s_date}'" if s_date else "NULL"
        u_date_sql = f"'{u_date}'" if u_date else "NULL"
        con.execute(f"""
        INSERT INTO dim_trial (
            nct_id, current_brief_title, current_overall_status, current_phase,
            current_study_type, current_lead_sponsor, start_date, registry_url,
            latest_seen_snapshot_date
        ) VALUES (
            '{nct}', 'Title {nct}', 'RECRUITING', 'PHASE3',
            'INTERVENTIONAL', 'Sponsor', {s_date_sql}, 'https://url', '2026-09-01'
        );

        INSERT INTO fct_trial_snapshot VALUES ('{nct}', '2026-09-01', '2026-09-01 12:00:00Z', true);

        INSERT INTO stg_trials (
            nct_id, ingestion_run_id, snapshot_id, snapshot_timestamp_utc,
            snapshot_date, last_update_post_date
        ) VALUES (
            '{nct}', 'run_01', 'snap_01', '2026-09-01 12:00:00Z',
            '2026-09-01', {u_date_sql}
        );
        """)

    con.close()

    mgr = DuckDBWarehouseManager(db_file)
    container = APIContainer(warehouse=mgr)
    app = create_app(container=container)

    with TestClient(app) as client:
        # Test all 4 date sorts across page boundaries with limit=2
        for sort_key in ("-updated", "updated", "-start_date", "start_date"):
            collected_ids: list[str] = []
            cursor: str | None = None
            for _ in range(10):  # At most 10 iterations to walk 5 items
                url = f"/v1/trials?limit=2&sort={sort_key}"
                if cursor:
                    url += f"&cursor={cursor}"
                resp = client.get(url)
                assert resp.status_code == 200, f"Sort {sort_key} failed on url {url}"
                data = resp.json()
                page_ids = [item["nct_id"] for item in data["items"]]
                collected_ids.extend(page_ids)
                cursor = data["pagination"]["next_cursor"]
                if not data["pagination"]["has_more"]:
                    break

            expected_ids = {
                "NCT00000001",
                "NCT00000002",
                "NCT00000003",
                "NCT00000004",
                "NCT00000005",
            }
            assert len(collected_ids) == 5, f"Sort {sort_key} dropped rows: {collected_ids}"
            assert set(collected_ids) == expected_ids, f"Sort {sort_key} missing rows"
            assert len(set(collected_ids)) == 5, f"Sort {sort_key} contained duplicates"

    mgr.close()


def test_invalid_cursor_handling(client_warehouse: TestClient) -> None:
    resp = client_warehouse.get("/v1/trials?cursor=invalid_not_base64_json")
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_CURSOR"


def test_cursor_sort_mismatch_handling(client_warehouse: TestClient) -> None:
    mismatched = encode_keyset_cursor(
        {"sort": "start_date", "v": "2024-01-01", "id": "NCT00000001"}
    )
    resp = client_warehouse.get(f"/v1/trials?sort=-updated&cursor={mismatched}")
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_CURSOR"
    assert "Cursor sort order" in body["detail"]


def test_cursor_malformed_types_handling(client_warehouse: TestClient) -> None:
    """Test Minor 6: crafted cursor with unexpected types does not raise 500."""
    bad_dist = encode_keyset_cursor(
        {"sort": "-updated", "v": "2024-01-01", "dist": "not_a_float", "id": "NCT00000001"}
    )
    resp = client_warehouse.get(f"/v1/trials?sort=-updated&cursor={bad_dist}")
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_CURSOR"


# --- 6. Protocol Detail & Privacy Tests ---


def test_warehouse_trial_detail_success(client_warehouse: TestClient) -> None:
    resp = client_warehouse.get("/v1/trials/NCT00000001?lat=37.7749&lon=-122.4194")
    assert resp.status_code == 200
    detail = TrialDetail(**resp.json())
    assert detail.nct_id == "NCT00000001"
    assert detail.overall_status == "RECRUITING"
    assert detail.study_design.phase_normalized == "PHASE3"
    assert detail.study_design.phases == ["Phase 3"]
    assert detail.enrollment.count == 350
    assert detail.dates.start_date == "2024-01-10"
    assert detail.sponsors.lead_sponsor.name == "NeuroPharm Inc"
    assert len(detail.conditions) >= 1
    assert len(detail.interventions) >= 1
    assert detail.eligibility.minimum_age_years == 55.0
    assert detail.eligibility.maximum_age_years == 85.0

    # Issue #67: lossless structural split of the criteria text
    sections = detail.eligibility.eligibility_sections
    assert sections is not None
    assert sections.parsed is True
    assert sections.inclusion == ["Age 55-85, confirmed early Alzheimer."]
    assert sections.exclusion == []
    assert sections.unclassified == []

    assert len(detail.sites) == 2

    # Verify site distance computation
    ucsf_site = next(s for s in detail.sites if "UCSF" in s.facility_name)
    assert ucsf_site.distance_miles is not None
    assert 1.0 <= ucsf_site.distance_miles <= 5.0

    # ADR 0065 strict compliance check
    assert (
        detail.registry_contact_url
        == "https://clinicaltrials.gov/study/NCT00000001#contacts-locations"
    )
    site_dict = ucsf_site.model_dump()
    for forbidden in ("investigator_name", "email", "phone", "contact_name", "contact_email"):
        assert forbidden not in site_dict


def test_warehouse_detail_sections_multi_item(
    warehouse_db: Path,
) -> None:
    """Issue #67: bulleted multi-item criteria split into inclusion/exclusion."""
    con = duckdb.connect(str(warehouse_db))
    con.execute(
        "UPDATE stg_trials SET eligibility_criteria_text = ? WHERE nct_id = 'NCT00000004'",
        [
            "Key Inclusion Criteria:\n\n"
            "* Men aged 18 to 50\n"
            "* Idiopathic Parkinson disease\n\n"
            "Exclusion Criteria (applies to all patients):\n"
            "* Prior ablative therapy\n"
        ],
    )
    con.close()

    mgr = DuckDBWarehouseManager(warehouse_db, max_connections=2, timeout=2.0)
    container = APIContainer(warehouse=mgr)
    app = create_app(container=container)
    with TestClient(app) as client:
        resp = client.get("/v1/trials/NCT00000004")
    mgr.close()

    assert resp.status_code == 200
    detail = TrialDetail(**resp.json())
    sections = detail.eligibility.eligibility_sections
    assert sections is not None
    assert sections.parsed is True
    assert sections.inclusion == ["Men aged 18 to 50", "Idiopathic Parkinson disease"]
    assert sections.exclusion == ["Prior ablative therapy"]
    assert sections.unclassified == []


def test_warehouse_detail_sections_api_payload_lossless_with_repeated_headings(
    warehouse_db: Path,
) -> None:
    """Issue #67: the API sections list is lossless and keeps repeated same-kind
    heading boundaries that the flat convenience lists merge away."""
    from src.transform.eligibility_sections import strip_bullet_markers

    criteria = (
        "Note: review all criteria with the study team.\n"
        "Baseline: Inclusion Criteria:\n"
        "* Age 18+\n"
        "\n"
        "Extension Phase: Inclusion Criteria:\n"
        "* Completed baseline\n"
        "Exclusion Criteria for Patients:\n"
        "* Prior therapy\n"
        "Inclusion Criteria for Cognitively Healthy Participants:\n"
        "* MoCA 26+\n"
    )
    con = duckdb.connect(str(warehouse_db))
    con.execute(
        "UPDATE stg_trials SET eligibility_criteria_text = ? WHERE nct_id = 'NCT00000004'",
        [criteria],
    )
    con.close()

    mgr = DuckDBWarehouseManager(warehouse_db, max_connections=2, timeout=2.0)
    container = APIContainer(warehouse=mgr)
    app = create_app(container=container)
    with TestClient(app) as client:
        resp = client.get("/v1/trials/NCT00000004")
    mgr.close()

    assert resp.status_code == 200
    detail = TrialDetail(**resp.json())
    sections = detail.eligibility.eligibility_sections
    assert sections is not None
    assert sections.parsed is True

    # Ordered groups with verbatim headings: repeated same-kind boundaries kept.
    assert [(s.kind, s.heading) for s in sections.sections] == [
        ("unclassified", None),
        ("inclusion", "Baseline: Inclusion Criteria:"),
        ("inclusion", "Extension Phase: Inclusion Criteria:"),
        ("exclusion", "Exclusion Criteria for Patients:"),
        ("inclusion", "Inclusion Criteria for Cognitively Healthy Participants:"),
    ]

    # Lossless at the API layer: heading + items per section rebuild the blob.
    rebuilt = "\n".join(
        line for s in sections.sections for line in ([s.heading] if s.heading else []) + s.items
    )
    assert rebuilt.split() == strip_bullet_markers(criteria).split()

    # Convenience lists merge across same-kind sections (no group semantics).
    assert sections.inclusion == ["Age 18+", "Completed baseline", "MoCA 26+"]
    assert sections.exclusion == ["Prior therapy"]
    assert sections.unclassified == ["Note: review all criteria with the study team."]


def test_warehouse_detail_sections_no_misclassification_or_group_loss(
    warehouse_db: Path,
) -> None:
    """Issue #67 review findings: mid-line exclusion heading, parenthetical
    heading, bulleted keyword non-heading, blank lines, nested bullets."""
    criteria = (
        "Inclusion Criteria:\n"
        "\n"
        "* 18 years old or over\n"
        "\n"
        "Paragraph one.\n"
        "\n"
        "Paragraph two.\n"
        "* can provide written informed consent. Exclusion Criteria: The patients who have\n"
        "* advanced cancer\n"
        "\n"
        "Exclusion Criteria (PCP): none\n"
        "* Prior exclusion from study:\n"
        "* Has one of:\n"
        "  * A\n"
        "  * B\n"
    )
    con = duckdb.connect(str(warehouse_db))
    con.execute(
        "UPDATE stg_trials SET eligibility_criteria_text = ? WHERE nct_id = 'NCT00000004'",
        [criteria],
    )
    con.close()

    mgr = DuckDBWarehouseManager(warehouse_db, max_connections=2, timeout=2.0)
    container = APIContainer(warehouse=mgr)
    app = create_app(container=container)
    with TestClient(app) as client:
        resp = client.get("/v1/trials/NCT00000004")
    mgr.close()

    assert resp.status_code == 200
    detail = TrialDetail(**resp.json())
    sections = detail.eligibility.eligibility_sections
    assert sections is not None
    assert sections.parsed is True
    assert [(s.kind, s.heading) for s in sections.sections] == [
        ("inclusion", "Inclusion Criteria:"),
        ("exclusion", "Exclusion Criteria:"),
        ("exclusion", "Exclusion Criteria (PCP):"),
    ]

    inclusion = sections.inclusion
    exclusion = sections.exclusion
    # Mid-line exclusion heading: its pre-text stays an inclusion item, the
    # following text is exclusion.
    assert "can provide written informed consent." in inclusion
    assert "The patients who have" in exclusion
    assert "advanced cancer" in exclusion
    assert "The patients who have" not in inclusion
    assert "advanced cancer" not in inclusion
    # Blank lines are ignored: consecutive non-bullet lines join into one item
    # (here, the continuation of the preceding item).
    assert "18 years old or over\nParagraph one.\nParagraph two." in inclusion
    # Parenthetical heading kept verbatim as a section heading.
    assert sections.sections[2].heading == "Exclusion Criteria (PCP):"
    # Bulleted criterion that merely ends in a colon is an item, not a heading.
    assert "Prior exclusion from study:" in exclusion
    assert "Prior exclusion from study:" not in {s.heading for s in sections.sections}
    # Nested sub-bullets flatten to sibling items (documented limitation).
    assert sections.sections[2].items == [
        "none",
        "Prior exclusion from study:",
        "Has one of:",
        "A",
        "B",
    ]
    assert exclusion[-5:] == sections.sections[2].items


def test_warehouse_trial_detail_null_criteria_yields_null_sections(
    warehouse_db: Path,
) -> None:
    """Issue #67: absent criteria text yields null sections, not empty lists."""
    con = duckdb.connect(str(warehouse_db))
    con.execute(
        "UPDATE stg_trials SET eligibility_criteria_text = NULL WHERE nct_id = 'NCT00000001'"
    )
    con.close()

    mgr = DuckDBWarehouseManager(warehouse_db, max_connections=2, timeout=2.0)
    container = APIContainer(warehouse=mgr)
    app = create_app(container=container)
    with TestClient(app) as client:
        resp = client.get("/v1/trials/NCT00000001")
    mgr.close()

    assert resp.status_code == 200
    detail = TrialDetail(**resp.json())
    assert detail.eligibility.criteria_text is None
    assert detail.eligibility.eligibility_sections is None


def test_warehouse_trial_detail_not_found(client_warehouse: TestClient) -> None:
    resp = client_warehouse.get("/v1/trials/NCT99999999")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "STUDY_NOT_FOUND"


def test_warehouse_trial_detail_invalid_nct_id(client_warehouse: TestClient) -> None:
    resp = client_warehouse.get("/v1/trials/not-an-nct")
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_NCT_ID"


def test_multi_ingestion_runs_child_records_not_duplicated(warehouse_db: Path) -> None:
    """Test Important I2: Child table joins must be scoped to active ingestion run."""
    con = duckdb.connect(str(warehouse_db))
    # Insert older run_00 child rows for NCT00000001
    con.execute("""
    INSERT INTO stg_trial_locations VALUES
    ('run_00', 'snap_20260801', '2026-08-01 12:00:00Z', 'page=00001.json', 1, 'adrd',
     'NCT00000001', 99, 'Old Duplicate Facility', 'old duplicate facility', 'Old City',
     'old city', 'CA', 'CA', '90000', 'United States', 'US', 37.0, -122.0,
     'RECRUITING', true, true, 'h_old');

    INSERT INTO stg_trial_conditions VALUES
    ('run_00', 'adrd', 'NCT00000001', 'Old Duplicate Condition',
     'old duplicate condition', 'Old Group', true, 1.0, 'hc_old');

    INSERT INTO stg_trial_sponsors VALUES
    ('run_00', 'adrd', 'NCT00000001', 'Old Sponsor Corp',
     'collaborator', 'INDUSTRY', 'old sponsor corp', 'hs_old');

    INSERT INTO stg_trial_interventions VALUES
    ('run_00', 'adrd', 'NCT00000001', 'Old Drug', 'DRUG',
     'Old description', 'old drug', 'hi_old');
    """)
    con.close()

    mgr = DuckDBWarehouseManager(warehouse_db)
    container = APIContainer(warehouse=mgr)
    with TestClient(create_app(container=container)) as client:
        resp = client.get("/v1/trials/NCT00000001")
        assert resp.status_code == 200
        detail = resp.json()

        # Sites: only run_01's 2 sites, not 3
        assert len(detail["sites"]) == 2
        assert not any("Old Duplicate Facility" in s["facility_name"] for s in detail["sites"])

        # Conditions: only run_01's 2 conditions, not 3
        assert len(detail["conditions"]) == 2
        assert not any(
            c["condition_raw"] == "Old Duplicate Condition" for c in detail["conditions"]
        )

        # Collaborators: only run_01's 1 collaborator, not 2
        assert len(detail["sponsors"]["collaborators"]) == 1
        assert not any(c["name"] == "Old Sponsor Corp" for c in detail["sponsors"]["collaborators"])

        # Interventions: only run_01's 1 intervention, not 2
        assert len(detail["interventions"]) == 1
        assert not any(i["intervention_name"] == "Old Drug" for i in detail["interventions"])
    mgr.close()


# --- 7. Caching Headers & ETag Validation (HTTP 304) ---


def test_etag_and_conditional_if_none_match(client_warehouse: TestClient) -> None:
    resp1 = client_warehouse.get("/v1/trials")
    assert resp1.status_code == 200
    etag = resp1.headers.get("ETag")
    assert etag is not None

    # Exact match
    resp2 = client_warehouse.get("/v1/trials", headers={"If-None-Match": etag})
    assert resp2.status_code == 304
    assert resp2.headers.get("ETag") == etag
    assert not resp2.content

    # Wildcard match
    resp_wildcard = client_warehouse.get("/v1/trials", headers={"If-None-Match": "*"})
    assert resp_wildcard.status_code == 304

    # Comma-separated match
    resp_comma = client_warehouse.get(
        "/v1/trials", headers={"If-None-Match": f'W/"mismatch", {etag}'}
    )
    assert resp_comma.status_code == 304


def test_etag_deterministic_stability(client_warehouse: TestClient) -> None:
    """Test Important I1: ETag must be completely deterministic across repeated calls."""
    etags: list[str] = []
    for _ in range(25):
        resp = client_warehouse.get("/v1/trials")
        assert resp.status_code == 200
        etags.append(resp.headers["ETag"])
    assert len(set(etags)) == 1, f"ETags differed across runs: {set(etags)}"


def test_warehouse_pool_saturation_returns_503_busy(warehouse_db: Path) -> None:
    """Test Important I4: Pool saturation returns 503 WAREHOUSE_BUSY with Retry-After."""
    mgr = DuckDBWarehouseManager(warehouse_db, max_connections=1, timeout=0.1)
    conn = mgr.pool.acquire()
    try:
        container = APIContainer(warehouse=mgr)
        with TestClient(create_app(container=container)) as client:
            # Search endpoint
            resp_search = client.get("/v1/trials")
            assert resp_search.status_code == 503
            assert resp_search.json()["code"] == "WAREHOUSE_BUSY"
            assert resp_search.headers.get("Retry-After") == "5"

            # Detail endpoint
            resp_detail = client.get("/v1/trials/NCT00000001")
            assert resp_detail.status_code == 503
            assert resp_detail.json()["code"] == "WAREHOUSE_BUSY"
            assert resp_detail.headers.get("Retry-After") == "5"
    finally:
        mgr.pool.release(conn)
        mgr.close()


def test_custom_request_id_preserved_and_capped(client_warehouse: TestClient) -> None:
    custom_req_id = "test-corr-id-12345"
    resp = client_warehouse.get("/v1/trials", headers={"X-Request-ID": custom_req_id})
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID") == custom_req_id

    # Test capped at 128 characters
    long_id = "a" * 200
    resp_long = client_warehouse.get("/v1/trials", headers={"X-Request-ID": long_id})
    assert resp_long.status_code == 200
    assert resp_long.headers.get("X-Request-ID") == long_id[:128]


def test_cors_credentials_false_when_wildcard(client_warehouse: TestClient) -> None:
    """Test Minor 3: allow_credentials is false when origins is wildcard."""
    resp = client_warehouse.options(
        "/v1/trials",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    # With wildcard origins and allow_credentials=False, allow-credentials header is omitted
    assert resp.headers.get("access-control-allow-credentials") is None


def test_warehouse_timestamps_consistent_under_non_utc_timezone(
    warehouse_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test Finding N1: timestamps remain exact UTC instant with Z suffix regardless of host TZ."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        mgr = DuckDBWarehouseManager(warehouse_db)
        # Verify connection pool initialized with UTC TimeZone setting
        with mgr.pool.connection() as conn:
            tz_val = conn.execute("SELECT current_setting('TimeZone');").fetchone()[0]
            assert tz_val == "UTC"

        container = APIContainer(warehouse=mgr)
        with TestClient(create_app(container=container)) as client:
            # 1. /readyz snapshot_timestamp_utc
            resp_ready = client.get("/readyz")
            assert resp_ready.status_code == 200
            assert resp_ready.json()["snapshot_timestamp_utc"] == "2026-09-01T12:00:00Z"

            # 2. Search Data-Updated header
            resp_search = client.get("/v1/trials")
            assert resp_search.status_code == 200
            assert resp_search.headers.get("Data-Updated") == "2026-09-01T12:00:00Z"

            # 3. Protocol detail provenance timestamps
            resp_detail = client.get("/v1/trials/NCT00000001")
            assert resp_detail.status_code == 200
            prov = resp_detail.json()["provenance"]
            assert prov["snapshot_timestamp_utc"] == "2026-09-01T12:00:00Z"
            assert prov["retrieved_at_utc"] == "2026-09-01T12:00:00Z"
    finally:
        mgr.close()
        monkeypatch.delenv("TZ", raising=False)
        if hasattr(time, "tzset"):
            time.tzset()
