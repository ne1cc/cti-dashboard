"""Warehouse codes render as reader-facing labels."""

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from components.formatting import (  # noqa: E402
    format_phase,
    format_priority_explanation,
    format_snapshot_date,
)


def test_phase_labels_come_from_the_dbt_seed():
    assert format_phase("PHASE3") == "Phase 3"
    assert format_phase("PHASE1/PHASE2") == "Phase 1/2"
    assert format_phase("NOT_APPLICABLE") == "Not Applicable"


def test_unmapped_and_missing_phases_degrade_readably():
    assert format_phase("SOME_NEW_PHASE") == "Some New Phase"
    assert format_phase(None) == ""
    assert format_phase(float("nan")) == ""


def test_snapshot_date_drops_the_midnight_time():
    assert format_snapshot_date(pd.Timestamp("2026-10-05 00:00:00")) == "Oct 5, 2026"
    assert format_snapshot_date(dt.date(2026, 1, 9)) == "Jan 9, 2026"
    assert format_snapshot_date(None) == "not yet captured"
    assert format_snapshot_date(pd.NaT) == "not yet captured"


@pytest.mark.parametrize("proxy", [False, True])
def test_priority_summary_labels_proxy_and_observed_growth_distinctly(proxy):
    row = pd.Series(
        {
            "recruiting_trial_count": 15,
            "growth_uses_registry_proxy_flag": proxy,
            "recent_growth_input": 2,
            "sponsor_count": 6,
            "sponsor_hhi": 0.26,
            "site_overlap_share": 0.93,
            "operational_completeness_share": 0.74,
        }
    )
    before = row.copy()
    text = format_priority_explanation(row)
    assert len(text) < 190
    assert "Recruiting trials: 15" in text
    assert "93%" in text and "74%" in text
    if proxy:
        assert "First posted (90d proxy): 2" in text
        assert "Newly recruiting" not in text
    else:
        assert "Newly recruiting (90d): 2" in text
        assert "proxy" not in text
    pd.testing.assert_series_equal(row, before)
