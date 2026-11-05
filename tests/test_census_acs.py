"""Offline tests for the ACS population extract (Issue #71).

Fixtures under tests/fixtures/acs are verbatim header + selected rows of the Census
table-based Summary File (ACS 2020-2024 5-year). Nothing here touches the network.
"""

import csv
from collections import defaultdict
from pathlib import Path

import pytest

from src.ingest.census_acs import VINTAGE, build_rows, read_names, read_table

FIXTURES = Path(__file__).parent / "fixtures" / "acs"
SEED = Path(__file__).resolve().parents[1] / "dbt_clinical_trials" / "seeds"


def _lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def _extract() -> list[dict]:
    sex_age = read_table(_lines("acsdt5y2024-b01001.dat"), "B01001", {"25"}, {"02115"})
    race = read_table(_lines("acsdt5y2024-b03002.dat"), "B03002", {"25"}, {"02115"})
    names = read_names(_lines("Geos20245YR.txt"), sex_age.keys() & race.keys())
    return list(build_rows(sex_age, race, names))


def test_selection_keeps_states_chosen_counties_and_zctas_only() -> None:
    rows = _extract()
    geos = {(r["geo_level"], r["geo_id"]) for r in rows}
    assert geos == {
        ("state", "25"),
        ("state", "44"),
        ("state", "72"),
        ("county", "25001"),
        ("county", "25025"),
        ("zcta", "02115"),
    }  # 44007 (county outside --county-states) and the 04000A0US25 metro slice are dropped
    names = {r["geo_id"]: r["geo_name"] for r in rows}
    assert names["25025"] == "Suffolk County, Massachusetts"
    assert names["25"] == "Massachusetts"


def test_each_geography_reconciles_across_dimensions() -> None:
    by_geo: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    counts: dict[str, int] = defaultdict(int)
    for r in _extract():
        by_geo[r["geo_id"]][r["dimension"]] += r["population"]
        counts[r["geo_id"]] += 1
    for geo, dims in by_geo.items():
        assert dims["total"] == dims["age_sex"] == dims["race_ethnicity"] > 0, geo
        assert counts[geo] == 17  # 1 total + 4 age bands x 2 sexes + 8 race/ethnicity groups


def test_rows_carry_table_ids_and_vintage() -> None:
    rows = _extract()
    assert {r["vintage"] for r in rows} == {VINTAGE}
    assert {r["table_id"] for r in rows if r["dimension"] == "race_ethnicity"} == {"B03002"}
    assert {r["table_id"] for r in rows if r["dimension"] != "race_ethnicity"} == {"B01001"}


def test_age_band_is_dropped_when_a_component_estimate_is_missing() -> None:
    lines = _lines("acsdt5y2024-b01001.dat")
    header = lines[0].split("|")
    idx = header.index("B01001_E004")  # a line inside the 0-17 male band
    row = lines[1].split("|")
    row[idx] = "-555555555"
    sex_age = read_table([lines[0], "|".join(row)], "B01001", set(), set())
    rows = list(build_rows(sex_age, {row[0]: {1: 1}}, {row[0]: "x"}))
    bands = {(r["sex"], r["age_band"]) for r in rows if r["dimension"] == "age_sex"}
    assert ("male", "0-17") not in bands and ("female", "0-17") in bands


def test_wrong_table_file_is_rejected() -> None:
    with pytest.raises(ValueError, match="B03002_E001"):
        read_table(_lines("acsdt5y2024-b01001.dat"), "B03002", set(), set())


def test_committed_seed_reconciles_and_covers_all_states() -> None:
    with (SEED / "acs_population_extract.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_geo: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by_geo[(r["geo_level"], r["geo_id"])][r["dimension"]] += int(r["population"])
    assert sum(1 for level, _ in by_geo if level == "state") == 52  # 50 states + DC + PR
    for geo, dims in by_geo.items():
        assert dims["total"] == dims["age_sex"] == dims["race_ethnicity"] > 0, geo
    assert {r["vintage"] for r in rows} == {VINTAGE}
