"""Warehouse codes render as reader-facing labels."""

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from components.formatting import (  # noqa: E402
    format_band,
    format_phase,
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


def test_band_labels():
    assert format_band("priority_review") == "Priority review"
    assert format_band("review") == "Review"
    assert format_band("watch") == "Watch"
    assert format_band("unexpected") == "unexpected"


def test_snapshot_date_drops_the_midnight_time():
    assert format_snapshot_date(pd.Timestamp("2026-10-05 00:00:00")) == "Oct 5, 2026"
    assert format_snapshot_date(dt.date(2026, 1, 9)) == "Jan 9, 2026"
    assert format_snapshot_date(None) == "not yet captured"
    assert format_snapshot_date(pd.NaT) == "not yet captured"
