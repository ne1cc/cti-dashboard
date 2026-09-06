"""Hermetic end-to-end test: fixture bronze snapshot through the full dbt graph.

The session fixture in tests/conftest.py builds one warehouse from
tests/fixtures/bronze_snapshot; every test in this module asserts against it.
No network, no real API, everything under tmp_path_factory.
"""

import json
import os
from pathlib import Path

import duckdb
import pytest

FIXTURE_RUN_ID = "20260901T120000Z_fixture01"

STAGING_WITH_PROFILE = [
    "stg_trials",
    "stg_trial_conditions",
    "stg_trial_interventions",
    "stg_trial_locations",
    "stg_trial_outcomes",
    "stg_trial_sponsors",
    "stg_trial_snapshots",
]


def test_dbt_build_passes_on_fixture_snapshot(fixture_project_root: Path) -> None:
    # The session fixture asserts dbt's exit code before returning; this pins
    # the artifacts the later assertions read.
    assert (fixture_project_root / "data/warehouse/clinical_trials.duckdb").exists()
    assert (fixture_project_root / "dbt_target/manifest.json").exists()


def _rows(root: Path, sql: str) -> list[tuple]:
    """Query the fixture warehouse with the fixture tree as the working directory.

    Staging models are views over relative globs, so DuckDB resolves them against
    the process cwd; without the chdir a staging test reads the developer's real
    data/ tree. Materialized marts are unaffected.
    """
    con = duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True)
    start = Path(os.getcwd())
    try:
        os.chdir(root)
        return con.execute(sql).fetchall()
    finally:
        os.chdir(start)
        con.close()


@pytest.mark.parametrize("model", STAGING_WITH_PROFILE)
def test_staging_models_expose_the_profile(fixture_project_root: Path, model: str) -> None:
    """Every row the warehouse reads must be attributable to exactly one
    profile; staging is where that becomes non-negotiable."""
    rows = _rows(
        fixture_project_root,
        f"select count(*), count(distinct indication_profile_id)"
        f" from main_staging.{model} where indication_profile_id is not null",
    )
    assert rows[0][0] > 0, f"{model} has no profile-stamped rows"
    assert rows[0][1] == 2, f"{model} should cover both fixture profiles, got {rows[0][1]}"


def test_silver_source_glob_is_profile_agnostic() -> None:
    """Silver is one shared tree keyed by indication_profile_id, not a tree per
    profile. Asserted here because the failure mode is a *successful* build with
    one profile's data missing."""
    import yaml

    from src.utils.paths import project_root

    sources = yaml.safe_load(
        (project_root() / "dbt_clinical_trials/models/staging/_sources.yml").read_text(
            encoding="utf-8"
        )
    )
    silver = next(s for s in sources["sources"] if s["name"] == "silver")
    assert silver["meta"]["external_location"] == (
        "read_parquet('data/silver/{name}/*.parquet', union_by_name=true)"
    )


def test_dim_trial_grain_is_trial_x_profile(fixture_project_root: Path) -> None:
    """20 rows for 10 NCT IDs: the same trial listed under two indication
    profiles is two facts, and neither may silently win.

    Before the composite-grain migration int_trial_status_history picks one row
    per (nct_id, snapshot_date), so one profile's 10 trials disappear and this
    returns (10, 10) with every existing uniqueness test still green.
    """
    assert _rows(
        fixture_project_root,
        "select count(*), count(distinct nct_id) from main_marts.dim_trial",
    ) == [(20, 10)]
    assert _rows(
        fixture_project_root,
        "select indication_profile_id, count(*) from main_marts.dim_trial group by 1 order by 1",
    ) == [("adrd", 10), ("oncology_nsclc", 10)]
    # Carried over unchanged from the pre-two-profile fixture: both runs hold the
    # same 10 trials, so all eight statuses must survive whatever the grain is.
    statuses = {
        r[0]
        for r in _rows(
            fixture_project_root,
            "select distinct current_overall_status from main_marts.dim_trial",
        )
    }
    assert statuses == {
        "RECRUITING",
        "ACTIVE_NOT_RECRUITING",
        "NOT_YET_RECRUITING",
        "COMPLETED",
        "ENROLLING_BY_INVITATION",
        "SUSPENDED",
        "TERMINATED",
        "WITHDRAWN",
    }


def test_fct_trial_snapshot_grain_carries_the_profile(fixture_project_root: Path) -> None:
    """One row per (profile, nct_id, snapshot_date): 20, and snapshot_key must
    be unique at that grain — md5(nct_id, snapshot_date) collides across
    profiles on the same date."""
    assert _rows(
        fixture_project_root,
        "select count(*), count(distinct snapshot_key) from main_marts.fct_trial_snapshot",
    ) == [(20, 20)]


def test_bridge_trial_condition_is_profile_scoped(fixture_project_root: Path) -> None:
    """The ADRD taxonomy and the NSCLC taxonomy disagree on dementia_relevance
    by construction, so a cross-indication leak is visible as a nonzero count."""
    per_profile = _rows(
        fixture_project_root,
        "select indication_profile_id, count(*) from main_marts.bridge_trial_condition"
        " group by 1 order by 1",
    )
    assert dict(per_profile)["adrd"] == 12
    assert dict(per_profile)["oncology_nsclc"] > 0
    assert _rows(
        fixture_project_root,
        "select count(*) from main_marts.bridge_trial_condition"
        " where indication_profile_id = 'oncology_nsclc' and dementia_relevance_flag",
    ) == [(0,)]


def test_mart_data_reliability_has_one_row_per_run_per_profile(
    fixture_project_root: Path,
) -> None:
    assert _rows(
        fixture_project_root,
        "select indication_profile_id, count(*) from main_marts.mart_data_reliability"
        " group by 1 order by 1",
    ) == [("adrd", 1), ("oncology_nsclc", 1)]


def test_mart_feasibility_priority_queue_is_profile_scoped(fixture_project_root: Path) -> None:
    per_profile = _rows(
        fixture_project_root,
        "select indication_profile_id, count(*) from main_marts.mart_feasibility_priority_queue"
        " group by 1 order by 1",
    )
    assert per_profile[0][0] == "adrd"
    assert _rows(
        fixture_project_root,
        "select count(*) > 1 from main_marts.mart_feasibility_priority_queue"
        " where indication_profile_id = 'oncology_nsclc'",
    ) == [(True,)]


def test_fct_trial_snapshot_one_current_record_per_trial(
    fixture_project_root: Path,
) -> None:
    # 20 rows, 20 of them current: the fixture's two runs land on one
    # snapshot_date on purpose, so each profile's own max(snapshot_date) marks
    # all ten of its rows current. With a per-profile second date the sum would
    # drop below the count; assert_one_current_record_per_trial.sql is what
    # forbids two current rows for the same (profile, nct_id).
    assert _rows(
        fixture_project_root,
        "select count(*), sum(case when current_record_flag then 1 else 0 end) "
        "from main_marts.fct_trial_snapshot",
    ) == [(20, 20)]


def test_fct_trial_site_us_scope(fixture_project_root: Path) -> None:
    # 28 = the fixture's 14 U.S. facility listings under each of the two
    # profiles. Reaching it needed indication_profile_id in the mart's qualify
    # partition, not just in its column list: a profile-free qualify keeps one
    # row per nct_id and silently drops one profile's sites.
    assert _rows(fixture_project_root, "select count(*) from main_marts.fct_trial_site") == [(28,)]
    assert (
        _rows(
            fixture_project_root,
            "select state_normalized from main_marts.fct_trial_site "
            "where not regexp_matches(state_normalized, '^[A-Z]{2}$')",
        )
        == []
    )
    assert _rows(
        fixture_project_root,
        "select count(*) from main_marts.fct_trial_site where facility_normalized in "
        "('charite memory clinic', 'toronto memory program')",
    ) == [(0,)]


def test_bridge_trial_condition_taxonomy_groups(fixture_project_root: Path) -> None:
    # 25 = 12 adrd + 13 oncology_nsclc rows, both profiles' condition groups
    # mapped by their own taxonomy.
    assert _rows(
        fixture_project_root,
        "select count(*) from main_marts.bridge_trial_condition",
    ) == [(25,)]
    # Scoped to adrd rather than widened to the union of both taxonomies: the
    # exact ADRD expectation is the useful one, and a union would keep passing
    # if one profile's groups leaked into the other. The nsclc half is pinned
    # by test_bridge_trial_condition_is_profile_scoped.
    groups = {
        r[0]
        for r in _rows(
            fixture_project_root,
            "select distinct condition_group from main_marts.bridge_trial_condition "
            "where indication_profile_id = 'adrd'",
        )
    }
    assert groups == {
        "alzheimers_disease",
        "cognitive_impairment_other",
        "frontotemporal_dementia",
        "lewy_body_dementia",
        "mild_cognitive_impairment",
        "non_dementia_other",
    }


def test_mart_feasibility_priority_queue_shape(fixture_project_root: Path) -> None:
    assert _rows(
        fixture_project_root,
        "select count(*) from main_marts.mart_feasibility_priority_queue",
    ) == [(6,)]
    assert _rows(
        fixture_project_root,
        "select count(*) from main_marts.mart_feasibility_priority_queue "
        "where feasibility_review_priority_score < 0 "
        "or feasibility_review_priority_score > 1",
    ) == [(0,)]


def test_mart_data_reliability_reconciles(fixture_project_root: Path) -> None:
    assert _rows(
        fixture_project_root,
        "select status, manifest_record_count, trial_row_count, "
        "manifest_reconciled_flag, unique_nct_flag "
        f"from main_marts.mart_data_reliability where ingestion_run_id = '{FIXTURE_RUN_ID}'",
    ) == [("success", 10, 10, True, True)]


def test_marts_contracts_enforced(fixture_project_root: Path) -> None:
    manifest = json.loads(
        (fixture_project_root / "dbt_target/manifest.json").read_text(encoding="utf-8")
    )
    marts = {
        node["name"]: node
        for node in manifest["nodes"].values()
        if node["resource_type"] == "model"
        and node["original_file_path"].startswith("models/marts/")
    }
    assert len(marts) == 16
    for name, node in marts.items():
        assert node["contract"]["enforced"] is True, name
        assert {c["name"] for c in node["columns"].values()}, name
