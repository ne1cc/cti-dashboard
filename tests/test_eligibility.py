"""Tests for eligibility parsing, classification, validation, and filter matching."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import duckdb
import pytest

from src.transform.eligibility import (
    classify_age_bound,
    matches_age,
    parse_age_to_years,
    validate_age_bounds,
)

FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "api" / "eligibility_cases.json"
MACRO_PATH = (
    Path(__file__).resolve().parent.parent
    / "dbt_clinical_trials"
    / "macros"
    / "parse_age_years.sql"
)


@pytest.fixture
def eligibility_cases() -> dict[str, Any]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


def test_single_age_cases(eligibility_cases: Mapping[str, Any]) -> None:
    for case in eligibility_cases["single_age_cases"]:
        raw = case["raw"]
        expected_years = case["expected_years"]
        expected_class = case["classification"]

        parsed = parse_age_to_years(raw)
        if expected_years is None:
            assert parsed is None, f"Expected None for {raw!r}, got {parsed}"
        else:
            assert parsed is not None, f"Expected {expected_years} for {raw!r}, got None"
            assert abs(parsed - expected_years) < 1e-6, (
                f"Mismatch for {raw!r}: {parsed} vs {expected_years}"
            )

        classification = classify_age_bound(raw)
        assert classification == expected_class, (
            f"Classification mismatch for {raw!r}: {classification} vs {expected_class}"
        )


def test_paired_bounds_cases(eligibility_cases: Mapping[str, Any]) -> None:
    for case in eligibility_cases["paired_bounds_cases"]:
        res = validate_age_bounds(case["min_raw"], case["max_raw"])
        assert res.is_valid == case["is_valid"], f"Validation mismatch for {case['name']}"
        assert res.is_contradictory == case["is_contradictory"], (
            f"Contradiction mismatch for {case['name']}"
        )
        assert res.has_unspecified_min_age == case["has_unspecified_min_age"], (
            f"Unspecified min mismatch for {case['name']}"
        )
        assert res.has_unspecified_max_age == case["has_unspecified_max_age"], (
            f"Unspecified max mismatch for {case['name']}"
        )
        if case["min_years"] is None:
            assert res.min_age_years is None
        else:
            assert res.min_age_years is not None
            assert abs(res.min_age_years - case["min_years"]) < 1e-6
        if case["max_years"] is None:
            assert res.max_age_years is None
        else:
            assert res.max_age_years is not None
            assert abs(res.max_age_years - case["max_years"]) < 1e-6


def test_patient_age_filter_cases(eligibility_cases: Mapping[str, Any]) -> None:
    for case in eligibility_cases["patient_age_filter_cases"]:
        min_years = parse_age_to_years(case["min_raw"])
        max_years = parse_age_to_years(case["max_raw"])
        matched = matches_age(case["target_age"], min_years, max_years)
        desc = case["description"]
        tgt = case["target_age"]
        assert matched == case["expected_match"], (
            f"Failed {desc}: target {tgt}, min {min_years}, max {max_years}"
        )


def test_dbt_macro_duckdb_parity(eligibility_cases: Mapping[str, Any]) -> None:
    """Verify DuckDB evaluates the parse_age_years macro logic identically to Python."""
    macro_content = MACRO_PATH.read_text(encoding="utf-8")
    start_tag = "{% macro parse_age_years(column) %}"
    end_tag = "{% endmacro %}"
    body = macro_content.split(start_tag)[1].split(end_tag)[0].strip()
    body_sql = body.replace("{{ column }}", "col")

    con = duckdb.connect()
    for case in eligibility_cases["single_age_cases"]:
        raw = case["raw"]
        expected = case["expected_years"]
        if raw is None:
            val_clause = "NULL"
        else:
            escaped = raw.replace("'", "''")
            val_clause = f"'{escaped}'"
        result = con.execute(f"select {body_sql} from (select {val_clause} as col)").fetchone()[0]
        if expected is None:
            assert result is None, f"DuckDB mismatch for {raw!r}: expected None, got {result}"
        else:
            assert result is not None and abs(result - expected) < 1e-6, (
                f"DuckDB mismatch for {raw!r}: expected {expected}, got {result}"
            )
