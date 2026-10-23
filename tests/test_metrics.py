"""Metric-definition tests: config consistency, score math."""

import csv
from pathlib import Path

import duckdb
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCORE_WEIGHTS_YML = ROOT / "config" / "score_weights.yml"
SEED_CSV = ROOT / "dbt_clinical_trials" / "seeds" / "feasibility_score_weights.csv"
DBT_PROJECT_YML = ROOT / "dbt_clinical_trials" / "dbt_project.yml"


@pytest.fixture(scope="module")
def score_config() -> dict:
    return yaml.safe_load(SCORE_WEIGHTS_YML.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def seed_weights() -> dict[str, float]:
    with open(SEED_CSV, encoding="utf-8") as handle:
        return {row["component"]: float(row["weight"]) for row in csv.DictReader(handle)}


def test_score_weights_sum_to_one(score_config):
    weights = score_config["score"]["weights"]
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_yaml_weights_match_dbt_seed(score_config, seed_weights):
    assert score_config["score"]["weights"] == seed_weights


def test_band_thresholds_match_dbt_vars(score_config):
    dbt_vars = yaml.safe_load(DBT_PROJECT_YML.read_text(encoding="utf-8"))["vars"]
    bands = score_config["score"]["bands"]
    assert bands["review"] == dbt_vars["feasibility_band_review_threshold"]
    assert bands["priority_review"] == dbt_vars["feasibility_band_priority_threshold"]
    assert bands["watch"] == 0.0
    assert bands["watch"] < bands["review"] < bands["priority_review"] <= 1.0


def test_data_confidence_weights_sum_to_one(score_config):
    dc = score_config["data_confidence"]
    assert abs(dc["record_quality_weight"] + dc["location_usability_weight"] - 1.0) < 1e-9


def test_weighted_score_stays_in_bounds(seed_weights):
    """The score formula (weighted sum of 0..1 components) is bounded by [0, 1]."""
    con = duckdb.connect(":memory:")
    con.execute(
        """
        create table components as
        select * from (values
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (1.0, 1.0, 1.0, 1.0, 1.0),
            (0.5, 0.25, 0.75, 0.1, 0.9)
        ) t(density, growth, concentration, overlap, confidence)
        """
    )
    w = seed_weights
    rows = con.execute(
        f"""
        select {w["normalized_recruiting_trial_count"]} * density
             + {w["normalized_recent_recruiting_growth"]} * growth
             + {w["normalized_sponsor_concentration"]} * concentration
             + {w["normalized_site_overlap"]} * overlap
             + {w["normalized_data_confidence_adjustment"]} * confidence
        from components
        """
    ).fetchall()
    scores = [float(row[0]) for row in rows]
    assert scores[0] == 0.0
    assert abs(scores[1] - 1.0) < 1e-9
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_min_max_normalization_degenerate_spread_is_zero():
    """When max == min, safe_divide yields null -> coalesce to 0 (no signal)."""
    result = duckdb.sql(
        """
        select coalesce(
            case when (mx - mn) = 0 then null else (v - mn) / (mx - mn) end, 0
        )
        from (select 5.0 v, 5.0 mn, 5.0 mx)
        """
    ).fetchone()[0]
    assert result == 0.0


def test_sponsor_hhi_math():
    """HHI of shares {0.5, 0.25, 0.25} = 0.375; single sponsor = 1.0."""
    hhi = duckdb.sql(
        """
        select sum(power(cnt / total, 2))
        from (
            select cnt, sum(cnt) over () as total
            from (values (2.0), (1.0), (1.0)) t(cnt)
        )
        """
    ).fetchone()[0]
    assert abs(hhi - 0.375) < 1e-9
    solo = duckdb.sql("select power(3.0 / 3.0, 2)").fetchone()[0]
    assert solo == 1.0
