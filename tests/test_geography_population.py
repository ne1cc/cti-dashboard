"""Tests for state population denominator seed and per-100k rate calculations."""

import csv
from dataclasses import replace
from pathlib import Path

import duckdb
import yaml

from tests.fixture_warehouse import AddedStudy, base_adrd_run, build_fixture_warehouse

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "dbt_clinical_trials" / "seeds" / "state_population.csv"
GEO_RULES_PATH = ROOT / "config" / "geography_rules.yml"


def _load_seed_rows() -> list[dict[str, str]]:
    """Read the committed denominator rows; propagate missing or invalid CSV errors."""
    with open(SEED_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def test_state_population_seed_integrity() -> None:
    """The committed Census seed must hold 52 rows (50 states + DC + PR) with valid schema."""
    rows = _load_seed_rows()
    assert len(rows) == 52, f"Expected exactly 52 rows, got {len(rows)}"

    state_codes = [r["state_code"] for r in rows]
    fips_codes = [r["fips_code"] for r in rows]

    assert len(set(state_codes)) == 52, "Duplicate state_code found in seed"
    assert len(set(fips_codes)) == 52, "Duplicate fips_code found in seed"

    assert "DC" in state_codes, "DC missing from state population seed"
    assert "PR" in state_codes, "PR missing from state population seed"
    assert "CA" in state_codes, "CA missing from state population seed"
    assert "WY" in state_codes, "WY missing from state population seed"

    for r in rows:
        assert len(r["state_code"]) == 2 and r["state_code"].isupper()
        assert len(r["fips_code"]) == 2 and r["fips_code"].isdigit()
        assert r["state_name"].strip() != ""
        pop = int(r["population"])
        assert pop > 0, f"Nonpositive population for {r['state_code']}: {pop}"
        assert r["estimate_date"] == "2024-07-01"
        assert r["vintage"] == "Vintage 2024"
        assert r["source_url"].startswith("https://www2.census.gov/")

    # National and regional summary codes must not be present
    assert "US" not in state_codes
    assert "00" not in fips_codes


def test_seed_matches_geography_rules() -> None:
    """All seed state codes and names must conform to config/geography_rules.yml."""
    geo_rules = yaml.safe_load(GEO_RULES_PATH.read_text(encoding="utf-8"))
    valid_abbrs = set(geo_rules["valid_state_abbreviations"])
    name_to_abbr = geo_rules["state_name_to_abbreviation"]

    rows = _load_seed_rows()
    for r in rows:
        assert r["state_code"] in valid_abbrs, f"{r['state_code']} not in valid_state_abbreviations"
        assert name_to_abbr[r["state_name"].lower()] == r["state_code"], (
            f"State name {r['state_name']} mapped to wrong code"
        )


def _compute_per_100k(recruiting_count: int, population: int | None) -> float | None:
    """Reference calculation matching mart_condition_geography_trends SQL formula."""
    if population is None or population <= 0:
        return None
    return round(100000.0 * recruiting_count / population, 4)


def test_per_100k_synthetic_calculation() -> None:
    """Small states with fewer raw trials can rank higher per-100k than large states."""
    # Wyoming: population 587,618 (from Census 2024)
    # California: population 39,431,263 (from Census 2024)
    wy_pop = 587618
    ca_pop = 39431263

    wy_trials = 10
    ca_trials = 500

    wy_rate = _compute_per_100k(wy_trials, wy_pop)
    ca_rate = _compute_per_100k(ca_trials, ca_pop)

    assert wy_rate is not None and ca_rate is not None
    assert wy_rate == 1.7018
    assert ca_rate == 1.2680

    # Normalization effect: WY has a higher rate per 100k residents despite CA
    # having 50x more raw trials
    assert wy_trials < ca_trials
    assert wy_rate > ca_rate


def test_unknown_and_null_population_handling() -> None:
    """Missing or nonpositive population yields None without raising errors."""
    assert _compute_per_100k(5, None) is None
    assert _compute_per_100k(5, 0) is None
    assert _compute_per_100k(5, -100) is None
    assert _compute_per_100k(0, 1000000) == 0.0


def test_population_rates_in_fixture_warehouse(tmp_path: Path) -> None:
    """Build real fixture marts to check small-state rates and unmatched territories.

    Returns after asserting counts, rates, and provenance; propagates build errors.
    Uses synthetic public-registry records and writes only to the temporary root.
    """
    # 1. Give each jurisdiction a listing so an inner population join cannot pass.
    states = ("CA", "CA", "WY", "DC", "PR", "GU", "VI", "AS", "MP", "VT")
    added = tuple(
        AddedStudy(
            nct_id=f"NCT009900{index:02d}",
            clone_of="NCT00000001",
            overall_status="COMPLETED" if state == "VT" else "RECRUITING",
            location=(f"Population fixture {index}", "Fixture City", state),
        )
        for index, state in enumerate(states)
    )
    root = build_fixture_warehouse(
        tmp_path / "population", runs=[replace(base_adrd_run(), added=added)]
    )

    # 2. Query the built model, rather than validating only a Python copy of its formula.
    with duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True) as con:
        rows = con.execute(
            """
            select state_normalized, recruiting_trial_count, population,
                   population_source, population_vintage, recruiting_trials_per_100k
            from main_marts.mart_condition_geography_trends
            where indication_profile_id = 'adrd' and condition_group = 'alzheimers_disease'
            """
        ).fetchall()
    by_state = {
        state: (count, population, source, vintage, rate)
        for state, count, population, source, vintage, rate in rows
    }
    assert set(states) <= by_state.keys()
    assert by_state["WY"] == (1, 587618, "U.S. Census Bureau", "Vintage 2024", 0.1702)
    assert by_state["DC"] == (1, 702250, "U.S. Census Bureau", "Vintage 2024", 0.1424)
    assert by_state["PR"] == (1, 3203295, "U.S. Census Bureau", "Vintage 2024", 0.0312)
    assert by_state["VT"] == (0, 648493, "U.S. Census Bureau", "Vintage 2024", 0.0)
    assert by_state["CA"][0] > by_state["WY"][0]
    assert by_state["CA"][4] < by_state["WY"][4]
    for state in ("GU", "VI", "AS", "MP"):
        assert by_state[state] == (1, None, None, None, None)
