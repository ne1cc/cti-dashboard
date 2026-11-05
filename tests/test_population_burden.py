"""Issue #71: ACS population denominators, burden extracts and the access-rate mart.

Unit tests parse small excerpts of the real source files (tests/fixtures/acs,
tests/fixtures/burden); no test touches the network. The seed tests guard the
committed extracts, and the end-to-end test builds a dbt warehouse over
tests/fixtures/burden_rates (NSCLC, type 2 diabetes, ADRD and one unmapped
condition) and checks every denominator rule on the mart.
"""

import csv
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.ingest import acs_population, burden_extracts
from tests.conftest import FIXTURE_SNAPSHOT_DAY
from tests.fixture_warehouse import FixtureRun, build_fixture_warehouse

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SEEDS = ROOT / "dbt_clinical_trials" / "seeds"

sys.path.insert(0, str(ROOT / "dashboard"))
from components.access_rates import NO_BURDEN, PER_1K_CASES, PER_100K, rate_view  # noqa: E402


def _seed(name: str) -> list[dict[str, str]]:
    with (SEEDS / f"{name}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --- ACS ingest ---------------------------------------------------------------


@pytest.fixture(scope="module")
def acs_rows() -> list[dict[str, object]]:
    return acs_population.build_rows(FIXTURES / "acs", 2024, {"10"}, {"19711"})


def _pop(rows: list[dict[str, object]], level: str, geo_id: str, **dims: str) -> int:
    want = {"sex": "all", "age_band": "all", "race_ethnicity": "all", **dims}
    (hit,) = [
        r
        for r in rows
        if r["geo_level"] == level
        and r["geo_id"] == geo_id
        and all(r[k] == v for k, v in want.items())
    ]
    assert isinstance(hit["population"], int)
    return hit["population"]


def test_acs_subset_levels_and_vintage(acs_rows: list[dict[str, object]]) -> None:
    keys = {(r["geo_level"], r["geo_id"]) for r in acs_rows}
    # Nation row and a county outside the requested states are filtered out.
    assert keys == {("state", "10"), ("county", "10001"), ("zcta", "19711")}
    assert {r["vintage"] for r in acs_rows} == {"ACS 2020-2024 5-year"}
    assert {r["table_id"] for r in acs_rows} == {"B01001", "B03002"}
    names = {r["geo_id"]: r["geo_name"] for r in acs_rows}
    assert names["10"] == "Delaware"
    assert names["10001"] == "Kent County, Delaware"
    assert all(r["state_fips"] == "" for r in acs_rows if r["geo_level"] == "zcta")
    assert all(r["state_fips"] == "10" for r in acs_rows if r["geo_level"] != "zcta")


@pytest.mark.parametrize("geo", [("state", "10"), ("county", "10001"), ("zcta", "19711")])
def test_acs_breakdowns_add_up_to_total(
    acs_rows: list[dict[str, object]], geo: tuple[str, str]
) -> None:
    level, geo_id = geo
    total = _pop(acs_rows, level, geo_id)
    assert (
        _pop(acs_rows, level, geo_id, sex="male") + _pop(acs_rows, level, geo_id, sex="female")
        == total
    )
    bands = ["0-17", "18-44", "45-64", "65+"]
    assert sum(_pop(acs_rows, level, geo_id, age_band=b) for b in bands) == total
    for sex in ("male", "female"):
        assert sum(_pop(acs_rows, level, geo_id, sex=sex, age_band=b) for b in bands) == _pop(
            acs_rows, level, geo_id, sex=sex
        )
    races = {r["race_ethnicity"] for r in acs_rows if r["table_id"] == "B03002"}
    assert len(races) == 7
    assert sum(_pop(acs_rows, level, geo_id, race_ethnicity=r) for r in races) == total


def test_acs_total_matches_source_file(acs_rows: list[dict[str, object]]) -> None:
    with (FIXTURES / "acs" / "acsdt5y2024-b01001.dat").open(encoding="utf-8") as f:
        raw = {r["GEO_ID"]: r for r in csv.DictReader(f, delimiter="|")}
    assert _pop(acs_rows, "state", "10") == int(raw["0400000US10"]["B01001_E001"])
    assert _pop(acs_rows, "zcta", "19711") == int(raw["860Z200US19711"]["B01001_E001"])


def test_acs_unavailable_sentinel_is_null() -> None:
    assert acs_population._num("-555555555") is None
    assert acs_population._num("") is None
    assert acs_population._num("12") == 12


# --- burden extract parsers -----------------------------------------------------


def test_parse_places_keeps_county_crude_prevalence_only() -> None:
    records = json.loads((FIXTURES / "burden" / "places_de.json").read_text(encoding="utf-8"))
    ignored = [
        {**records[0], "measureid": "OBESITY"},
        {**records[0], "datavaluetypeid": "AgeAdjPrv"},
    ]
    rows = burden_extracts.parse_places(records + ignored)
    assert len(rows) == 6  # 3 Delaware counties x DIABETES and COGNITION
    kent = [r for r in rows if r["county_fips"] == "10001" and r["measure_id"] == "DIABETES"]
    assert kent[0]["prevalence_pct"] == "14.3"
    assert kent[0]["release"] == "PLACES 2025 release"


def test_parse_scp_drops_us_row_and_maps_registry_program() -> None:
    text = (FIXTURES / "burden" / "scp_lung_sample.csv").read_bytes().decode("latin-1")
    rows = {r["state_fips"]: r for r in burden_extracts.parse_scp(text)}
    assert set(rows) == {"21", "24", "49", "53", "72"}  # US aggregate row dropped
    assert rows["24"]["annual_cases"] == "3864"
    assert rows["24"]["state_name"] == "Maryland"
    assert [rows[k]["registry_program"] for k in ("21", "24", "53")] == [
        "SEER",
        "NPCR",
        "SEER+NPCR",
    ]
    assert rows["24"]["period"] == "2019-2023"


def test_parse_scp_suppressed_values_stay_empty() -> None:
    text = (
        'State,FIPS,"rate","lo","hi","r","rl","ru",Average Annual Count,x\n'
        '"Nowhere(2)",99000,* ,* , *,N/A,N/A,N/A,* ,falling\n'
    )
    (row,) = burden_extracts.parse_scp(text)
    assert row["annual_cases"] == "" and row["age_adjusted_rate_per_100k"] == ""


# --- committed seeds ------------------------------------------------------------


def test_acs_seed_covers_every_state_and_adds_up() -> None:
    state_pop = {r["fips_code"] for r in _seed("state_population")}
    rows = _seed("acs_population")
    states = {r["geo_id"] for r in rows if r["geo_level"] == "state"}
    assert states == state_pop  # 50 states + DC + PR
    counties = {r["geo_id"] for r in rows if r["geo_level"] == "county"}
    assert {c[:2] for c in counties} == {"10", "24", "44", "50"}
    assert len(counties) == 3 + 24 + 5 + 14
    totals = {
        (r["geo_level"], r["geo_id"]): int(r["population"])
        for r in rows
        if r["sex"] == r["age_band"] == r["race_ethnicity"] == "all" and r["table_id"] == "B01001"
    }
    assert all(v >= 0 for v in totals.values())  # ZCTA 21287 is a hospital campus: 0 residents
    for key, total in totals.items():
        bands = sum(
            int(r["population"])
            for r in rows
            if (r["geo_level"], r["geo_id"]) == key and r["sex"] == "all" and r["age_band"] != "all"
        )
        assert bands == total, key


def test_places_extract_is_complete_for_each_state_it_covers() -> None:
    acs_counties = {r["geo_id"] for r in _seed("acs_population") if r["geo_level"] == "county"}
    places = _seed("burden_places_county")
    for measure in ("DIABETES", "COGNITION"):
        got = {r["county_fips"] for r in places if r["measure_id"] == measure}
        assert got == acs_counties, measure
    assert {r["release"] for r in places} == {"PLACES 2025 release"}


def test_scp_extract_has_a_row_per_state() -> None:
    rows = _seed("burden_scp_lung_state")
    assert {r["state_fips"] for r in rows} == {r["fips_code"] for r in _seed("state_population")}
    assert all(r["annual_cases"] for r in rows)


def test_condition_burden_map_rules() -> None:
    rows = {r["mesh_id"]: r for r in _seed("condition_burden_map")}
    assert len(rows) == len(_seed("condition_burden_map"))
    flags = {k: r["denominator_available"] for k, r in rows.items()}
    assert flags["D002289"] == flags["D003924"] == "true"  # NSCLC, type 2 diabetes
    assert flags["D000544"] == flags["D003704"] == "false"  # ADRD fallback path
    assert "cognitive" in rows["D000544"]["burden_measure"].lower()
    for r in rows.values():
        assert r["denominator_note"]
        if r["denominator_available"] == "true":
            assert r["burden_source"] and r["burden_vintage"] and r["burden_source_id"]
        else:
            assert r["denominator_available"] == "false"
    sources = {r["burden_source_id"] for r in rows.values() if r["denominator_available"] == "true"}
    assert sources == {"scp_lung_state", "places_diabetes_county"}


# --- dashboard rule ---------------------------------------------------------------


def _frame(**overrides: object) -> pd.DataFrame:
    base = {
        "state_code": ["MD", "CA"],
        "population_vintage": ["ACS 2020-2024 5-year"] * 2,
        "burden_denominator_available": [True, False],
        "burden_measure": ["Adults with diagnosed diabetes"] * 2,
        "burden_source": ["CDC PLACES"] * 2,
        "burden_vintage": ["PLACES 2025 release"] * 2,
        PER_100K: [1.0, 2.0],
        PER_1K_CASES: [0.5, None],
    }
    return pd.DataFrame({**base, **overrides})


def test_rate_view_per_100k_states_its_denominator() -> None:
    shown, column, caption = rate_view(_frame(), per_case=False)
    assert column == PER_100K and len(shown) == 2
    assert "per 100,000 residents (ACS 2020-2024 5-year)" in caption


def test_rate_view_per_case_drops_uncovered_states_and_says_so() -> None:
    shown, column, caption = rate_view(_frame(), per_case=True)
    assert column == PER_1K_CASES and shown["state_code"].tolist() == ["MD"]
    assert "CDC PLACES, PLACES 2025 release" in caption
    assert f"1 state(s) have {NO_BURDEN}" in caption


def test_rate_view_without_any_burden_falls_back_to_population_rate_never_counts() -> None:
    frame = _frame(burden_denominator_available=[False, False], **{PER_1K_CASES: [None, None]})
    shown, column, caption = rate_view(frame, per_case=True)
    assert column == PER_100K and len(shown) == 2
    assert caption.startswith("No burden denominator available")
    assert "per 100,000 residents" in caption


# --- end to end ---------------------------------------------------------------------


def _warehouse_run() -> FixtureRun:
    return FixtureRun(
        profile="adrd",
        run_id="20260901T140000Z_fixtureburden",
        started_at=FIXTURE_SNAPSHOT_DAY + timedelta(hours=14),
        condition="Alzheimer Disease",
        fixture_dir="burden_rates",
    )


@pytest.fixture(scope="module")
def mart(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], dict[str, object]]:
    root = build_fixture_warehouse(
        tmp_path_factory.mktemp("burden_warehouse"), runs=[_warehouse_run()]
    )
    con = duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True)
    start = Path(os.getcwd())
    try:
        os.chdir(root)
        frame = con.execute("select * from main_marts.mart_condition_access_rates").df()
    finally:
        os.chdir(start)
        con.close()
    return {(r["mesh_id"], r["state_code"]): r for r in frame.to_dict("records")}


def test_nsclc_cites_state_cancer_profiles_and_computes_both_rates(mart) -> None:
    md_cases = int(
        next(r for r in _seed("burden_scp_lung_state") if r["state_fips"] == "24")["annual_cases"]
    )
    md_pop = _pop_total("24")
    row = mart[("D002289", "MD")]
    assert row["recruiting_trial_count"] == 1
    assert row["burden_denominator_available"]
    assert row["estimated_cases"] == md_cases
    assert row["recruiting_trials_per_1k_est_cases"] == pytest.approx(round(1000 / md_cases, 4))
    assert row["recruiting_trials_per_100k"] == pytest.approx(round(100000 / md_pop, 4))
    assert "State Cancer Profiles" in row["burden_source"] and row["burden_vintage"].startswith(
        "2019-2023"
    )
    assert "per 1,000 estimated cases" in row["denominator_label"]
    assert mart[("D002289", "CA")]["burden_denominator_available"]  # SEER state, full coverage


def test_type2_diabetes_sums_places_counties_and_gaps_outside_extract(mart) -> None:
    expected = sum(
        float(r["prevalence_pct"]) / 100 * int(r["adult_population"])
        for r in _seed("burden_places_county")
        if r["measure_id"] == "DIABETES" and r["state_code"] == "MD"
    )
    row = mart[("D003924", "MD")]
    assert row["burden_denominator_available"]
    assert row["estimated_cases"] == pytest.approx(round(expected))
    assert "CDC PLACES" in row["burden_source"] and "BRFSS 2023" in row["burden_vintage"]
    ca = mart[("D003924", "CA")]  # CA is not in the committed PLACES extract
    assert not ca["burden_denominator_available"]
    assert pd.isna(ca["estimated_cases"]) and pd.isna(ca["recruiting_trials_per_1k_est_cases"])
    assert not pd.isna(ca["recruiting_trials_per_100k"])
    assert NO_BURDEN in ca["denominator_label"]


@pytest.mark.parametrize("mesh_id", ["D000544", "D005621"])  # ADRD fallback, unmapped condition
def test_no_burden_means_population_rate_only(mart, mesh_id: str) -> None:
    for state in ("MD", "CA"):
        row = mart[(mesh_id, state)]
        assert not row["burden_denominator_available"]
        assert pd.isna(row["estimated_cases"]) and pd.isna(
            row["recruiting_trials_per_1k_est_cases"]
        )
        assert not pd.isna(row["recruiting_trials_per_100k"])
        assert NO_BURDEN in row["denominator_label"]


def test_adrd_row_labels_the_proxy_as_not_diagnosed_dementia(mart) -> None:
    assert "not diagnosed dementia" in mart[("D000544", "MD")]["burden_measure"]
    assert "cognitive disability" in mart[("D000544", "MD")]["burden_source"].lower()


def test_every_mart_row_states_a_denominator(mart) -> None:
    assert mart
    for row in mart.values():
        assert row["denominator_label"]
        assert "per 100,000 residents" in row["denominator_label"]
        if not row["burden_denominator_available"]:
            assert pd.isna(row["recruiting_trials_per_1k_est_cases"])


def _pop_total(fips: str) -> int:
    return next(
        int(r["population"])
        for r in _seed("acs_population")
        if r["geo_level"] == "state"
        and r["geo_id"] == fips
        and r["sex"] == r["age_band"] == r["race_ethnicity"] == "all"
        and r["table_id"] == "B01001"
    )
