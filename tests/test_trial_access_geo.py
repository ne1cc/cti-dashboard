"""Issue #72: trial access by geography (county centroids within 25/50/100 miles of open sites).

Unit tests parse tiny excerpts of the federal source files (tests/fixtures/access); the seed
tests guard the committed county context; the end-to-end test builds a dbt warehouse over
tests/fixtures/access_geo with a fixture Census Gazetteer and checks every rule on the marts,
including a hand-checked metro (Baltimore, MD) against an independent haversine.
"""

import csv
import math
import os
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from src.ingest import access_context
from tests.conftest import FIXTURE_SNAPSHOT_DAY
from tests.fixture_warehouse import FixtureRun, build_fixture_warehouse

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SEEDS = ROOT / "dbt_clinical_trials" / "seeds"


def _seed(name: str) -> list[dict[str, str]]:
    with (SEEDS / f"{name}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Independent great-circle distance (earth radius 3958.8 mi) for cross-checking the mart."""
    (la1, lo1), (la2, lo2) = (tuple(map(math.radians, p)) for p in (a, b))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 2 * 3958.8 * math.asin(math.sqrt(h))


# --- ingest -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def context_rows() -> dict[str, dict[str, object]]:
    rows = access_context.build_rows(
        FIXTURES / "access" / "gazetteer_counties.txt",
        FIXTURES / "access" / "svi_county.csv",
        FIXTURES / "access" / "ruca_tracts.csv",
        {"10", "24"},
    )
    return {str(r["county_fips"]): r for r in rows}


@pytest.mark.parametrize(
    ("code", "group"),
    [("1", "metropolitan"), ("3", "metropolitan"), ("4", "micropolitan"), ("6", "micropolitan"),
     ("7", "small_town"), ("9", "small_town"), ("10", "rural"), ("99", "not_coded")],
)  # fmt: skip
def test_ruca_primary_code_groups(code: str, group: str) -> None:
    assert access_context.ruca_group(code) == group


def test_context_keeps_requested_states_only(context_rows: dict[str, dict[str, object]]) -> None:
    assert sorted(context_rows) == ["10001", "10003", "24001"]  # Texas dropped
    kent = context_rows["10001"]
    assert (kent["latitude"], kent["longitude"]) == (39.097088, -75.502982)  # padded header trimmed
    assert kent["svi_release"] == "CDC/ATSDR SVI 2022"
    assert kent["ruca_release"] == "USDA ERS RUCA 2020"


def test_context_ruca_takes_population_dominant_group(
    context_rows: dict[str, dict[str, object]],
) -> None:
    kent = context_rows["10001"]
    assert (kent["ruca_group"], kent["ruca_group_pop_share"]) == ("metropolitan", 0.6)
    # 100 residents each: the tie goes to the more urban group.
    assert context_rows["10003"]["ruca_group"] == "metropolitan"
    assert context_rows["10003"]["ruca_group_pop_share"] == 0.5
    # A county whose only tract is zero-population has a group but no share to report.
    assert context_rows["24001"]["ruca_group"] == "not_coded"
    assert context_rows["24001"]["ruca_group_pop_share"] is None


def test_context_svi_missing_values_become_null(context_rows: dict[str, dict[str, object]]) -> None:
    assert context_rows["10001"]["svi_rpl_themes"] == 0.8151
    assert context_rows["10003"]["svi_rpl_themes"] is None  # -999 = not computed
    assert context_rows["24001"]["svi_rpl_themes"] is None  # blank


def test_context_csv_round_trip(tmp_path: Path, context_rows: dict[str, dict[str, object]]) -> None:
    out = tmp_path / "ctx.csv"
    access_context.write_csv(list(context_rows.values()), out)
    with out.open(encoding="utf-8", newline="") as f:
        assert list(csv.DictReader(f))[0].keys() == set(access_context.COLUMNS)


# --- committed seed -----------------------------------------------------------------


def test_seed_counties_match_acs_counties() -> None:
    acs = {r["geo_id"] for r in _seed("acs_population") if r["geo_level"] == "county"}
    assert {r["county_fips"] for r in _seed("county_access_context")} == acs


def test_seed_rows_are_complete_and_within_bounds() -> None:
    for r in _seed("county_access_context"):
        assert 24 < float(r["latitude"]) < 50 and -125 < float(r["longitude"]) < -66, r
        assert r["state_fips"] == r["county_fips"][:2]
        assert r["svi_release"] and r["ruca_release"]
        if r["svi_rpl_themes"]:
            assert 0 <= float(r["svi_rpl_themes"]) <= 1


# --- end to end ---------------------------------------------------------------------

BALTIMORE_JHH = (39.2965, -76.5922)  # exact registry geoPoint in the fixture
NEWARK_DE = (39.6865, -75.6935)
BURLINGTON_ZIP = (44.487609, -73.212052)  # fixture Gazetteer ZCTA 05401
PROVIDENCE_CITY = (41.823, -71.4187)  # fixture Gazetteer place


def _run() -> FixtureRun:
    return FixtureRun(
        profile="adrd",
        run_id="20260901T150000Z_fixtureaccess",
        started_at=FIXTURE_SNAPSHOT_DAY + timedelta(hours=15),
        condition="Alzheimer Disease",
        fixture_dir="access_geo",
    )


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    previous = os.environ.get("CTI_GAZETTEER_DIR")
    os.environ["CTI_GAZETTEER_DIR"] = str(FIXTURES / "access_geo" / "gazetteer")
    try:
        return build_fixture_warehouse(tmp_path_factory.mktemp("access_warehouse"), runs=[_run()])
    finally:
        if previous is None:
            del os.environ["CTI_GAZETTEER_DIR"]
        else:
            os.environ["CTI_GAZETTEER_DIR"] = previous


def _frame(root: Path, sql: str) -> list[dict[str, object]]:
    con = duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True)
    start = Path.cwd()
    try:
        os.chdir(root)
        return con.execute(sql).df().to_dict("records")  # type: ignore[no-any-return]
    finally:
        os.chdir(start)
        con.close()


@pytest.fixture(scope="module")
def geo(warehouse: Path) -> dict[tuple[str, str], dict[str, object]]:
    rows = _frame(warehouse, "select * from main_marts.mart_trial_access_by_geo")
    return {(str(r["mesh_id"]), str(r["county_fips"])): r for r in rows}


@pytest.fixture(scope="module")
def strata(warehouse: Path) -> list[dict[str, object]]:
    return _frame(warehouse, "select * from main_marts.mart_trial_access_by_stratum")


def test_every_node_covers_every_seed_county(geo: dict[tuple[str, str], dict[str, object]]) -> None:
    n = len(_seed("county_access_context"))
    assert {m for m, _ in geo} == {"D000544", "D003704", "D020961"}
    for mesh in {m for m, _ in geo}:
        assert sum(1 for m, _ in geo if m == mesh) == n


def test_baltimore_metro_hand_checked(geo: dict[tuple[str, str], dict[str, object]]) -> None:
    # Johns Hopkins (exact geoPoint) is about a mile from the Baltimore city centroid.
    row = geo[("D000544", "24510")]
    county = next(r for r in _seed("county_access_context") if r["county_fips"] == "24510")
    expected = _miles(BALTIMORE_JHH, (float(county["latitude"]), float(county["longitude"])))
    assert expected < 3
    assert row["nearest_site_miles"] == pytest.approx(round(expected, 1), abs=0.05)
    assert (row["within_25mi"], row["within_50mi"], row["within_100mi"]) == (True, True, True)
    assert row["population"] == next(
        int(r["population"])
        for r in _seed("acs_population")
        if r["geo_id"] == "24510" and r["sex"] == r["age_band"] == r["race_ethnicity"] == "all"
        and r["table_id"] == "B01001"
    )  # fmt: skip
    assert (row["state_code"], row["ruca_group"]) == ("MD", "metropolitan")


def test_distance_rings_match_independent_haversine(
    geo: dict[tuple[str, str], dict[str, object]],
) -> None:
    # Usable Alzheimer sites: Baltimore (exact), Burlington (ZIP), Providence (city).
    sites = [BALTIMORE_JHH, BURLINGTON_ZIP, PROVIDENCE_CITY]
    for (mesh, fips), row in geo.items():
        if mesh != "D000544":
            continue
        county = next(r for r in _seed("county_access_context") if r["county_fips"] == fips)
        d = [_miles(s, (float(county["latitude"]), float(county["longitude"]))) for s in sites]
        assert row["sites_within_25mi"] == sum(x <= 25 for x in d), fips
        assert row["sites_within_50mi"] == sum(x <= 50 for x in d), fips
        assert row["sites_within_100mi"] == sum(x <= 100 for x in d), fips
        if min(d) <= 100:
            assert row["nearest_site_miles"] == pytest.approx(round(min(d), 1), abs=0.05), fips
        else:
            assert row["nearest_site_miles"] is None or math.isnan(
                float(str(row["nearest_site_miles"]))
            )


def test_delaware_counties_sit_outside_50_miles_of_baltimore(
    geo: dict[tuple[str, str], dict[str, object]],
) -> None:
    for fips in ("10001", "10003", "10005"):
        row = geo[("D000544", fips)]
        assert row["within_100mi"] is True and row["within_50mi"] is False, fips
    # Allegany County is more than 100 miles from every Alzheimer site.
    assert geo[("D000544", "24001")]["within_100mi"] is False


def test_zip_and_city_fallbacks_place_sites_but_are_counted_as_approximate(
    geo: dict[tuple[str, str], dict[str, object]],
) -> None:
    row = geo[("D000544", "44007")]  # Providence County: the city-centroid site is nearby
    assert row["within_25mi"] is True
    assert geo[("D000544", "50007")]["within_25mi"] is True  # Chittenden: the ZIP-centroid site
    ad = geo[("D000544", "24510")]
    assert (ad["sites_total"], ad["sites_exact"], ad["sites_zip_approx"]) == (4, 1, 1)
    assert (ad["sites_city_approx"], ad["sites_excluded"]) == (1, 1)
    assert ad["exact_coordinate_share"] == pytest.approx(1 / 3, abs=1e-4)
    assert ad["coverage_confidence"] == "low"


def test_closed_site_and_suspended_trial_are_not_open_sites(
    geo: dict[tuple[str, str], dict[str, object]],
) -> None:
    # The COMPLETED Dover site and the SUSPENDED trial's Newport site would reach Kent / RI.
    assert geo[("D000544", "10001")]["sites_within_25mi"] == 0
    assert geo[("D000544", "44007")]["sites_within_100mi"] == 1  # Providence only, not Newport too


def test_parent_node_rolls_up_descendant_sites(
    geo: dict[tuple[str, str], dict[str, object]],
) -> None:
    # Dementia (D003704) adds the Lewy body trial's exact Newark, DE site.
    parent = geo[("D003704", "10003")]
    assert parent["sites_total"] == 5 and parent["sites_exact"] == 2
    assert parent["sites_within_25mi"] == 1
    child = geo[("D020961", "10003")]
    assert (child["sites_total"], child["coverage_confidence"]) == (1, "high")
    assert child["nearest_site_miles"] == pytest.approx(
        round(_miles(NEWARK_DE, (39.577, -75.644)), 1), abs=0.3
    )


def test_strata_partition_the_overall_population(strata: list[dict[str, object]]) -> None:
    for mesh in ("D000544", "D003704", "D020961"):
        node = [r for r in strata if r["mesh_id"] == mesh]
        (overall,) = [r for r in node if r["stratum_type"] == "overall"]
        for kind in ("ruca_group", "svi_quartile"):
            parts = [r for r in node if r["stratum_type"] == kind]
            for col in ("population", "population_within_50mi", "county_count"):
                assert sum(int(str(r[col])) for r in parts) == overall[col], (mesh, kind, col)
        for r in node:
            assert 0 <= float(str(r["share_within_25mi"])) <= float(str(r["share_within_50mi"]))
            assert float(str(r["share_within_50mi"])) <= float(str(r["share_within_100mi"])) <= 1


def test_overall_share_is_population_weighted(
    geo: dict[tuple[str, str], dict[str, object]], strata: list[dict[str, object]]
) -> None:
    rows = [r for (m, _), r in geo.items() if m == "D000544"]
    total = sum(int(str(r["population"])) for r in rows)
    within = sum(int(str(r["population"])) for r in rows if r["within_50mi"])
    (overall,) = [r for r in strata if r["mesh_id"] == "D000544" and r["stratum_type"] == "overall"]
    assert overall["population_within_50mi"] == within
    assert overall["share_within_50mi"] == pytest.approx(round(within / total, 4))
    assert overall["coverage_confidence"] == "low"
    assert "docs/access_methodology.md" in str(overall["access_basis"])
