"""Reader-facing labels for warehouse codes shown in the dashboard.

Phase labels are read from the dbt seed that defines them, so a phase the
pipeline learns to map is labelled here without a second edit.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from functools import cache
from pathlib import Path

import pandas as pd

PHASE_SEED = (
    Path(__file__).resolve().parents[2] / "dbt_clinical_trials" / "seeds" / "phase_mapping.csv"
)


@cache
def _phase_labels() -> dict[str, str]:
    with PHASE_SEED.open(newline="", encoding="utf-8") as handle:
        return {row["phase_normalized"]: row["phase_display"] for row in csv.DictReader(handle)}


def format_phase(code: object) -> str:
    """``"PHASE1/PHASE2"`` → ``"Phase 1/2"``; unmapped codes are title-cased."""
    if not isinstance(code, str):
        return ""
    return _phase_labels().get(code, code.replace("_", " ").title())


def format_snapshot_date(value: str | pd.Timestamp | datetime | date | None) -> str:
    """Calendar date of a snapshot, e.g. ``"Oct 5, 2026"``."""
    if value is None or pd.isna(value):
        return "not yet captured"
    stamp = pd.Timestamp(value)
    return f"{stamp:%b} {stamp.day}, {stamp.year}"


def format_priority_explanation(row: pd.Series) -> str:
    """Summarize segment metrics without presenting first-post proxies as recruiting events."""
    growth = (
        "First posted (90d proxy)"
        if row["growth_uses_registry_proxy_flag"]
        else "Newly recruiting (90d)"
    )
    return (
        f"Recruiting trials: {row['recruiting_trial_count']:.0f} · "
        f"{growth}: {row['recent_growth_input']:.0f} · "
        f"Sponsors: {row['sponsor_count']:.0f} (HHI {row['sponsor_hhi']:.2f}) · "
        f"Trials sharing sites: {row['site_overlap_share']:.0%} · "
        f"Operational completeness: {row['operational_completeness_share']:.0%}"
    )
