"""Offline Census Gazetteer lookup tests (Issue #63).

Covers:
1. Postal-code -> GeoPoint resolution from the offline ZCTA centroid file.
2. DuckDB view registration with and without reference files present.
3. API postal_code search: equivalence with lat/lon, conflict and unknown-code
   problem responses, and the live backend passing the geographic filter upstream.
4. Warehouse search: ZIP/city centroid fallback for sites missing geoPoint
   (flagged approximate), recruiting-site-only radius filtering.
5. Detail endpoint per-site approximate flag.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pytest
from fastapi.testclient import TestClient

from src.api.app import APIContainer, create_app
from src.api.cache import CircuitBreaker, SQLiteCache
from src.api.gazetteer import register_gazetteer_views, resolve_postal_code
from src.api.warehouse import DuckDBWarehouseManager
from src.ingest.ctg_client import CTGClient
from tests.test_api_warehouse import _init_fixture_database

GAZETTEER_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gazetteer"
SEATTLE_ZCTA = (47.6104, -122.3343)


@pytest.fixture
def warehouse_db(tmp_path: Path) -> Path:
    """Same populated warehouse as tests/test_api_warehouse.py, per test."""
    db_file = tmp_path / "warehouse" / "clinical_trials.duckdb"
    _init_fixture_database(db_file)
    return db_file


@pytest.fixture
def gazetteer_warehouse_mgr(
    warehouse_db: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[DuckDBWarehouseManager, None, None]:
    """Warehouse client whose gazetteer views read the fixture CSVs.

    The directory is set via CTI_GAZETTEER_DIR so the endpoint's postal_code
    resolution and the pool's SQL views resolve the same reference files.
    """
    monkeypatch.setenv("CTI_GAZETTEER_DIR", str(GAZETTEER_FIXTURE_DIR))
    mgr = DuckDBWarehouseManager(warehouse_db, max_connections=4, timeout=2.0)
    yield mgr
    mgr.close()


@pytest.fixture
def client_gazetteer_warehouse(
    gazetteer_warehouse_mgr: DuckDBWarehouseManager,
) -> Generator[TestClient, None, None]:
    container = APIContainer(warehouse=gazetteer_warehouse_mgr)
    with TestClient(create_app(container=container)) as client:
        yield client


# --- 1. Postal code resolution ---


def test_resolve_postal_code_known() -> None:
    point = resolve_postal_code("98101", gazetteer_dir=GAZETTEER_FIXTURE_DIR)
    assert point is not None
    assert point.lat == pytest.approx(SEATTLE_ZCTA[0])
    assert point.lon == pytest.approx(SEATTLE_ZCTA[1])


def test_resolve_postal_code_unknown_and_malformed() -> None:
    assert resolve_postal_code("00000", gazetteer_dir=GAZETTEER_FIXTURE_DIR) is None
    assert resolve_postal_code("9810", gazetteer_dir=GAZETTEER_FIXTURE_DIR) is None
    assert resolve_postal_code("abcde", gazetteer_dir=GAZETTEER_FIXTURE_DIR) is None
    assert resolve_postal_code("9810 ", gazetteer_dir=GAZETTEER_FIXTURE_DIR) is None


# --- 2. DuckDB view registration ---


def test_gazetteer_views_without_reference_files(tmp_path: Path) -> None:
    conn = duckdb.connect()
    try:
        register_gazetteer_views(conn, tmp_path / "missing")
        assert conn.execute("SELECT count(*) FROM gazetteer_zcta").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM gazetteer_place").fetchone() == (0,)
        # Column shape stays stable so downstream SQL always binds.
        zcta_cols = [d[0] for d in conn.execute("SELECT * FROM gazetteer_zcta LIMIT 0").description]
        assert zcta_cols == ["zcta", "latitude", "longitude"]
    finally:
        conn.close()


def test_gazetteer_views_load_fixture_files() -> None:
    conn = duckdb.connect()
    try:
        register_gazetteer_views(conn, GAZETTEER_FIXTURE_DIR)
        rows = conn.execute(
            "SELECT zcta, latitude, longitude FROM gazetteer_zcta WHERE zcta = '98101'"
        ).fetchall()
        assert rows == [("98101", pytest.approx(SEATTLE_ZCTA[0]), pytest.approx(SEATTLE_ZCTA[1]))]
        place = conn.execute(
            "SELECT state_usps, city_name, city_name_stripped, latitude FROM gazetteer_place"
            " WHERE city_name = 'san francisco'"
        ).fetchall()
        assert place == [("CA", "san francisco", "san francisco", pytest.approx(37.7749))]
        # 2024-style names carry a type suffix ("Durham city"); the stripped
        # key matches registry cities that never carry one, alongside the
        # bare 2020-style row.
        suffixed = conn.execute(
            "SELECT city_name, city_name_stripped, latitude FROM gazetteer_place"
            " WHERE city_name_stripped = 'durham' ORDER BY city_name"
        ).fetchall()
        assert suffixed == [
            ("durham", "durham", pytest.approx(35.9886)),
            ("durham city", "durham", pytest.approx(35.9886)),
        ]
    finally:
        conn.close()


def test_tab_delimited_gazetteer_is_supported(tmp_path: Path) -> None:
    """The real Census Gazetteer ZCTA file is tab-separated."""
    (tmp_path / "gazetteer_zcta.csv").write_text(
        "GEOID\tALAND\tAWATER\tINTPTLAT\tINTPTLONG\n98101\t1964115\t0\t47.6104\t-122.3343\n",
        encoding="utf-8",
    )
    point = resolve_postal_code("98101", gazetteer_dir=tmp_path)
    assert point is not None
    assert point.lat == pytest.approx(SEATTLE_ZCTA[0])
    assert point.lon == pytest.approx(SEATTLE_ZCTA[1])


def test_corrupt_gazetteer_file_degrades_to_empty_views(tmp_path: Path) -> None:
    """A truncated download must not break warehouse connections (Issue #63)."""
    (tmp_path / "gazetteer_zcta.csv").write_bytes(b"GEOID\tINTPTLA")
    conn = duckdb.connect()
    try:
        register_gazetteer_views(conn, tmp_path)
        assert conn.execute("SELECT count(*) FROM gazetteer_zcta").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM gazetteer_place").fetchone() == (0,)
    finally:
        conn.close()


# --- 3. API postal_code parameter ---


def test_postal_code_search_matches_coordinates(
    client_gazetteer_warehouse: TestClient,
) -> None:
    by_postal = client_gazetteer_warehouse.get("/v1/trials?postal_code=94143&radius_mi=20")
    by_coords = client_gazetteer_warehouse.get("/v1/trials?lat=37.7631&lon=-122.4578&radius_mi=20")
    assert by_postal.status_code == 200
    assert by_postal.json() == by_coords.json()
    items = by_postal.json()["items"]
    assert [i["nct_id"] for i in items] == ["NCT00000001"]
    nearest = items[0]["nearest_site"]
    assert nearest["facility_name"] == "UCSF Memory and Aging Center"
    assert nearest["coordinate_approximate"] is False
    assert nearest["location_status"] == "RECRUITING"


def test_postal_code_conflicts_with_coordinates(
    client_gazetteer_warehouse: TestClient,
) -> None:
    resp = client_gazetteer_warehouse.get("/v1/trials?postal_code=98101&lat=47.6&lon=-122.33")
    assert resp.status_code == 400
    assert resp.json()["code"] == "CONFLICTING_LOCATION_PARAMS"


def test_postal_code_unknown_returns_400(client_gazetteer_warehouse: TestClient) -> None:
    resp = client_gazetteer_warehouse.get("/v1/trials?postal_code=00000")
    assert resp.status_code == 400
    assert resp.json()["code"] == "UNKNOWN_POSTAL_CODE"


def test_postal_code_invalid_format_returns_422(
    client_gazetteer_warehouse: TestClient,
) -> None:
    resp = client_gazetteer_warehouse.get("/v1/trials?postal_code=9810")
    assert resp.status_code == 422


def test_live_backend_postal_code_passes_geo_filter_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #63: the live backend forwards the geographic filter rather than
    filtering client-side; distance(lat,lon,radius) is the verified v2 syntax."""
    monkeypatch.setenv("CTI_GAZETTEER_DIR", str(GAZETTEER_FIXTURE_DIR))
    mock_client = MagicMock(spec=CTGClient)
    mock_client.fetch_studies.return_value = ({"studies": []}, "{}")
    cache = SQLiteCache(db_path=":memory:")
    container = APIContainer(ctg_client=mock_client, cache=cache, circuit_breaker=CircuitBreaker())
    try:
        with TestClient(create_app(container=container)) as client:
            resp = client.get("/v1/trials?postal_code=98101&radius_mi=50")
        assert resp.status_code == 200
        params = mock_client.fetch_studies.call_args[0][0]
        assert params["filter.geo"] == f"distance({SEATTLE_ZCTA[0]},{SEATTLE_ZCTA[1]},50.0mi)"
    finally:
        cache.close()


def test_live_backend_unknown_postal_code_returns_400() -> None:
    mock_client = MagicMock(spec=CTGClient)
    cache = SQLiteCache(db_path=":memory:")
    container = APIContainer(ctg_client=mock_client, cache=cache, circuit_breaker=CircuitBreaker())
    try:
        with TestClient(create_app(container=container)) as client:
            resp = client.get("/v1/trials?postal_code=00000")
        assert resp.status_code == 400
        assert resp.json()["code"] == "UNKNOWN_POSTAL_CODE"
        mock_client.fetch_studies.assert_not_called()
    finally:
        cache.close()


# --- 4. Warehouse fallback coordinates and recruiting-site filtering ---


def _add_seattle_site_without_geopoint(warehouse_db: Path) -> None:
    con = duckdb.connect(str(warehouse_db))
    con.execute("""
    INSERT INTO stg_trial_locations VALUES
    ('run_01', 'snap_20260901', '2026-09-01 12:00:00Z',
     'page=00001.json', 1, 'adrd', 'NCT00000004', 2,
     'Seattle Neuro Site', 'seattle neuro site',
     'Seattle', 'seattle',
     'WA', 'WA', '98101', 'United States', 'US',
     NULL, NULL, 'RECRUITING', true, true, 'h5');
    """)
    con.close()


def test_site_without_geopoint_uses_zip_fallback(
    warehouse_db: Path, client_gazetteer_warehouse: TestClient
) -> None:
    _add_seattle_site_without_geopoint(warehouse_db)
    resp = client_gazetteer_warehouse.get("/v1/trials?postal_code=98101&radius_mi=25")
    assert resp.status_code == 200
    items = resp.json()["items"]
    # NCT00000004 has a recruiting site near Seattle only via the ZIP centroid.
    nct4 = next(item for item in items if item["nct_id"] == "NCT00000004")
    nearest = nct4["nearest_site"]
    assert nearest["facility_name"] == "Seattle Neuro Site"
    assert nearest["location_status"] == "RECRUITING"
    assert nearest["coordinate_approximate"] is True
    assert nearest["distance_miles"] is not None and nearest["distance_miles"] <= 5.0


def test_detail_flags_fallback_coordinates(
    warehouse_db: Path, client_gazetteer_warehouse: TestClient
) -> None:
    _add_seattle_site_without_geopoint(warehouse_db)
    resp = client_gazetteer_warehouse.get("/v1/trials/NCT00000004?lat=47.6104&lon=-122.3343")
    assert resp.status_code == 200
    sites = {s["facility_name"]: s for s in resp.json()["sites"]}
    seattle = sites["Seattle Neuro Site"]
    assert seattle["coordinate_approximate"] is True
    assert seattle["distance_miles"] is not None and seattle["distance_miles"] <= 5.0
    ucla = sites["UCLA Health Center"]
    assert ucla["coordinate_approximate"] is False


def test_city_centroid_fallback_when_zip_missing(
    warehouse_db: Path, client_gazetteer_warehouse: TestClient
) -> None:
    con = duckdb.connect(str(warehouse_db))
    con.execute("""
    INSERT INTO stg_trial_locations VALUES
    ('run_01', 'snap_20260901', '2026-09-01 12:00:00Z',
     'page=00001.json', 1, 'adrd', 'NCT00000002', 2,
     'Boston Second Campus', 'boston second campus',
     'Boston', 'boston',
     'MA', 'MA', NULL, 'United States', 'US',
     NULL, NULL, 'RECRUITING', true, true, 'h6');
    """)
    con.close()
    resp = client_gazetteer_warehouse.get("/v1/trials?postal_code=02114&radius_mi=25")
    assert resp.status_code == 200
    items = {item["nct_id"]: item for item in resp.json()["items"]}
    # NCT00000002's Duke site is not recruiting; its Boston site exists only
    # through the city centroid fallback.
    nearest = items["NCT00000002"]["nearest_site"]
    assert nearest["facility_name"] == "Boston Second Campus"
    assert nearest["coordinate_approximate"] is True


def test_site_without_any_coordinates_excluded_from_radius(
    warehouse_db: Path, client_gazetteer_warehouse: TestClient
) -> None:
    con = duckdb.connect(str(warehouse_db))
    con.execute("""
    INSERT INTO stg_trial_locations VALUES
    ('run_01', 'snap_20260901', '2026-09-01 12:00:00Z',
     'page=00001.json', 1, 'oncology_nsclc', 'NCT00000003', 1,
     'Mystery Site', 'mystery site',
     'Nowhere', 'nowhere',
     'ZZ', 'UNKNOWN', NULL, 'United States', 'US',
     NULL, NULL, 'RECRUITING', true, true, 'h7');
    """)
    con.close()
    resp = client_gazetteer_warehouse.get("/v1/trials?lat=37.7749&lon=-122.4194&radius_mi=500")
    assert resp.status_code == 200
    assert "NCT00000003" not in {item["nct_id"] for item in resp.json()["items"]}


def test_only_recruiting_sites_count_for_available_near_me(
    client_gazetteer_warehouse: TestClient,
) -> None:
    """Acceptance: near Durham only trials with a *recruiting* site match.

    NCT00000002 lists Duke Health Clinic (Durham, 27710) but it is
    ACTIVE_NOT_RECRUITING, so nothing is available near Durham.
    """
    resp = client_gazetteer_warehouse.get("/v1/trials?postal_code=27710&radius_mi=25")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["pagination"]["total_count"] == 0


def test_non_recruiting_site_never_nearest(
    warehouse_db: Path, client_gazetteer_warehouse: TestClient
) -> None:
    con = duckdb.connect(str(warehouse_db))
    con.execute("""
    INSERT INTO stg_trial_locations VALUES
    ('run_01', 'snap_20260901', '2026-09-01 12:00:00Z',
     'page=00001.json', 1, 'adrd', 'NCT00000001', 3,
     'SF Inactive Annex', 'sf inactive annex',
     'San Francisco', 'san francisco',
     'CA', 'CA', '94143', 'United States', 'US',
     37.7631, -122.4578, 'ACTIVE_NOT_RECRUITING', true, true, 'h8');
    """)
    con.close()
    resp = client_gazetteer_warehouse.get("/v1/trials?lat=37.7631&lon=-122.4578&radius_mi=5")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    nearest = items[0]["nearest_site"]
    assert nearest["facility_name"] == "UCSF Memory and Aging Center"
    assert nearest["location_status"] == "RECRUITING"


def test_site_with_unknown_status_excluded(
    warehouse_db: Path, client_gazetteer_warehouse: TestClient
) -> None:
    con = duckdb.connect(str(warehouse_db))
    con.execute("""
    INSERT INTO stg_trial_locations VALUES
    ('run_01', 'snap_20260901', '2026-09-01 12:00:00Z',
     'page=00001.json', 1, 'adrd', 'NCT00000001', 4,
     'SF Status Unknown', 'sf status unknown',
     'San Francisco', 'san francisco',
     'CA', 'CA', '94143', 'United States', 'US',
     37.7631, -122.4578, NULL, true, true, 'h9');
    """)
    con.close()
    resp = client_gazetteer_warehouse.get("/v1/trials?lat=37.7631&lon=-122.4578&radius_mi=5")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


def test_postal_code_plus_condition_sorts_by_distance_by_default(
    warehouse_db: Path, client_gazetteer_warehouse: TestClient
) -> None:
    """Acceptance: '98101, 50 mi, NSCLC'-style queries (ZIP + radius + condition)
    return trials sorted by distance without an explicit sort param, nearest
    site and miles on each item."""
    con = duckdb.connect(str(warehouse_db))
    # A second dementia trial with a recruiting site ~11 mi from the 94143
    # centroid, plus a Dementia condition row so cond=dementia matches both it
    # and NCT00000001. Its update date is made NEWER than NCT00000001's, so
    # -updated order is the reverse of distance order and the default-sort
    # assertion below actually discriminates the distance behavior.
    con.execute("""
    INSERT INTO stg_trial_locations VALUES
    ('run_01', 'snap_20260901', '2026-09-01 12:00:00Z',
     'page=00001.json', 1, 'adrd', 'NCT00000002', 2,
     'East Bay Memory Clinic', 'east bay memory clinic',
     'Oakland', 'oakland',
     'CA', 'CA', '94612', 'United States', 'US',
     37.8044, -122.2712, 'RECRUITING', true, true, 'h10');
    """)
    con.execute(
        "UPDATE stg_trials SET last_update_post_date = DATE '2026-07-01'"
        " WHERE nct_id = 'NCT00000002'"
    )
    con.execute("""
    INSERT INTO stg_trial_conditions VALUES
    ('run_01', 'adrd', 'NCT00000002', 'Dementia',
     'dementia', 'Alzheimer Disease', true, 0.9, 'hc6');
    """)
    con.close()
    resp = client_gazetteer_warehouse.get("/v1/trials?postal_code=94143&radius_mi=50&cond=dementia")
    assert resp.status_code == 200
    items = resp.json()["items"]
    # Distance order: the nearer trial first. This is the reverse of -updated
    # order (NCT00000002 was updated more recently), asserted explicitly below.
    assert [i["nct_id"] for i in items] == ["NCT00000001", "NCT00000002"]
    distances = [i["nearest_site"]["distance_miles"] for i in items]
    assert distances == sorted(distances)
    assert all(i["nearest_site"]["location_status"] == "RECRUITING" for i in items)
    # The discriminating counterpart: with an explicit -updated the same query
    # returns the reverse order.
    by_update = client_gazetteer_warehouse.get(
        "/v1/trials?postal_code=94143&radius_mi=50&cond=dementia&sort=-updated"
    )
    assert [i["nct_id"] for i in by_update.json()["items"]] == ["NCT00000002", "NCT00000001"]
    # Explicit sort=distance via postal code no longer 422s either.
    explicit = client_gazetteer_warehouse.get(
        "/v1/trials?postal_code=94143&radius_mi=50&sort=distance"
    )
    assert explicit.status_code == 200
    assert [i["nct_id"] for i in explicit.json()["items"]] == ["NCT00000001", "NCT00000002"]


def test_ambiguous_city_name_resolves_one_fixed_centroid(
    warehouse_db: Path, client_gazetteer_warehouse: TestClient
) -> None:
    """The fixture has two same-named Springfield places in TX ~385 mi apart.
    A site in Springfield must resolve to one fixed centroid (same tie-break
    as detail.sql), never to whichever twin is closest to the user."""
    con = duckdb.connect(str(warehouse_db))
    con.execute("""
    INSERT INTO stg_trial_locations VALUES
    ('run_01', 'snap_20260901', '2026-09-01 12:00:00Z',
     'page=00001.json', 1, 'adrd', 'NCT00000002', 2,
     'Springfield Clinic', 'springfield clinic',
     'Springfield', 'springfield',
     'TX', 'TX', NULL, 'United States', 'US',
     NULL, NULL, 'RECRUITING', true, true, 'h11');
    """)
    con.close()
    # From near the northern twin: the site must not snap to the closer twin,
    # so the trial stays out of the radius.
    far = client_gazetteer_warehouse.get("/v1/trials?lat=36.5&lon=-101.0&radius_mi=25")
    assert far.status_code == 200
    assert "NCT00000002" not in {i["nct_id"] for i in far.json()["items"]}
    # Detail reports the same fixed centroid search uses (lowest latitude).
    detail = client_gazetteer_warehouse.get("/v1/trials/NCT00000002?lat=36.5&lon=-101.0")
    assert detail.status_code == 200
    site = next(s for s in detail.json()["sites"] if s["facility_name"] == "Springfield Clinic")
    assert site["coordinate_approximate"] is True
    assert site["coordinates"]["lat"] == pytest.approx(32.0)
    assert site["coordinates"]["lon"] == pytest.approx(-97.0)
    assert site["distance_miles"] is not None and site["distance_miles"] > 25
    # From near the fixed centroid the site is inside the radius.
    near = client_gazetteer_warehouse.get("/v1/trials?lat=32.0&lon=-97.0&radius_mi=25")
    assert near.status_code == 200
    assert "NCT00000002" in {i["nct_id"] for i in near.json()["items"]}
