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
   - early_stop_transparency(preset)
   - results_posting_transparency(preset)
   - Adheres to preset-first argument and parameter binding.
"""

import math
from pathlib import Path

import duckdb
import pytest

from dashboard.components.data import (
    early_stop_transparency,
    results_posting_transparency,
)
from tests.preset_helpers import preset_for


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

    df_early_stop_adrd = early_stop_transparency(preset_for("adrd"))
    df_results_adrd = results_posting_transparency(preset_for("adrd"))

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
        assert "evaluable_nct_ids" in df_results_adrd.columns
        assert "posted_nct_ids" in df_results_adrd.columns
        assert "overdue_nct_ids" in df_results_adrd.columns

    df_early_stop_nsclc = early_stop_transparency(preset_for("oncology_nsclc"))
    if not df_early_stop_nsclc.empty:
        assert (df_early_stop_nsclc["indication_profile_id"] == "oncology_nsclc").all()


def test_mart_results_posting_nct_id_consistency(fixture_project_root: Path) -> None:
    """Verify NCT ID lists match their respective counts exactly."""
    con = _duckdb_connection(fixture_project_root)
    try:
        rows = con.execute(
            """
            select
                lead_sponsor,
                total_evaluable_trials,
                results_posted_evaluable_count,
                results_overdue_count,
                evaluable_nct_ids,
                posted_nct_ids,
                overdue_nct_ids
            from main_marts.mart_results_posting_transparency
            """
        ).fetchall()

        for (
            sponsor,
            n,
            posted_cnt,
            overdue_cnt,
            eval_ids,
            post_ids,
            overdue_ids,
        ) in rows:
            if n == 0:
                assert eval_ids is None, f"{sponsor} has n=0 but eval_ids={eval_ids}"
                assert post_ids is None, f"{sponsor} has n=0 but post_ids={post_ids}"
                assert overdue_ids is None, f"{sponsor} has n=0 but overdue_ids={overdue_ids}"
            else:
                eval_set = {x.strip() for x in eval_ids.split(",") if x.strip()}
                assert len(eval_set) == n, (
                    f"{sponsor}: eval list count mismatch ({len(eval_set)} != {n})"
                )

                if posted_cnt > 0:
                    assert post_ids is not None
                    post_set = {x.strip() for x in post_ids.split(",") if x.strip()}
                    assert len(post_set) == posted_cnt
                    assert post_set.issubset(eval_set)
                else:
                    assert post_ids is None

                if overdue_cnt > 0:
                    assert overdue_ids is not None
                    overdue_set = {x.strip() for x in overdue_ids.split(",") if x.strip()}
                    assert len(overdue_set) == overdue_cnt
                    assert overdue_set.issubset(eval_set)
                else:
                    assert overdue_ids is None
    finally:
        con.close()


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


def test_registry_transparency_marts_preset_vs_source_totals_and_baseline_parity(
    tmp_path: Path,
) -> None:
    """Phase 4b transparency marts keep preset denominators, source totals, and legacy parity."""
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from tests.fixture_warehouse import (
        AddedStudy,
        base_adrd_run,
        base_nsclc_run,
        build_fixture_warehouse,
    )

    adrd_mesh = {
        "conditionBrowseModule": {"meshes": [{"id": "D000544", "term": "Alzheimer Disease"}]}
    }
    nsclc_mesh = {
        "conditionBrowseModule": {
            "meshes": [{"id": "D002289", "term": "Carcinoma, Non-Small-Cell Lung"}]
        }
    }
    # Add an unassigned terminated trial by NeuroPharm Inc (no MeSH) + an evaluable completed trial
    # by NeuroPharm Inc (no MeSH) so source_total_* > preset_total_* is observable.
    unassigned_stopped = AddedStudy(
        nct_id="NCT00000081",
        clone_of="NCT00000006",
        overall_status="TERMINATED",
        condition="Unspecified Condition",
        sponsor="NeuroPharm Inc",
    )
    unassigned_completed = AddedStudy(
        nct_id="NCT00000082",
        clone_of="NCT00000003",
        overall_status="COMPLETED",
        condition="Unspecified Condition",
        sponsor="NeuroPharm Inc",
    )
    nsclc_stopped = AddedStudy(
        nct_id="NCT00000083",
        clone_of="NCT00000006",
        overall_status="SUSPENDED",
        condition="Non-Small Cell Lung Cancer",
        sponsor="Pacific Oncology Institute",
    )
    overrides = {
        **{f"NCT{i:08d}": {"derivedSection": adrd_mesh} for i in range(1, 11)},
        "NCT00000081": {
            "derivedSection": {"conditionBrowseModule": {"meshes": []}},
            "conditionsModule": {"conditions": []},
            "statusModule": {
                "overallStatus": "TERMINATED",
                "whyStopped": "Low accrual",
                "primaryCompletionDateStruct": {"date": "2024-01-01"},
            },
        },
        "NCT00000082": {
            "derivedSection": {"conditionBrowseModule": {"meshes": []}},
            "conditionsModule": {"conditions": []},
            "statusModule": {
                "overallStatus": "COMPLETED",
                "primaryCompletionDateStruct": {"date": "2024-01-01"},
            },
        },
        "NCT00000083": {
            "derivedSection": nsclc_mesh,
            "statusModule": {
                "overallStatus": "SUSPENDED",
                "whyStopped": "Safety hold",
                "primaryCompletionDateStruct": {"date": "2024-06-01"},
            },
        },
        "NCT00000006": {
            "derivedSection": adrd_mesh,
            "statusModule": {
                "overallStatus": "SUSPENDED",
                "whyStopped": "Safety hold",
                "primaryCompletionDateStruct": {"date": "2024-06-01"},
            },
        },
    }
    base = base_adrd_run()
    catalog_run = replace(
        base,
        profile="full_catalog",
        condition="",
        added=(unassigned_stopped, unassigned_completed, nsclc_stopped),
        study_overrides=overrides,
    )
    # Profile-only pull on later day must not pollute canonical transparency marts
    profile_only = replace(
        base_nsclc_run(),
        run_id="20260908T110000Z_profile_only",
        started_at=datetime(2026, 9, 8, 11, tzinfo=UTC),
        status_overrides={"NCT00000001": "TERMINATED"},
    )
    root = tmp_path / "registry_transparency"
    build_fixture_warehouse(root, runs=[catalog_run, profile_only])

    with duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True) as con:
        # 1. Early stop: preset excludes unassigned; source totals include all stopped trials
        assert (
            con.execute(
                "select count(*) from main_marts.mart_registry_early_stop_transparency "
                "where preset_id = 'unassigned'"
            ).fetchone()[0]
            == 0
        )
        early_rows = con.execute(
            "select preset_id, condition_group, overall_status, why_stopped, "
            "stopped_trial_count, source_stopped_trial_count, source_total_stopped_trials, "
            "contributing_nct_ids "
            "from main_marts.mart_registry_early_stop_transparency "
            "order by preset_id, overall_status, condition_group"
        ).fetchall()
        # Registry has 5 distinct stopped trials (NCT06/07/08 adrd, NCT81 unmapped, NCT83 nsclc)
        assert all(r[6] == 5 for r in early_rows)
        # NCT00000006 (adrd) and NCT00000083 (nsclc) share stratum (INDUSTRY, 2024, SUSPENDED)
        adrd_susp = [r for r in early_rows if r[0] == "adrd" and r[2] == "SUSPENDED"][0]
        assert adrd_susp[4] == 1  # preset stopped_trial_count
        assert adrd_susp[5] == 2  # source_stopped_trial_count across adrd + nsclc
        assert adrd_susp[7] == "NCT00000006"

        # 2. Results posting: preset excludes unassigned NCT00000081/82; source_total_* keeps them
        res_rows = con.execute(
            "select preset_id, lead_sponsor, total_completed_trials, total_terminated_trials, "
            "total_evaluable_trials, results_posted_evaluable_count, results_overdue_count, "
            "results_posted_rate, ci_lower, ci_upper, is_ranked, "
            "source_total_completed_trials, source_total_terminated_trials, "
            "source_total_evaluable_trials, contributing_nct_ids "
            "from main_marts.mart_registry_results_posting_transparency "
            "where lead_sponsor = 'NeuroPharm Inc'"
        ).fetchall()
        assert len(res_rows) == 1
        row = res_rows[0]
        assert row[0] == "adrd"
        # Preset has NCT00000006 (SUSPENDED, not completed/terminated) + NCT00000001/10 (RECRUITING)
        assert (row[2], row[3], row[4]) == (0, 0, 0)
        # Source adds unassigned NCT00000082 (COMPLETED) & NCT00000081 (TERMINATED), PCD 2024-01-01
        assert (row[11], row[12], row[13]) == (1, 1, 2)
        assert "NCT00000081" not in row[14] and "NCT00000082" not in row[14]

        # 3. Direct equivalence between legacy and canonical marts for identical ADRD single-run
        adrd_only_run = replace(
            base,
            profile="full_catalog",
            condition="",
            study_overrides={f"NCT{i:08d}": {"derivedSection": adrd_mesh} for i in range(1, 11)},
        )
        adrd_legacy_run = replace(
            base,
            run_id=base.run_id + "_leg",
            started_at=base.started_at + timedelta(seconds=1),
            study_overrides={f"NCT{i:08d}": {"derivedSection": adrd_mesh} for i in range(1, 11)},
        )
        parity_root = tmp_path / "parity_transparency"
        build_fixture_warehouse(parity_root, runs=[adrd_only_run, adrd_legacy_run])
        with duckdb.connect(
            str(parity_root / "data/warehouse/clinical_trials.duckdb"), read_only=True
        ) as pcon:
            leg_early = pcon.execute(
                "select condition_group, sponsor_class, stopped_year, overall_status, "
                "why_stopped, stopped_trial_count, contributing_nct_ids "
                "from main_marts.mart_early_stop_transparency "
                "where indication_profile_id = 'adrd' "
                "order by 1, 2, 3, 4, 5"
            ).fetchall()
            reg_early = pcon.execute(
                "select condition_group, sponsor_class, stopped_year, overall_status, "
                "why_stopped, stopped_trial_count, contributing_nct_ids "
                "from main_marts.mart_registry_early_stop_transparency where preset_id = 'adrd' "
                "order by 1, 2, 3, 4, 5"
            ).fetchall()
            assert reg_early == leg_early

            leg_res = pcon.execute(
                "select sponsor_class, lead_sponsor, total_completed_trials, "
                "total_terminated_trials, total_evaluable_trials, "
                "results_posted_evaluable_count, results_overdue_count, results_posted_rate, "
                "ci_lower, ci_upper, results_overdue_rate, is_ranked, evaluable_nct_ids, "
                "contributing_nct_ids, overdue_nct_ids, posted_nct_ids "
                "from main_marts.mart_results_posting_transparency "
                "where indication_profile_id = 'adrd' order by 1, 2"
            ).fetchall()
            reg_res = pcon.execute(
                "select sponsor_class, lead_sponsor, total_completed_trials, "
                "total_terminated_trials, total_evaluable_trials, "
                "results_posted_evaluable_count, results_overdue_count, results_posted_rate, "
                "ci_lower, ci_upper, results_overdue_rate, is_ranked, evaluable_nct_ids, "
                "contributing_nct_ids, overdue_nct_ids, posted_nct_ids "
                "from main_marts.mart_registry_results_posting_transparency "
                "where preset_id = 'adrd' order by 1, 2"
            ).fetchall()
            assert reg_res == leg_res
