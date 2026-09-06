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

# The five segment marts Task 10 re-grained from (segment) to
# (profile x segment). The shared dimensions and mart_trial_similarity are
# Tasks 11-12.
SEGMENT_MARTS_WITH_PROFILE = [
    "mart_trial_activity",
    "mart_site_overlap",
    "mart_condition_geography_trends",
    "mart_recruiting_competition",
    "mart_feasibility_priority_queue",
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


def test_segment_marts_are_grained_per_profile(fixture_project_root: Path) -> None:
    """The four segment marts that build in this commit, at profile x segment.

    Only mart_site_overlap changes its total here (14 pooled -> 28): both
    profiles list the same 14 facilities, and a facility shared between two
    profiles is not overlap within either profile's query scope. The other three
    keep their totals because the ADRD and NSCLC taxonomies are disjoint, so no
    (condition_group, state, phase) key existed for pooling to merge -- their
    partition rewrites are correctness by construction, and this fixture cannot
    discriminate them. The asymmetric-fixture measurement that can is recorded
    in the message of commit 5a1930f.
    """
    expected = {
        "mart_trial_activity": [("adrd", 16), ("oncology_nsclc", 18)],
        "mart_site_overlap": [("adrd", 14), ("oncology_nsclc", 14)],
        "mart_condition_geography_trends": [("adrd", 14), ("oncology_nsclc", 16)],
        "mart_recruiting_competition": [("adrd", 6), ("oncology_nsclc", 4)],
    }
    for model, want in expected.items():
        rows = _rows(
            fixture_project_root,
            f"select indication_profile_id, count(*) from main_marts.{model} group by 1 order by 1",
        )
        assert rows == want, model
    # mart_recruiting_competition's band is a distribution, so it is worth
    # recording what it is on this fixture: every segment holds exactly one
    # recruiting trial, percent_rank() ties all of them at 0.0 and every band
    # is 'low' -- the partition-by-profile rewrite is therefore NOT observable in
    # these values, and asserting a non-degenerate band here would be a lie.
    assert _rows(
        fixture_project_root,
        "select indication_profile_id, min(density_percentile),"
        " max(density_percentile), count(distinct competition_signal_band)"
        " from main_marts.mart_recruiting_competition group by 1 order by 1",
    ) == [("adrd", 0.0, 0.0, 1), ("oncology_nsclc", 0.0, 0.0, 1)]


def test_mart_feasibility_priority_queue_shape(fixture_project_root: Path) -> None:
    """Segments pinned per profile, replacing one pooled count.

    The leg this replaces was `count(*) == [(6,)]`. Measured values here come
    from the fixture tree built with these models. Two honest caveats, both
    about what this fixture cannot show:

    * Step 5d's `run_reliability` reads mart_data_reliability.indication_profile_id,
      which Task 12 adds, so the queue does not build in this commit's state.
      The numbers below were measured with that one column forward-ported in a
      scratch copy of the fixture tree; the queue legs are therefore a
      prediction for the committed tree until Task 12 lands, and the ledger
      records them as errors, not passes.
    * `[(1, 1)]`, not the plan's `[(1, 6)]`: every fixture segment holds exactly
      one recruiting trial, so every min-max denominator is 0, all ten scores
      are 0.0, and rank() ties every row at 1. A spread across 1..6 needs
      uneven segment sizes, which this fixture does not have -- that is a
      prediction about a richer fixture (Task 12's divergent-date work), not an
      assertion here. priority_rank restarting per profile is likewise
      unverifiable while all scores tie.
    """
    assert _rows(
        fixture_project_root,
        "select indication_profile_id, count(*) from main_marts.mart_feasibility_priority_queue"
        " group by 1 order by 1",
    ) == [("adrd", 6), ("oncology_nsclc", 4)]
    # One queue row per (profile, condition_group, state, phase), and the key
    # now hashes the profile: the same segment in two profiles is two rows with
    # two keys. 6 + 4 = 10 matches mart_recruiting_competition's 6 + 4, so no
    # profile was dropped by the 5e/5f joins that replaced the cross joins.
    assert _rows(
        fixture_project_root,
        "select count(*), count(distinct priority_queue_key)"
        " from main_marts.mart_feasibility_priority_queue",
    ) == [(10, 10)]
    for profile in ("adrd", "oncology_nsclc"):
        ranks = _rows(
            fixture_project_root,
            "select min(priority_rank), max(priority_rank)"
            " from main_marts.mart_feasibility_priority_queue"
            f" where indication_profile_id = '{profile}'",
        )
        assert ranks == [(1, 1)], profile
    # Carried forward unchanged: no score may leave [0, 1] now that the
    # normalization is partitioned per profile.
    assert _rows(
        fixture_project_root,
        "select count(*) from main_marts.mart_feasibility_priority_queue "
        "where feasibility_review_priority_score < 0 "
        "or feasibility_review_priority_score > 1",
    ) == [(0,)]


def test_segment_mart_contracts_declare_the_profile(fixture_project_root: Path) -> None:
    """Each of the five segment marts groups by indication_profile_id, so each
    must say so in its contract as a VARCHAR not_null column. A mart that keeps
    the column only in its select list still builds, and its docs then describe
    a grain the table does not have."""
    manifest = json.loads(
        (fixture_project_root / "dbt_target/manifest.json").read_text(encoding="utf-8")
    )
    models = {
        node["name"]: node
        for node in manifest["nodes"].values()
        if node["resource_type"] == "model"
        and node["original_file_path"].startswith("models/marts/")
    }
    not_null_models = {
        dep.split(".")[-1]
        for node in manifest["nodes"].values()
        if node["resource_type"] == "test"
        and node.get("column_name") == "indication_profile_id"
        and node.get("test_metadata", {}).get("name") == "not_null"
        for dep in node["depends_on"]["nodes"]
    }
    for name in SEGMENT_MARTS_WITH_PROFILE:
        column = models[name]["columns"]["indication_profile_id"]
        assert column["data_type"] == "VARCHAR", name
        assert name in not_null_models, name


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
