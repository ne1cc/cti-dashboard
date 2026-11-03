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


@pytest.mark.parametrize("mode", ["covered", "short", "gap"])
def test_priority_summary_labels_growth_and_registrations_distinctly(mode: str) -> None:
    if mode == "covered":
        observed_days = 90
        new_recruiting = 2
    elif mode == "short":
        observed_days = 14
        new_recruiting = pd.NA
    else:  # gap
        observed_days = 105
        new_recruiting = pd.NA

    row = pd.Series(
        {
            "recruiting_trial_count": 15,
            "new_recruiting_90d": new_recruiting,
            "newly_registered_90d": 4,
            "observed_history_days": observed_days,
            "sponsor_count": 6,
            "sponsor_hhi": 0.26,
            "site_overlap_share": 0.93,
            "operational_completeness_share": 0.74,
        }
    )
    before = row.copy()
    text = format_priority_explanation(row)
    assert len(text) < 240
    assert "Recruiting trials: 15" in text
    assert "Newly registered (first posted): 4" in text
    assert "93%" in text and "74%" in text
    assert "proxy" not in text

    if mode == "covered":
        assert "Newly recruiting (90d): 2 (90 days of history observed)" in text
        assert "not enough history" not in text
    elif mode == "short":
        assert "Newly recruiting: not enough history (14 of 90 days observed)" in text
        assert "(90d)" not in text
    else:
        assert "Newly recruiting: not enough history (gap between snapshots exceeds window)" in text
        assert "(90d)" not in text
        assert "105 of 90 days observed" not in text

    pd.testing.assert_series_equal(row, before)
