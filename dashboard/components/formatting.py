"""Reader-facing labels for warehouse codes shown in the dashboard.

Phase labels are read from the dbt seed that defines them, so a phase the
pipeline learns to map is labelled here without a second edit.
"""

from __future__ import annotations

import csv
from functools import cache
from pathlib import Path

import pandas as pd

PHASE_SEED = (
    Path(__file__).resolve().parents[2] / "dbt_clinical_trials" / "seeds" / "phase_mapping.csv"
)

BAND_LABELS: dict[str, str] = {
    "priority_review": "Priority review",
    "review": "Review",
    "watch": "Watch",
}


@cache
def _phase_labels() -> dict[str, str]:
    with PHASE_SEED.open(newline="", encoding="utf-8") as handle:
        return {row["phase_normalized"]: row["phase_display"] for row in csv.DictReader(handle)}


def format_phase(code: object) -> str:
    """``"PHASE1/PHASE2"`` → ``"Phase 1/2"``; unmapped codes are title-cased."""
    if not isinstance(code, str):
        return ""
    return _phase_labels().get(code, code.replace("_", " ").title())


def format_band(band: object) -> str:
    """``"priority_review"`` → ``"Priority review"``; unknown bands pass through."""
    if not isinstance(band, str):
        return ""
    return BAND_LABELS.get(band, band)


def format_snapshot_date(value: object) -> str:
    """Calendar date of a snapshot, e.g. ``"Oct 5, 2026"``."""
    if value is None or pd.isna(value):
        return "not yet captured"
    stamp = pd.Timestamp(value)
    return f"{stamp:%b} {stamp.day}, {stamp.year}"
