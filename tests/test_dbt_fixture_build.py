"""Hermetic end-to-end test: fixture bronze snapshot through the full dbt graph.

The session fixture in tests/conftest.py builds one warehouse from
tests/fixtures/bronze_snapshot; every test in this module asserts against it.
No network, no real API, everything under tmp_path_factory.
"""

import json
import os
import re
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

# The files test_window_frames_partition_by_profile reads. A separate list from
# the one above on purpose: that one pins which contracts declare the column,
# this one pins which models' window frames must be profile-scoped, and Tasks
# 11-12 extend them differently -- mart_trial_similarity joins this list with
# Task 12's fix to its unprofiled row_number() (mart_trial_similarity.sql:73-74),
# not before. The two with no windows today are listed because the guard exists
# to catch the first window someone adds to them.
MARTS_WITH_PROFILE_SCOPED_WINDOWS = [
    "mart_trial_activity",
    "mart_site_overlap",
    "mart_condition_geography_trends",
    "mart_recruiting_competition",
    "mart_feasibility_priority_queue",
]

_SQL_COMMENT_RE = re.compile(r"--[^\n]*|\{#.*?#\}", re.DOTALL)
_OVER_KEYWORD_RE = re.compile(r"\bover\b", re.IGNORECASE)
_PARTITION_BY_RE = re.compile(r"\bpartition\s+by\b", re.IGNORECASE)
# A frame is either inline, `over (partition by ...)`, or a reference to a named
# window, `over trailing_3m`. Neither branch nests, so a frame this cannot read
# fails the count check in _window_frames rather than going unchecked.
_FRAME_RE = re.compile(
    r"\bover\b\s*(?:\((?P<inline>[^()]*)\)|(?P<named>[a-z_][a-z0-9_]*))",
    re.IGNORECASE,
)


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


def _window_frames(sql: str, model: str) -> list[str]:
    """The text of every window frame in a model's SQL, comment markup removed.

    Reads the whole file as one string because frames span lines, and resolves
    a named-window reference to its `window name as (...)` definition. The
    final assert is what keeps this honest: a frame shape the regex cannot read
    (nested parens, an `over` used some other way) shrinks the frame list while
    the keyword count stays put, and that has to be a failure rather than a
    shorter list of things the guard silently stopped checking.
    """
    code = _SQL_COMMENT_RE.sub(" ", sql)
    frames: list[str] = []
    for match in _FRAME_RE.finditer(code):
        inline = match.group("inline")
        if inline is not None:
            frames.append(inline)
            continue
        named = match.group("named")
        definition = re.search(rf"\b{named}\b\s+as\s*\(([^()]*)\)", code, re.IGNORECASE)
        assert definition is not None, f"{model}: 'over {named}' references an undeclared window"
        frames.append(definition.group(1))
    parsed = len(frames)
    keywords = len(_OVER_KEYWORD_RE.findall(code))
    assert parsed == keywords, (
        f"{model}: {keywords} window keywords but {parsed} readable frames -- "
        "the guard cannot check a frame it cannot parse"
    )
    return frames


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

    Only mart_site_overlap changes its total here: 28, the sum of the 14 rows
    this test measures per profile. The pooled 14 it displaces is derived, not
    measured -- nothing at this SHA builds the pre-migration table, and the
    derivation is that both profiles list the same 14 facilities, so pooling
    would merge them to 14. A facility shared between two profiles is not
    overlap within either profile's query scope. The other three keep their
    totals because the ADRD and NSCLC taxonomies are disjoint, so no
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
    # PREDICTED — never produced by a committed build; re-measure at Task 12
    assert _rows(
        fixture_project_root,
        "select indication_profile_id, count(*) from main_marts.mart_feasibility_priority_queue"
        " group by 1 order by 1",
    ) == [("adrd", 6), ("oncology_nsclc", 4)]
    # One queue row per (profile, condition_group, state, phase), and the key
    # now hashes the profile: the same segment in two profiles is two rows with
    # two keys. 6 + 4 = 10 matches mart_recruiting_competition's 6 + 4, so no
    # profile was dropped by the 5e/5f joins that replaced the cross joins.
    # PREDICTED — never produced by a committed build; re-measure at Task 12
    assert _rows(
        fixture_project_root,
        "select count(*), count(distinct priority_queue_key)"
        " from main_marts.mart_feasibility_priority_queue",
    ) == [(10, 10)]
    # PREDICTED — never produced by a committed build; re-measure at Task 12
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
    # PREDICTED — never produced by a committed build; re-measure at Task 12
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


def test_window_frames_partition_by_profile() -> None:
    """No frame in Task 10's marts may normalize across the whole warehouse.

    Asserted against the model source, not the built tables, because nothing on
    this fixture can discriminate the two: every segment holds exactly one
    recruiting trial, so a pooled `percent_rank()` and a per-profile one both
    return 0.0, and a global `rank()` over all-tied scores still yields (1, 1).
    Deleting a `partition by indication_profile_id` would leave every value
    assertion in this file green. It takes no fixture_project_root for the same
    reason -- the built warehouse is in blackout until Task 12, and this guard
    has to run today.

    Two rules over MARTS_WITH_PROFILE_SCOPED_WINDOWS, applied by _window_frames
    to the whole file (frames span lines, so a line-by-line scan would find
    almost nothing and look green): an empty or whitespace-only frame is banned
    outright, and a frame with a `partition by` must partition by the profile.
    That list is provisional -- Tasks 11-12 extend it, mart_trial_similarity
    joining when Task 12 gives its row_number() a profile. Not caught: a frame
    with an `order by` and no `partition by` at all, which reads as a deliberate
    global and stays a reviewer's job.
    """
    from src.utils.paths import project_root

    marts = project_root() / "dbt_clinical_trials/models/marts"
    for model in MARTS_WITH_PROFILE_SCOPED_WINDOWS:
        sql = (marts / f"{model}.sql").read_text(encoding="utf-8")
        for frame in _window_frames(sql, model):
            assert frame.strip(), f"{model}: unbounded frame 'over ()' normalizes across profiles"
            if _PARTITION_BY_RE.search(frame):
                assert "indication_profile_id" in frame, (
                    f"{model}: frame partitions without the profile: 'over ({frame.strip()})'"
                )


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
