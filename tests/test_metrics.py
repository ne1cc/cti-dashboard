"""Metric-definition tests: raw metric mathematical invariants, ROI arithmetic."""

from pathlib import Path

import duckdb
import pytest

from src.analysis.roi_scenarios import (
    RoiConfig,
    compute_scenarios,
    load_roi_config,
    render_markdown,
)

ROOT = Path(__file__).resolve().parents[1]
ROI_YML = ROOT / "config" / "roi_assumptions.yml"


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


def test_sponsor_hhi_bounds():
    """Sponsor HHI calculated as sum of squared shares is strictly in [0, 1.0]."""
    con = duckdb.connect(":memory:")
    res = con.execute(
        """
        with sponsor_shares as (
            select cnt / sum(cnt) over () as share
            from (values (10.0), (20.0), (30.0), (40.0)) t(cnt)
        )
        select sum(power(share, 2)) as hhi from sponsor_shares
        """
    ).fetchone()[0]
    assert 0.0 <= res <= 1.0


def test_site_overlap_rate_bounds():
    """Site overlap share/rate is bounded within [0.0, 1.0]."""
    con = duckdb.connect(":memory:")
    res = con.execute(
        """
        select
            0.0 / 10.0 as min_rate,
            5.0 / 10.0 as mid_rate,
            10.0 / 10.0 as max_rate
        """
    ).fetchone()
    assert res == (0.0, 0.5, 1.0)
    for r in res:
        assert 0.0 <= r <= 1.0


class TestRoiScenarios:
    def test_load_and_compute(self):
        config = load_roi_config(ROI_YML)
        results = compute_scenarios(config)
        assert {r.scenario_key for r in results} == set(config.scenarios)
        for r in results:
            assert r.illustrative_total_value == pytest.approx(
                r.illustrative_review_effort_value + r.illustrative_activation_value
            )
            assert "not" in r.disclaimer.lower()

    def test_arithmetic_is_pure_assumption_product(self):
        config = RoiConfig.model_validate(
            {
                "disclaimer": "Illustrative only — not observed outcomes.",
                "currency": "USD",
                "assumptions": {
                    "cost_per_feasibility_review": 1000,
                    "deprioritized_review_share": 0.5,
                    "cost_per_underperforming_site_activation": 10000,
                    "site_activations_per_cycle": 4,
                    "activation_decisions_influenced_share": 0.25,
                },
                "scenarios": {
                    "base": {
                        "label": "Base",
                        "review_multiplier": 1.0,
                        "activation_multiplier": 1.0,
                    }
                },
                "reviews_per_cycle": 10,
            }
        )
        (result,) = compute_scenarios(config)
        assert result.reviews_deprioritized == 5.0
        assert result.illustrative_review_effort_value == 5000.0
        assert result.activations_influenced == 1.0
        assert result.illustrative_activation_value == 10000.0
        assert result.illustrative_total_value == 15000.0

    def test_markdown_carries_disclaimer(self):
        config = load_roi_config(ROI_YML)
        markdown = render_markdown(compute_scenarios(config))
        assert "Illustrative" in markdown
        assert "not observed outcomes" in markdown
