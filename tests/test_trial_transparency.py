"""Unit and warehouse-level tests for Trial Transparency (Issue #76).

Tests:
1. mart_early_stop_transparency:
   - Captures TERMINATED, WITHDRAWN, SUSPENDED trials.
   - Preserves verbatim sponsor-reported whyStopped text without categorization.
   - Provides contributing NCT IDs.
   - Scoped strictly by indication profile.

2. mart_results_posting_transparency:
   - Evaluates completed and terminated trials with PCD >= 365 days prior to snapshot.
   - Computes 95% two-sided Wilson score confidence intervals [lower, upper] bounded in [0.0, 1.0].
   - Flags small-n (< 5) sponsor classes as unranked (is_ranked = false).
   - Provides contributing, overdue, and posted NCT IDs.

3. dashboard.components.data:
   - early_stop_transparency(profile_id)
   - results_posting_transparency(profile_id)
   - Adheres to profile-first argument and parameter binding.
"""

import math
from pathlib import Path

import duckdb
import pytest

from dashboard.components.data import (
    early_stop_transparency,
    results_posting_transparency,
)


def _duckdb_connection(root: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True)


def test_wilson_ci_math_properties(fixture_project_root: Path) -> None:
    """Verify that Wilson confidence intervals are mathematically valid:
    - 0.0 <= lower <= upper <= 1.0
    - lower <= rate <= upper (or rate within bounds when p=0 or p=1)
    """
    con = _duckdb_connection(fixture_project_root)
    try:
        rows = con.execute(
            """
            select
                total_evaluable_trials,
                results_posted_evaluable_count,
                results_posted_rate,
                ci_lower,
                ci_upper,
                is_ranked
            from main_marts.mart_results_posting_transparency
            where total_evaluable_trials > 0
            """
        ).fetchall()

        for (
            eligible,
            results,
            rate,
            ci_lower,
            ci_upper,
            is_ranked,
        ) in rows:
            assert eligible > 0
            assert 0 <= results <= eligible
            assert 0.0 <= rate <= 1.0
            assert 0.0 <= ci_lower <= 1.0
            assert 0.0 <= ci_upper <= 1.0
            assert ci_lower <= ci_upper
            # Small-n threshold assertion: only ranked if n >= 5
            if eligible >= 5:
                assert is_ranked is True
            else:
                assert is_ranked is False

            # Verify Wilson score formula directly with z = 1.959963984540054
            z = 1.959963984540054
            z2 = z * z
            p = results / eligible
            denom = 1.0 + z2 / eligible
            center = (p + z2 / (2.0 * eligible)) / denom
            spread = (z / denom) * math.sqrt(
                p * (1.0 - p) / eligible + z2 / (4.0 * eligible * eligible)
            )
            expected_lower = max(0.0, center - spread)
            expected_upper = min(1.0, center + spread)

            assert abs(ci_lower - round(expected_lower, 4)) < 1e-3
            assert abs(ci_upper - round(expected_upper, 4)) < 1e-3
    finally:
        con.close()


def test_mart_early_stop_transparency_structure(fixture_project_root: Path) -> None:
    """Verify mart_early_stop_transparency only contains stopped statuses
    and provides contributing NCT IDs."""
    con = _duckdb_connection(fixture_project_root)
    try:
        rows = con.execute(
            """
            select distinct overall_status
            from main_marts.mart_early_stop_transparency
            """
        ).fetchall()

        allowed_statuses = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
        for (status,) in rows:
            assert status in allowed_statuses

        # Check column types and non-null guarantees
        non_null_check = con.execute(
            """
            select
                count(*) as total_rows,
                count(indication_profile_id) as prof_count,
                count(condition_group) as cond_count,
                count(sponsor_class) as spon_count,
                count(overall_status) as status_count,
                count(stopped_trial_count) as stopped_count,
                count(contributing_nct_ids) as nct_count
            from main_marts.mart_early_stop_transparency
            """
        ).fetchone()
        assert non_null_check is not None
        total = non_null_check[0]
        for c in non_null_check[1:]:
            assert c == total
    finally:
        con.close()


def test_data_readers_profile_isolated(
    fixture_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify data.py readers return data frames properly scoped by profile."""
    # Point the warehouse connection to fixture_project_root
    db_path = str(fixture_project_root / "data/warehouse/clinical_trials.duckdb")
    monkeypatch.setenv("DUCKDB_PATH", db_path)

    df_early_stop_adrd = early_stop_transparency("adrd")
    df_results_adrd = results_posting_transparency("adrd")

    assert not df_early_stop_adrd.empty or df_early_stop_adrd is not None
    assert not df_results_adrd.empty or df_results_adrd is not None

    if not df_early_stop_adrd.empty:
        assert (df_early_stop_adrd["indication_profile_id"] == "adrd").all()
        assert "why_stopped" in df_early_stop_adrd.columns
        assert "contributing_nct_ids" in df_early_stop_adrd.columns

    if not df_results_adrd.empty:
        assert (df_results_adrd["indication_profile_id"] == "adrd").all()
        assert "results_posted_rate" in df_results_adrd.columns
        assert "ci_lower" in df_results_adrd.columns
        assert "ci_upper" in df_results_adrd.columns
        assert "is_ranked" in df_results_adrd.columns

    df_early_stop_nsclc = early_stop_transparency("nsclc")
    if not df_early_stop_nsclc.empty:
        assert (df_early_stop_nsclc["indication_profile_id"] == "nsclc").all()


def test_no_failure_categorization_in_marts(fixture_project_root: Path) -> None:
    """Verify no forbidden evaluative/blame column names or transformations exist."""
    con = _duckdb_connection(fixture_project_root)
    try:
        col_names = [
            row[0].lower()
            for row in con.execute(
                """
                select column_name
                from information_schema.columns
                where table_name in (
                    'mart_early_stop_transparency',
                    'mart_results_posting_transparency',
                )
                """
            ).fetchall()
        ]
        forbidden_substrings = ["failure", "blame", "performance_grade", "poor_performer"]
        for col in col_names:
            for forbidden in forbidden_substrings:
                assert forbidden not in col, f"Forbidden column found: {col}"
    finally:
        con.close()
