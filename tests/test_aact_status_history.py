"""Prototype checks for the AACT status-history source (issue #56, ADR 0056)."""

from pathlib import Path

import pandas as pd
import pytest

from src.analysis.aact_status_history import (
    SLIM_HISTORY_COLUMNS,
    build_status_history,
    compare_with_own,
)

MONTHLY_DATES = [f"2025-{month:02d}-01" for month in range(1, 13)] + [
    "2026-01-01",
    "2026-02-01",
]


def _extract_rows(snapshot_date: str) -> list[dict[str, str]]:
    # Trial 1 flips RECRUITING -> ACTIVE_NOT_RECRUITING with 2025-09-01;
    # trial 2 never changes. Dates are key registry dates, kept as strings.
    trial1_status = "RECRUITING" if snapshot_date < "2025-09-01" else "ACTIVE_NOT_RECRUITING"
    return [
        {
            "nct_id": "NCT00000001",
            "overall_status": trial1_status,
            "start_date": "2024-06-01",
            "primary_completion_date": "2026-06-01",
            "completion_date": "2026-12-01",
        },
        {
            "nct_id": "NCT00000002",
            "overall_status": "COMPLETED",
            "start_date": "2023-01-15",
            "primary_completion_date": "2024-01-15",
            "completion_date": "2024-01-15",
        },
    ]


@pytest.fixture
def extract_paths(tmp_path: Path) -> list[Path]:
    paths: list[Path] = []
    for snapshot_date in MONTHLY_DATES:
        stamp = snapshot_date.replace("-", "")
        frame = pd.DataFrame(_extract_rows(snapshot_date))
        path = tmp_path / f"studies_{stamp}.csv"
        frame.to_csv(path, index=False)
        paths.append(path)
    return paths


def test_build_slim_history_shape_and_hash(extract_paths: list[Path]) -> None:
    history = build_status_history(extract_paths)
    assert list(history.columns) == SLIM_HISTORY_COLUMNS
    # 2 trials x 14 monthly snapshots.
    assert len(history) == len(MONTHLY_DATES) * 2
    assert set(history["source"]) == {"aact"}
    assert set(history["snapshot_date"]) == set(MONTHLY_DATES)

    trial1 = history[history["nct_id"] == "NCT00000001"]
    # Fields are identical across the flat period -> one stable hash; the
    # status flip changes it.
    assert trial1["record_hash"].nunique() == 2
    flat = trial1[trial1["snapshot_date"] < "2025-09-01"]["record_hash"]
    assert flat.nunique() == 1


def test_transitions_computed_over_12_months(extract_paths: list[Path]) -> None:
    history = build_status_history(extract_paths)
    span_months = (
        pd.Period(max(MONTHLY_DATES), freq="M") - pd.Period(min(MONTHLY_DATES), freq="M")
    ).n
    assert span_months >= 12, "acceptance: transition metrics must cover 12+ months"

    trial1 = history[history["nct_id"] == "NCT00000001"].set_index("snapshot_date")
    first, flipped, last = (
        trial1.loc["2025-01-01"],
        trial1.loc["2025-09-01"],
        trial1.loc["2026-02-01"],
    )
    assert first["previous_status"] is None or pd.isna(first["previous_status"])
    assert not bool(first["status_changed_flag"])
    assert flipped["previous_status"] == "RECRUITING"
    assert bool(flipped["status_changed_flag"]) and bool(flipped["left_recruiting_flag"])
    assert not bool(flipped["entered_recruiting_flag"])
    assert last["previous_status"] == "ACTIVE_NOT_RECRUITING"

    trial2 = history[history["nct_id"] == "NCT00000002"]
    assert not trial2["status_changed_flag"].any()


def test_rejects_bad_extract_name_and_missing_columns(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="studies_YYYYMMDD"):
        build_status_history([tmp_path / "snap.csv"])

    bad = tmp_path / "studies_20250101.csv"
    pd.DataFrame([{"nct_id": "NCT00000001", "overall_status": "RECRUITING"}]).to_csv(
        bad, index=False
    )
    with pytest.raises(ValueError, match="missing AACT columns"):
        build_status_history([bad])


def _single_extract(tmp_path: Path, statuses: list[str], dates: list[str]) -> list[Path]:
    paths: list[Path] = []
    for status, date in zip(statuses, dates, strict=True):
        path = tmp_path / f"studies_{date.replace('-', '')}.csv"
        pd.DataFrame(
            [
                {
                    "nct_id": "NCT00000001",
                    "overall_status": status,
                    "start_date": "2024-06-01",
                    "primary_completion_date": "2026-06-01",
                    "completion_date": "2026-12-01",
                }
            ]
        ).to_csv(path, index=False)
        paths.append(path)
    return paths


def test_status_normalization_is_not_a_transition(tmp_path: Path) -> None:
    # AACT spans registry API eras with differing capitalization; case or
    # padding alone must never read as a status change.
    paths = _single_extract(
        tmp_path, ["Recruiting", "  RECRUITING  "], ["2025-01-01", "2025-02-01"]
    )
    history = build_status_history(paths)
    assert (history["overall_status"] == "RECRUITING").all()
    assert not history["status_changed_flag"].any()
    assert not history["entered_recruiting_flag"].any()
    assert not history["left_recruiting_flag"].any()


def test_null_status_matches_dbt_model(tmp_path: Path) -> None:
    # int_trial_status_history: `overall_status != 'RECRUITING'` is NULL for a
    # null status, so left_recruiting stays unset; `is distinct from` still
    # counts the null as a change.
    paths = _single_extract(tmp_path, ["RECRUITING", None], ["2025-01-01", "2025-02-01"])
    history = build_status_history(paths)
    second = history.iloc[1]
    assert pd.isna(second["overall_status"])
    assert not bool(second["left_recruiting_flag"])
    assert bool(second["status_changed_flag"])


def test_compare_matched_null_status_is_not_own_only(tmp_path: Path) -> None:
    paths = _single_extract(tmp_path, [None], ["2025-02-01"])
    aact = build_status_history(paths)
    own = pd.DataFrame(
        [{"nct_id": "NCT00000001", "snapshot_date": "2025-02-01", "overall_status": "RECRUITING"}]
    )
    report = compare_with_own(aact, own)
    row = report.iloc[0]
    assert row["snapshot_date_aact"] == "2025-02-01"
    assert row["outcome"] == "status_mismatch"


def test_compare_reports_discrepancies(extract_paths: list[Path]) -> None:
    aact = build_status_history(extract_paths)
    own = pd.DataFrame(
        [
            # Same day, same status -> match.
            {
                "nct_id": "NCT00000001",
                "snapshot_date": "2025-01-01",
                "overall_status": "RECRUITING",
            },
            # Same day, disagreeing status -> status_mismatch.
            {
                "nct_id": "NCT00000001",
                "snapshot_date": "2025-09-01",
                "overall_status": "RECRUITING",
            },
            # Trial the project observed but AACT extract lacks -> own_only.
            {
                "nct_id": "NCT00000003",
                "snapshot_date": "2025-03-05",
                "overall_status": "RECRUITING",
            },
        ]
    )
    report = compare_with_own(aact, own)
    # Report rows are ordered by own snapshot date; merge_asof returns rows
    # keyed by date, not in the caller's row order.
    assert list(report["outcome"]) == ["match", "own_only", "status_mismatch"]
    mismatch_row = report.iloc[2]
    assert mismatch_row["own_snapshot_date"] == "2025-09-01"
    assert mismatch_row["snapshot_date_aact"] == "2025-09-01"
    own_only_row = report.iloc[1]
    assert pd.isna(own_only_row["snapshot_date_aact"])


def test_compare_matches_nearest_date_within_tolerance(extract_paths: list[Path]) -> None:
    aact = build_status_history(extract_paths)
    within = pd.DataFrame(
        [{"nct_id": "NCT00000001", "snapshot_date": "2025-01-05", "overall_status": "RECRUITING"}]
    )
    report = compare_with_own(aact, within, tolerance_days=7)
    assert report.iloc[0]["outcome"] == "match"
    assert report.iloc[0]["snapshot_date_aact"] == "2025-01-01"

    outside = within.assign(snapshot_date="2025-02-15")
    report = compare_with_own(aact, outside, tolerance_days=7)
    assert report.iloc[0]["outcome"] == "own_only"
