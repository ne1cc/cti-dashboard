"""Tests for state population denominator seed and per-100k rate calculations."""

import csv
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "dbt_clinical_trials" / "seeds" / "state_population.csv"
GEO_RULES_PATH = ROOT / "config" / "geography_rules.yml"


def _load_seed_rows() -> list[dict[str, str]]:
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
