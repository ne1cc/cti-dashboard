"""Tests for state resident population seed and geographic normalization metrics.

Validates:
1. Static Census population seed (Vintage 2024, July 1, 2024 / POPESTIMATE2024)
   integrity, USPS postal codes, uniqueness, and completeness (50 states + DC + PR).
2. Rate normalization calculation formula (recruiting trials per 100k residents).
3. Null handling for non-positive or missing denominators.
4. Rank-inversion behavior where small populous states outrank large states on
   a per-100k basis despite lower raw listing counts.
"""

from __future__ import annotations

import csv

import yaml

from src.utils.paths import project_root


def test_state_population_seed_integrity() -> None:
    seed_path = project_root() / "dbt_clinical_trials" / "seeds" / "state_population.csv"
    assert seed_path.exists(), f"Seed file missing: {seed_path}"

    rules_path = project_root() / "config" / "geography_rules.yml"
    assert rules_path.exists(), f"Geography rules missing: {rules_path}"
    with open(rules_path, encoding="utf-8") as f:
        geo_rules = yaml.safe_load(f)

    valid_abbrs = set(geo_rules["valid_state_abbreviations"])

    with open(seed_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        assert fieldnames == [
            "state_code",
            "state_name",
            "population",
            "source_vintage",
            "estimate_date",
        ], f"Unexpected fieldnames: {fieldnames}"

        rows = list(reader)

    # 50 states + District of Columbia + Puerto Rico = 52
    assert len(rows) == 52, f"Expected exactly 52 state/territory entries, found {len(rows)}"

    state_codes: set[str] = set()
    state_names: set[str] = set()

    # Disallow aggregate regions or national totals
    forbidden_names = {"united states", "northeast", "midwest", "south", "west"}

    for row in rows:
        code = row["state_code"].strip()
        name = row["state_name"].strip()
        pop_str = row["population"].strip()
        vintage = row["source_vintage"].strip()
        est_date = row["estimate_date"].strip()

        assert len(code) == 2, f"State code {code} must be 2 characters"
        assert code in valid_abbrs, f"State code {code} not in valid abbreviations"
        assert code not in state_codes, f"Duplicate state code: {code}"
        state_codes.add(code)

        assert name.lower() not in forbidden_names, f"Aggregate row present: {name}"
        assert name not in state_names, f"Duplicate state name: {name}"
        state_names.add(name)

        pop = int(pop_str)
        assert pop > 0, f"Population for {code} must be positive, got {pop}"

        assert vintage == "Vintage 2024", f"Unexpected vintage: {vintage}"
        assert est_date == "2024-07-01", f"Unexpected estimate date: {est_date}"

    # Verify key known states exist
    for key_code in ("CA", "NY", "TX", "FL", "WY", "DC", "PR"):
        assert key_code in state_codes, f"Key state {key_code} missing from seed"


def test_recruiting_trials_per_100k_calculation_and_rank_inversion() -> None:
    """Demonstrate the normalization formula and small-state rank inversion.

    A smaller state (Wyoming) with 10 recruiting trials has a higher per-100k
    density than a large state (California) with 100 recruiting trials, even though
    California has 10x the raw listings.
    """
    seed_path = project_root() / "dbt_clinical_trials" / "seeds" / "state_population.csv"
    with open(seed_path, encoding="utf-8") as f:
        pops = {r["state_code"]: int(r["population"]) for r in csv.DictReader(f)}

    def calc_rate(recruiting_count: int, state_code: str) -> float | None:
        population = pops.get(state_code)
        if population is None or population <= 0:
            return None
        return (100000.0 * recruiting_count) / population

    wy_rate = calc_rate(10, "WY")
    ca_rate = calc_rate(100, "CA")

    assert wy_rate is not None
    assert ca_rate is not None
    assert wy_rate > ca_rate, f"Expected WY rate ({wy_rate:.3f}) > CA rate ({ca_rate:.3f}) per 100k"

    # Zero trials yields 0.0
    zero_rate = calc_rate(0, "WY")
    assert zero_rate == 0.0

    # Unmatched / missing state yields None
    unmatched_rate = calc_rate(10, "UNKNOWN")
    assert unmatched_rate is None

    # Territory not in Vintage 2024 totals (e.g., Guam) yields None
    gu_rate = calc_rate(10, "GU")
    assert gu_rate is None
