"""Prototype: AACT snapshots as a deep trial-status history source.

Issue #56 (Epic #43). AACT (CTTI) publishes dated static copies of the whole
ClinicalTrials.gov registry; monthly copies reach back years, giving
status-transition metrics 12+ months of depth without waiting for this
project's own weekly snapshots to accrue (retention keeps only 3, ~14 days).

The prototype consumes CSV extracts of the AACT ``ctgov.studies`` table, one
file per snapshot date, named ``studies_YYYYMMDD.csv``. After creating an
AACT account (https://aact.ctti-clinicaltrials.org), produce an extract per
snapshot with one psql command:

    \\copy (select nct_id, overall_status, start_date,
                  primary_completion_date, completion_date
           from ctgov.studies) to 'studies_YYYYMMDD.csv' csv header

It builds the slim ``fct_trial_status_history`` frame (nct_id, snapshot_date,
overall_status, key dates, record hash) with the same transition flags as dbt
``int_trial_status_history``, and compares AACT rows against this project's
own snapshot rows for overlapping dates. Decision record: ADR 0056.

This is prototype code: it never touches the production warehouse, and no
AACT dumps or extracts are committed to the repository.
"""

import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from src.utils.hashing import sha256_text

SLIM_HISTORY_COLUMNS = [
    "nct_id",
    "snapshot_date",
    "overall_status",
    "start_date",
    "primary_completion_date",
    "completion_date",
    "record_hash",
    "source",
    "previous_status",
    "status_changed_flag",
    "entered_recruiting_flag",
    "left_recruiting_flag",
]

_EXTRACT_NAME = re.compile(r"^studies_(\d{8})\.csv$")

# AACT studies-table columns kept in the slim history; also the exact column
# order the documented \copy extract emits.
_AACT_FIELDS = [
    "nct_id",
    "overall_status",
    "start_date",
    "primary_completion_date",
    "completion_date",
]


def extract_snapshot_date(path: Path) -> datetime:
    """Snapshot date from an extract filename like studies_20250101.csv."""
    match = _EXTRACT_NAME.match(path.name)
    if match is None:
        raise ValueError(f"extract must be named studies_YYYYMMDD.csv, got {path.name}")
    return datetime.strptime(match.group(1), "%Y%m%d")


def _record_hash(row: pd.Series) -> str:
    """Change-detection hash of one registry record's kept fields."""
    return sha256_text("|".join(str(row[field]) for field in _AACT_FIELDS))


def read_aact_extract(path: Path) -> pd.DataFrame:
    """One extract CSV -> one slim frame at that file's snapshot date."""
    snapshot_date = extract_snapshot_date(path).date().isoformat()
    frame = pd.read_csv(path, dtype=str)
    missing = [field for field in _AACT_FIELDS if field not in frame.columns]
    if missing:
        raise ValueError(f"{path.name} missing AACT columns: {missing}")
    frame["snapshot_date"] = snapshot_date
    frame["record_hash"] = frame.apply(_record_hash, axis=1)
    frame["source"] = "aact"
    return frame[_AACT_FIELDS + ["snapshot_date", "record_hash", "source"]]


def build_status_history(csv_paths: Sequence[Path]) -> pd.DataFrame:
    """Load N snapshot extracts into the slim status history with transitions.

    One row per nct_id per snapshot date, ordered over time, with the same
    transition flags as dbt int_trial_status_history. The span of snapshot
    dates in the result is the metric horizon; monthly extracts spanning 13
    months yield 12+ months of transition coverage.
    """
    frames = [read_aact_extract(path) for path in csv_paths]
    if not frames:
        raise ValueError("no AACT extracts given")
    history = pd.concat(frames, ignore_index=True)
    history = history.sort_values(["nct_id", "snapshot_date"], kind="stable")
    # The same date given twice is one snapshot; the last extract wins.
    history = history.drop_duplicates(["nct_id", "snapshot_date"], keep="last")
    grouped = history.groupby("nct_id", sort=False)
    history["previous_status"] = grouped["overall_status"].shift(1)
    changed = history["overall_status"] != history["previous_status"]
    history["status_changed_flag"] = history["previous_status"].notna() & changed
    history["entered_recruiting_flag"] = (
        (history["overall_status"] == "RECRUITING")
        & history["previous_status"].notna()
        & (history["previous_status"] != "RECRUITING")
    )
    history["left_recruiting_flag"] = (history["overall_status"] != "RECRUITING") & (
        history["previous_status"] == "RECRUITING"
    )
    return history[SLIM_HISTORY_COLUMNS].reset_index(drop=True)


def compare_with_own(
    aact: pd.DataFrame,
    own: pd.DataFrame,
    tolerance_days: int = 7,
) -> pd.DataFrame:
    """Compare AACT history against this project's own snapshot rows.

    ``own`` carries nct_id, snapshot_date, overall_status (one row per trial
    per own snapshot date, e.g. read from silver_trials parquet). Each own
    observation is matched to the nearest AACT snapshot for the same trial
    within ``tolerance_days``; the outcome column classifies

    - ``match``: same overall status at (near) the same date,
    - ``status_mismatch``: both sources present, status disagrees,
    - ``own_only``: no AACT snapshot for the trial within tolerance.

    Trials present only in AACT produce no row here; they are outside the
    project's own collection scope and never a discrepancy of it.
    """
    left = own.assign(_ts=pd.to_datetime(own["snapshot_date"])).sort_values("_ts")
    right = aact.assign(_ts=pd.to_datetime(aact["snapshot_date"]))
    right = right[["nct_id", "_ts", "snapshot_date", "overall_status"]].sort_values("_ts")
    merged = pd.merge_asof(
        left,
        right,
        on="_ts",
        by="nct_id",
        direction="nearest",
        tolerance=timedelta(days=tolerance_days),
        suffixes=("_own", "_aact"),
    )
    matched = merged["overall_status_aact"].notna()
    merged["outcome"] = "own_only"
    merged.loc[matched, "outcome"] = "match"
    mismatch = matched & (merged["overall_status_own"] != merged["overall_status_aact"])
    merged.loc[mismatch, "outcome"] = "status_mismatch"
    return (
        merged[
            ["nct_id", "snapshot_date_own", "snapshot_date_aact", "overall_status_own", "outcome"]
        ]
        .rename(columns={"snapshot_date_own": "own_snapshot_date"})
        .sort_values(["own_snapshot_date", "nct_id"], kind="stable")
        .reset_index(drop=True)
    )
