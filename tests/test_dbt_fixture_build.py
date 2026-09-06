"""Hermetic end-to-end test: fixture bronze snapshot through the full dbt graph.

tests/conftest.py builds four warehouses for this module and a test names the one
it reads: `fixture_project_root` is the base two-profile build (17 of the 26 test
functions), `divergent_fixture_root` adds a second ADRD run on a different date
plus an un-stamped NSCLC run (6), and `solo_fixture_roots` holds one warehouse per
profile for the invariance comparison (1). Those three numbers count requests, not
functions: 22 of the 26 take a warehouse, and two ask for two roots each, which is
why they sum past 26. The remaining 4 take no fixture — they read model source,
`_marts.yml` or `models/staging/_sources.yml`, so they still guard when a build
breaks. No network, no real API, everything under tmp_path_factory.
"""

import json
import os
import re
from datetime import date
from pathlib import Path

import duckdb
import pytest

# These are the strings tests/conftest.py writes into the warehouses it builds, so
# this module imports them rather than keeping a second copy: this file used to
# re-declare FIXTURE_RUN_ID locally, which was harmless while it was the only
# warehouse, and became a drift hazard the moment the divergent fixture's three
# other run ids were needed -- an edited conftest id would leave the assertions
# describing a warehouse that no longer exists. Same pattern as
# tests/test_orchestration_assets.py:10.
from tests.conftest import (
    DIVERGENT_ADRD_RUN2_ID,
    FIXTURE_RUN_ID,
    NSCLC_FIXTURE_RUN_ID,
    UNSTAMPED_MANIFEST_PROFILE,
    UNSTAMPED_NSCLC_RUN_ID,
)

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

# The three shared dimensions Task 11 turned into per-profile views of global
# entities: their statistics and their keys are both (entity, profile). Used by
# test_shared_dimension_contracts_state_the_per_profile_grain, which reads the
# contract source rather than the built warehouse so that it keeps reporting when a
# build is broken -- a fixture-backed guard errors instead of naming the drift,
# which is how the R27 blackout hid this module for six tasks: the fixture build
# went red at Task 6's bd60666 and green again at Task 12's 19fabf2.
SHARED_DIMS_WITH_PROFILE = ["dim_condition", "dim_sponsor", "dim_geography"]

# The files test_window_frames_partition_by_profile reads. A separate list from
# SEGMENT_MARTS_WITH_PROFILE on purpose: that one pins which contracts declare
# the column, this one pins which models' window frames must be profile-scoped,
# and they cover different files by design. mart_trial_similarity joined this
# list on 2026-09-06, the same change that gave its
# `row_number() over (partition by indication_profile_id, nct_id_a ...)` a
# profile: an unprofiled rank there re-cuts one trial's top-25 against whatever
# other profiles happen to be loaded, which is the same silent-rescaling defect
# class the five segment marts were added for. The two models here with no
# windows are listed because the guard exists to catch the first window someone
# adds to them. Task 11's three shared dims join neither list: checked
# 2026-09-06, the comment-stripped text of dim_condition.sql, dim_sponsor.sql and
# dim_geography.sql holds no `over` token, which is a static reading of the
# source rather than a build measurement. A window added to one of them later
# would be unchecked until its name joined this list, which is the same
# obligation mart_trial_similarity carried until it joined. fct_trial_site came
# with the coverage leg: scanning models/marts/ for the `over` token on
# 2026-09-06 found exactly five files, and its `qualify row_number() over
# (partition by indication_profile_id, nct_id, ...)` is a per-profile latest-run
# pick that no earlier task's list mentioned.
MARTS_WITH_PROFILE_SCOPED_WINDOWS = [
    "mart_trial_activity",
    "mart_site_overlap",
    "mart_condition_geography_trends",
    "mart_recruiting_competition",
    "mart_feasibility_priority_queue",
    "mart_trial_similarity",
    "fct_trial_site",
]

_SQL_COMMENT_RE = re.compile(r"--[^\n]*|\{#.*?#\}", re.DOTALL)
_OVER_KEYWORD_RE = re.compile(r"\bover\b", re.IGNORECASE)
_PARTITION_BY_RE = re.compile(r"\bpartition\s+by\b", re.IGNORECASE)
# A surrogate key and the alias it is emitted as, so a guard can ask "what does
# `sponsor_key` hash?" instead of "what does the first key-ish call in this file
# hash?". The optional `}}` is the Jinja call closing. Nothing here needs
# re.DOTALL: the pattern contains no `.` metacharacter, and the two places a
# model can break the call across lines -- between `[...]` and `)`, and between
# the closing `}}` and `as` -- are spanned by `\s*`, which matches newlines on
# its own. The flag was measured to be a no-op: running this pattern with and
# without it over every file in models/marts/ returns the same alias/argument
# pairs (2026-09-06), so it is dropped rather than left as a claim about the
# regex that the regex does not implement.
_SURROGATE_KEY_RE = re.compile(
    r"generate_surrogate_key\(\s*\[(?P<args>[^\]]*)\]\s*\)\s*(?:\}\})?\s*"
    r"as\s+(?P<alias>[a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)
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


def _strip_sql_comments(sql: str) -> str:
    r"""Model text with `--` line comments and Jinja `{# #}` markup blanked out.

    The single mechanism both source-level guards in this module read through.
    A guard that scans raw text can be satisfied by a comment, which makes it a
    decoration rather than a check; comments are also where an honest note about
    a column that a model deliberately lacks would live, so stripping cuts both
    ways -- it stops a comment faking compliance and stops one causing a false
    failure.

    Line structure survives *one* branch of the substitution, not both: the
    `--[^\n]*` alternative stops short of the terminating newline, so a line
    comment becomes a space on its own line and line-oriented reading still
    works; the `{# ... #}` alternative runs under re.DOTALL, so a block comment
    spanning lines collapses those lines into a single space. Nothing in this
    module reads the result line by line -- both guards search the whole text --
    so the asymmetry costs nothing today, but a guard that counted lines would
    have to know which branch fired.
    """
    return _SQL_COMMENT_RE.sub(" ", sql)


def _surrogate_key_inputs(sql: str, alias: str) -> list[str] | None:
    """The columns a model's `generate_surrogate_key([...]) as <alias>` hashes, in
    the order they are given.

    A list, not a set: `macros/generate_surrogate_key.sql` folds its `field_list`
    in order, so md5('a||b') and md5('b||a') are different keys and a *reorder*
    on one side of a join orphans every row exactly as surely as dropping a
    column does. A set comparison cannot see that.

    Returns None when the file emits no such key, so the caller decides whether
    that is a failure and names the file. Table qualification is stripped:
    `macros/generate_surrogate_key.sql` concatenates its inputs with `||` over
    `coalesce(cast(<field> as varchar), '__null__')`, so `sponsor_normalized`
    and `s.sponsor_normalized` hash to the same value in a one-table model and a
    joined one -- the inputs are what two sides of a join must agree on, not the
    spelling.

    First match wins: if a file ever emits the same alias twice, the guard reads
    the first occurrence and the second is unchecked. No model does today
    (counted over models/marts/ on 2026-09-06), but phase 1 added keys, so the
    limitation is stated where a future edit would meet it rather than in a
    comment nobody reads.
    """
    for match in _SURROGATE_KEY_RE.finditer(sql):
        if match.group("alias") != alias:
            continue
        return [
            arg.strip().strip("'\"").split(".")[-1]
            for arg in match.group("args").split(",")
            if arg.strip()
        ]
    return None


def _window_frames(sql: str, model: str) -> list[str]:
    """The text of every window frame in a model's SQL, comment markup removed.

    Reads the whole file as one string because frames span lines, and resolves
    a named-window reference to its `window name as (...)` definition. The
    final assert is what keeps this honest: a frame shape the regex cannot read
    (nested parens, an `over` used some other way) shrinks the frame list while
    the keyword count stays put, and that has to be a failure rather than a
    shorter list of things the guard silently stopped checking.
    """
    code = _strip_sql_comments(sql)
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
    """20 rows for 10 NCT IDs and 20 trial keys: the same trial listed under two
    indication profiles is two facts, and neither may silently win.

    Before the composite-grain migration int_trial_status_history picks one row
    per (nct_id, snapshot_date), so one profile's 10 trials disappear and this
    returns (10, 10) with every existing uniqueness test still green.

    The third value is the one Amendment A15 item 3 asked for. `trial_key` is the
    key this migration rehashes -- md5(nct_id) becomes md5(nct_id,
    indication_profile_id) -- and the row count alone cannot see it: 20 rows
    survive either hash. What collapses is `count(distinct trial_key)`, which
    reads 10 while the key is trial-only and 20 once it is trial x profile. The
    declared dbt `unique` test on the column does fire inside the session build,
    but it fails the whole build rather than naming this grain, so until now the
    widened key had no committed assertion that reads as its own statement.
    """
    assert _rows(
        fixture_project_root,
        "select count(*), count(distinct nct_id), count(distinct trial_key)"
        " from main_marts.dim_trial",
    ) == [(20, 10, 20)]
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


def test_fct_trial_snapshot_currency_is_per_profile(divergent_fixture_root: Path) -> None:
    """`current_record_flag` follows each profile's own latest snapshot, not the
    warehouse's.

    The leg the test above cannot provide: its header note says both fixture
    profiles land on one date on purpose, so `max(snapshot_date)` per profile and
    `max(snapshot_date)` over the whole table select the same rows there and no
    value assertion on that warehouse can tell the two apart. Here ADRD ran twice
    a week apart and NSCLC's only complete run predates ADRD's second, so the
    global max (2026-09-08) is strictly greater than NSCLC's own (2026-09-01).
    Currency computed globally would mark all ten NSCLC rows stale and leave that
    profile with zero current records -- no row disappears, no key repeats, and
    every count in the suite other than this one stays green.

    Values measured on 2026-09-06 against a `dbt build` of this warehouse.
    """
    assert _rows(
        divergent_fixture_root,
        "select indication_profile_id, snapshot_date, count(*),"
        " sum(case when current_record_flag then 1 else 0 end)"
        " from main_marts.fct_trial_snapshot group by 1,2 order by 1,2",
    ) == [
        # ADRD's first run is history now, so none of its rows are current...
        ("adrd", date(2026, 9, 1), 10, 0),
        ("adrd", date(2026, 9, 8), 27, 27),
        # ...while NSCLC's earlier run is still current, *in its own profile*.
        ("oncology_nsclc", date(2026, 9, 1), 10, 10),
    ]
    assert _rows(
        divergent_fixture_root,
        "select count(distinct indication_profile_id) from main_marts.fct_trial_snapshot"
        " where current_record_flag",
    ) == [(2,)]
    # The three runs above, by run id. The fixture's fourth run is absent, and
    # that is deliberate rather than incidental: its manifest disagrees with its
    # silver about the profile, which orphans it from the status history -- see
    # test_reliability_prefers_the_silver_profile_when_the_manifest_disagrees.
    assert _rows(
        divergent_fixture_root,
        "select ingestion_run_id, count(*), min(snapshot_date) from"
        " main_marts.fct_trial_snapshot group by 1 order by 1",
    ) == [
        (FIXTURE_RUN_ID, 10, date(2026, 9, 1)),
        (NSCLC_FIXTURE_RUN_ID, 10, date(2026, 9, 1)),
        (DIVERGENT_ADRD_RUN2_ID, 27, date(2026, 9, 8)),
    ]
    # dim_trial is the consumer of that currency: one row per trial *at its own
    # profile's latest run*, so ADRD grows to its second run's 27 listings while
    # NSCLC keeps its 10, and the widened trial_key still holds at 37 for 37 rows.
    assert _rows(
        divergent_fixture_root,
        "select indication_profile_id, count(*), count(distinct nct_id),"
        " count(distinct trial_key) from main_marts.dim_trial group by 1 order by 1",
    ) == [("adrd", 27, 27, 27), ("oncology_nsclc", 10, 10, 10)]


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

    The leg this replaces was `count(*) == [(6,)]`.

    All four values below were PREDICTED when Task 10 wrote them: while R27's CI
    blackout held this mart produced no table, so they were measured on a scratch
    copy of the fixture tree with mart_data_reliability.indication_profile_id
    forward-ported. Amendment A16 item 4 forbids inheriting that. Re-measured on
    2026-09-06 against the committed `dbt build` of these models (green: 36
    success + 137 pass, 0 non-pass nodes), every one of the four held exactly --
    (6, 4) per profile, (10, 10) rows/keys, (1, 1) ranks for both profiles, 0
    out-of-range scores. Nothing here is a prediction any more.

    `[(1, 1)]` is still not the plan's `[(1, 6)]`, and that is the fixture's
    doing rather than the model's: every segment on this warehouse holds exactly
    one recruiting trial, so every min-max denominator is 0, all ten scores are
    0.0, and rank() ties every row at 1. A single-snapshot-date fixture cannot
    show anything else, and this module's header note in tests/conftest.py
    explains why that date is pinned on purpose. A real spread is a property of
    multi-snapshot, unevenly-sized segments, so it is asserted in
    `test_priority_rank_spreads_within_a_profile_on_multi_snapshot_history`
    against `divergent_fixture_root`, not here; keeping the two claims in
    separate tests is A16 item 4's point, so neither can be read as covering the
    other.
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
    # All-tied scores on this fixture, so rank() returns 1 everywhere. See the
    # docstring: the spread lives in
    # test_priority_rank_spreads_within_a_profile_on_multi_snapshot_history.
    for profile in ("adrd", "oncology_nsclc"):
        ranks = _rows(
            fixture_project_root,
            "select min(priority_rank), max(priority_rank)"
            " from main_marts.mart_feasibility_priority_queue"
            f" where indication_profile_id = '{profile}'",
        )
        assert ranks == [(1, 1)], profile
    # Carried forward unchanged: no score may leave [0, 1] now that the
    # normalization is partitioned per profile. Measured 2026-09-06; on this
    # fixture every score *is* 0.0, so the leg bounds the range rather than
    # exercising it -- test_profile_scores_are_bit_identical_built_alone_or_alongside
    # reads the non-degenerate scores.
    assert _rows(
        fixture_project_root,
        "select count(*) from main_marts.mart_feasibility_priority_queue "
        "where feasibility_review_priority_score < 0 "
        "or feasibility_review_priority_score > 1",
    ) == [(0,)]


def test_priority_rank_spreads_within_a_profile_on_multi_snapshot_history(
    divergent_fixture_root: Path,
) -> None:
    """The rank spread Task 10 could only predict, measured on the divergent build.

    Its warehouse cannot produce a spread at all: one recruiting trial per
    segment makes every min-max denominator 0, every score 0.0, and `rank()` ties
    the lot at 1, so `[(1, 1)]` there says nothing about the partition. Here
    ADRD's second run puts a second recruiting trial into the
    (alzheimers_disease, NC, PHASE3) segment while its profile's other segments
    hold one, the density and growth inputs stop being constant, and the scores
    and ranks finally spread: 8 rows over 4 distinct scores and 4 distinct ranks,
    ranks running 1 to 5 because the ties are real (`rank()` skips), two priority
    bands instead of one, and a top score of 0.5 against a floor of 0.0.

    NSCLC stays at (1, 1) with a single distinct score, and that is half the
    point. Per-profile normalization means ADRD's new spread must not move
    NSCLC's numbers: a `min()/max()` that lost its `partition by
    indication_profile_id` would rescale NSCLC against ADRD's 0-0.5 range and
    take its ranks off 1. Task 10's static window guard reads the frames; this
    reads the values, which is the pairing
    `test_window_frames_partition_by_profile` says it is guarding.
    """
    assert _rows(
        divergent_fixture_root,
        "select indication_profile_id, min(priority_rank), max(priority_rank), count(*),"
        " count(distinct priority_rank), count(distinct feasibility_review_priority_score),"
        " min(feasibility_review_priority_score), max(feasibility_review_priority_score),"
        " count(distinct priority_band)"
        " from main_marts.mart_feasibility_priority_queue group by 1 order by 1",
    ) == [
        ("adrd", 1, 5, 8, 4, 4, 0.0, 0.5, 2),
        ("oncology_nsclc", 1, 1, 4, 1, 1, 0.0, 0.0, 1),
    ]
    # The top segment is the one whose recruiting count doubled, and it is the
    # only row the base fixture could never produce: a density input of 2 against
    # a profile maximum of 2 normalizes to 1.0, which is what lifts this row.
    assert _rows(
        divergent_fixture_root,
        "select condition_group, state_normalized, phase_normalized, recruiting_trial_count,"
        " priority_rank from main_marts.mart_feasibility_priority_queue"
        " where indication_profile_id = 'adrd' and priority_rank = 1",
    ) == [("alzheimers_disease", "NC", "PHASE3", 2, 1)]
    # Item L's tripwire, asserted as a literal rather than as `< 25` so it goes
    # red the moment the fixture grows instead of quietly becoming reachable.
    # `analyses/analysis_top_priority_segments.sql` ends with `order by
    # indication_profile_id, priority_rank limit 25`, and priority_rank is a
    # rank(), so tied segments share a value the order by cannot separate: if the
    # 25-row cut ever lands *inside* a tie band, which rows appear is whatever the
    # sort happens to emit. Measured 2026-09-06, it cannot land there from this
    # fixture -- 12 queue rows in total, 8 in the busiest profile -- so the limit
    # never fires and the tie bands above are a presentation-order matter only.
    # On a real warehouse the exposure is larger and much closer. Measured
    # 2026-09-06 by querying main's checkout read-only
    # (../../../data/warehouse/clinical_trials.duckdb): 448 queue rows over 79
    # distinct ranks, and rows with priority_rank <= 25 number exactly 25 because
    # the band at rank 25 is one row wide. That is deterministic today by luck; the
    # very next band (rank 26) holds 31 rows, and the largest bands hold 153 and 63,
    # so one score change moves the cut inside a band and which 25 segments a reader
    # sees becomes sort-order chosen. What turns this from latent into live per
    # profile is more than 25 segments, which is the number pinned below.
    assert _rows(
        divergent_fixture_root,
        "select max(c) from (select indication_profile_id, count(*) c"
        " from main_marts.mart_feasibility_priority_queue group by 1)",
    ) == [(8,)]


_INVARIED_MODELS = [
    "dim_trial",
    "fct_trial_snapshot",
    "bridge_trial_condition",
    "bridge_trial_sponsor",
    "fct_trial_site",
    "dim_condition",
    "dim_sponsor",
    "dim_geography",
    "mart_trial_activity",
    "mart_site_overlap",
    "mart_condition_geography_trends",
    "mart_recruiting_competition",
    "mart_feasibility_priority_queue",
    "mart_trial_similarity",
]


def test_profile_scores_are_bit_identical_built_alone_or_alongside(
    divergent_fixture_root: Path,
    solo_fixture_roots: dict[str, Path],
) -> None:
    """A profile's whole built state, every column of every model, must not depend
    on which other profiles happen to be loaded.

    This is the property the migration is for, stated as the strongest form the
    fixtures can support: the same bronze built alone and built next to a second
    profile has to come out row-for-row and byte-for-byte identical for that
    profile. Not just the scores -- `select *`, ordered by every column, compared
    as tuples, so a changed rank, a rescaled percentile, a re-cut top-25, a
    different surrogate key or a row that only exists in one of the two builds all
    fail the same way.

    Why `divergent_fixture_root` and not the base one: invariance is unfalsifiable
    on a warehouse whose two profiles hold identical trials, because a global
    normalization and a per-profile one then agree by construction (the base
    fixture's scores are all 0.0 and its ranks all 1 -- see
    `test_mart_feasibility_priority_queue_shape`). Here ADRD's numbers depend on
    its own 8 segments and NSCLC's on its own 4, so a forgotten
    `partition by indication_profile_id` *can* surface as a byte difference.

    Which shapes surface is measured (2026-09-06), not assumed. Pooling the queue's
    five min/max normalizers -- frames partitioned on the profile *alone*, 15
    occurrences -- goes red at `mart_feasibility_priority_queue/adrd: 8 rows built
    alongside another profile vs 8 built alone`. Pooling
    `mart_recruiting_competition`'s three `percent_rank() over (partition by
    indication_profile_id, snapshot_date)` frames stays green: no value moves on
    this fixture, so this test cannot see that shape. The shape is not unprotected,
    only unprotected *here*: the same poison fails
    `test_window_frames_partition_by_profile` in 0.3 s, naming the model and the
    frame. Read the loop below as "every model gets compared", not "every frame
    shape gets caught".

    `mart_data_reliability` is deliberately not in the list: it is one row per
    ingestion run, and NSCLC's second warehouse run exists only in the divergent
    build to carry the manifest/silver disagreement below, so its row counts
    differ by design. Its profile column is asserted where it belongs, in
    `test_reliability_prefers_the_silver_profile_when_the_manifest_disagrees`.
    """
    for profile, solo_root in solo_fixture_roots.items():
        for model in _INVARIED_MODELS:
            query = (
                f"select * from main_marts.{model}"
                f" where indication_profile_id = '{profile}' order by all"
            )
            alone = _rows(solo_root, query)
            together = _rows(divergent_fixture_root, query)
            # A comparison of two empty lists proves nothing, and a typo in the
            # profile literal would produce exactly that.
            assert alone, f"{model}: profile {profile} is empty built alone"
            assert together == alone, (
                f"{model}/{profile}: {len(together)} rows built alongside another profile vs"
                f" {len(alone)} built alone -- a per-profile number changed because a second"
                " indication was loaded"
            )


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


def test_shared_dimensions_are_per_profile(fixture_project_root: Path) -> None:
    """A sponsor or state listed by both indications gets two rows, each keyed
    to its profile; dim_condition gets two rows only when its entity is shared,
    and on this fixture it never is.

    The name says *per profile*, not *per-profile stats*, and that is deliberate:
    this test asserts row multiplicity and key grain, never the values of
    ``trial_count`` or ``listed_site_count``, because on a warehouse whose two
    profiles list identical trials those values cannot be discriminated (second
    bullet below). The statistics are asserted in
    test_shared_dimension_stats_are_scoped_to_their_profile.

    Every number below was measured on 2026-09-06 against a copy-tree build of
    the committed models -- the recipe Tasks 8-10 used, since this module was in
    the R27 blackout then -- and the pre-Task-11 half was measured by rebuilding
    the four HEAD versions of the changed models in a second copy. Task 12's
    green fixture build re-verified every one of them unchanged. Three limits,
    all of them the fixture's:

    * Row multiplicity IS provable, and `>` is the discriminating form rather
      than `>=`: the pre-Task-11 dims grouped by the entity alone, so a
      regression that drops ``indication_profile_id`` from the group by returns
      (7, 7) for dim_sponsor and (9, 9) for dim_geography and turns both the
      ``>`` and the exact-value assert red. ``>=`` would have passed there.
    * Value scoping is NOT provable here, and that was measured rather than
      assumed. Both profiles list the same ten nct_ids -- the dim_trial
      self-join at the bottom pins that at 10 -- so every shared entity's
      per-profile ``trial_count`` equals its pooled ``count(distinct nct_id)``:
      14/14 dim_sponsor rows and 18/18 dim_geography rows measured equal to the
      unscoped number, and their two rows per entity are identical. No value
      assertion can tell "counted within its profile" from "counted globally
      then copied" while membership is symmetric, so none is made *here*.
      Amendment A15 item 2's divergent warehouse breaks the symmetry, and
      Task 11's prediction that the scoping leg belongs there is now a
      measurement: `test_shared_dimension_stats_are_scoped_to_their_profile`
      asserts against it, where 4 of 14 dim_sponsor rows and 4 of 18
      dim_geography rows differ from the pooled number.
      That leaves the four fixture-shape literals below as literals about *this*
      warehouse, which is the point of building a second one rather than
      re-dating this: ``(14, 7)`` / ``(18, 9)``, ``dupes == [(7,)]`` /
      ``[(9,)]``, dim_condition's ``[("adrd", 10), ("oncology_nsclc", 11)]``
      partition with its ``(21, 21, 2)`` rows/keys/profiles triple, and the
      ``[(10,)]`` cross-profile dim_trial self-join. All four were re-checked on
      2026-09-06 after the divergent fixture landed and all four still hold --
      none of them was ever a prediction about Task 12's fixture, only
      candidates to be read that way. The last one keeps its tripwire job: if a
      future model change makes the base fixture's membership asymmetric, it
      goes red and the non-discrimination argument two sentences up has to be
      re-made rather than inherited.
    * dim_condition is excluded from the multiplicity loop by measurement: 21
      rows, 21 distinct condition_normalized, 0 entities listed by both
      profiles. condition_normalized is taxonomy-free text (normalize_text of
      the raw string) and the two bronze fixtures describe different
      conditions, so the entity sets are disjoint and ``count(*) >
      count(distinct entity)`` is false for a reason unrelated to this task.
      Its profile partition and composite-key uniqueness are asserted instead.
    """
    shapes = {
        "dim_sponsor": ("sponsor_normalized", 14, 7),
        "dim_geography": ("state_code", 18, 9),
    }
    for model, (entity, rows_want, entities_want) in shapes.items():
        rows = _rows(
            fixture_project_root,
            f"select count(*), count(distinct {entity}) from main_marts.{model}",
        )
        assert rows[0][0] > rows[0][1], f"{model}: rows should be entity x profile"
        assert rows == [(rows_want, entities_want)], model
        dupes = _rows(
            fixture_project_root,
            f"select count(*) from (select {entity}, count(*) c from main_marts.{model}"
            f" group by 1 having c > 1)",
        )
        assert dupes == [(entities_want,)], f"{model}: every entity should be shared"

    assert _rows(
        fixture_project_root,
        "select indication_profile_id, count(*) from main_marts.dim_condition"
        " group by 1 order by 1",
    ) == [("adrd", 10), ("oncology_nsclc", 11)]
    assert _rows(
        fixture_project_root,
        "select count(*), count(distinct condition_key), count(distinct indication_profile_id)"
        " from main_marts.dim_condition",
    ) == [(21, 21, 2)]

    # Step 2's key change, asserted where it bites: the bridge and the dimension
    # must hash the same two inputs. Measured counterfactual -- re-hashing the
    # bridge's sponsor_key from sponsor_normalized alone, as it was before this
    # task, leaves 7 of its 7 distinct keys with no dim_sponsor row to join, so
    # this leg is red under exactly the regression A19 names.
    assert _rows(
        fixture_project_root,
        "select count(*) from (select distinct sponsor_key from main_marts.bridge_trial_sponsor) b"
        " left join main_marts.dim_sponsor d using (sponsor_key) where d.sponsor_key is null",
    ) == [(0,)]

    # Step 4: a global dim_date means no profile column on the built table and a
    # calendar spine with one row per day and no gap -- which is what a
    # per-profile dim_date would break, independently of today's date.
    assert _rows(
        fixture_project_root,
        "select count(*) from information_schema.columns where table_schema = 'main_marts'"
        " and table_name = 'dim_date' and column_name = 'indication_profile_id'",
    ) == [(0,)]
    assert _rows(
        fixture_project_root,
        "select count(*) = count(distinct date_day),"
        " max(date_day) - min(date_day) + 1 = count(*) from main_marts.dim_date",
    ) == [(True, True)]

    # The fixture-overlap premise of the value-scoping bullet in the docstring
    # above, not a claim about Step 4. Task 12's asymmetric membership moves this
    # number, and when it
    # does the non-discrimination argument in the docstring lapses with it --
    # that is the point of asserting it.
    assert _rows(
        fixture_project_root,
        "select count(*) from main_marts.dim_trial d1"
        " inner join main_marts.dim_trial d2 using (nct_id)"
        " where d1.indication_profile_id = 'adrd'"
        " and d2.indication_profile_id = 'oncology_nsclc'",
    ) == [(10,)]


# ``(rows, joined, differs_when_scoped, joined_pooled, differs_when_pooled)`` for
# one shared dimension -- the same five fields the pinned tuples below are read
# against. `scoped` re-derives the dimension's own statistic from the model it
# reads, grouped by (profile, entity); `pooled` derives the same statistic with the
# profile taken out of the group by, i.e. what a dimension that counted globally
# and then copied the number into each of its rows would emit. Comparing a
# dimension against `scoped` can only say it is self-consistent; comparing it
# against `pooled` is what can say the profile in its group by is load-bearing.
# `joined_pooled` is what keeps that second comparison honest: a pooled
# counterfactual that matched no rows would report "0 differ" for free.
_SCOPING_QUERIES = {
    "dim_sponsor": (
        """
        with scoped as (
            select s.indication_profile_id, s.sponsor_normalized,
                   count(distinct s.nct_id) as n
            from main_staging.stg_trial_sponsors s
            inner join main_intermediate.int_current_trial_status r
                on s.ingestion_run_id = r.ingestion_run_id
                and s.indication_profile_id = r.indication_profile_id
                and s.nct_id = r.nct_id
            where s.sponsor_normalized is not null
            group by 1, 2
        ),
        pooled as (
            select s.sponsor_normalized, count(distinct s.nct_id) as n
            from main_staging.stg_trial_sponsors s
            inner join main_intermediate.int_current_trial_status r
                on s.ingestion_run_id = r.ingestion_run_id
                and s.indication_profile_id = r.indication_profile_id
                and s.nct_id = r.nct_id
            where s.sponsor_normalized is not null
            group by 1
        )
        select
            (select count(*) from main_marts.dim_sponsor),
            (select count(*) from main_marts.dim_sponsor p
                inner join scoped g on p.indication_profile_id = g.indication_profile_id
                and p.sponsor_normalized = g.sponsor_normalized),
            (select count(*) from main_marts.dim_sponsor p
                inner join scoped g on p.indication_profile_id = g.indication_profile_id
                and p.sponsor_normalized = g.sponsor_normalized
                where p.trial_count <> g.n),
            (select count(*) from main_marts.dim_sponsor p
                inner join pooled g using (sponsor_normalized)),
            (select count(*) from main_marts.dim_sponsor p
                inner join pooled g using (sponsor_normalized)
                where p.trial_count <> g.n)
        """
    ),
    "dim_geography": """
        with scoped as (
            select indication_profile_id, state_normalized, count(distinct nct_id) as n
            from main_intermediate.int_geography_normalized
            where state_normalized is not null
            group by 1, 2
        ),
        pooled as (
            select state_normalized, count(distinct nct_id) as n
            from main_intermediate.int_geography_normalized
            where state_normalized is not null
            group by 1
        )
        select
            (select count(*) from main_marts.dim_geography),
            (select count(*) from main_marts.dim_geography p
                inner join scoped g on p.indication_profile_id = g.indication_profile_id
                and p.state_code = g.state_normalized),
            (select count(*) from main_marts.dim_geography p
                inner join scoped g on p.indication_profile_id = g.indication_profile_id
                and p.state_code = g.state_normalized
                where p.trial_count <> g.n),
            (select count(*) from main_marts.dim_geography p
                inner join pooled g on p.state_code = g.state_normalized),
            (select count(*) from main_marts.dim_geography p
                inner join pooled g on p.state_code = g.state_normalized
                where p.trial_count <> g.n)
    """,
}

# ``(rows, joined, differs_when_scoped, joined_pooled, differs_when_pooled)``,
# measured 2026-09-06 on each warehouse. The two `joined` values being equal to
# `rows` is part of the assertion: a counterfactual that quietly lost rows on one
# side would shrink the comparison instead of failing it.
_SCOPED_MISMATCHES = {"dim_sponsor": [(14, 14, 0, 14, 4)], "dim_geography": [(18, 18, 0, 18, 4)]}
_BASE_SCOPED_MISMATCHES = {
    "dim_sponsor": [(14, 14, 0, 14, 0)],
    "dim_geography": [(18, 18, 0, 18, 0)],
}


def test_shared_dimension_stats_are_scoped_to_their_profile(
    divergent_fixture_root: Path,
    fixture_project_root: Path,
) -> None:
    """Task 11's fifth PREDICTED note, discharged as a measurement.

    Its docstring could only record that the base fixture cannot discriminate a
    per-profile statistic from a global one: both profiles list the same ten
    trials, so counting within a profile and counting globally then copying
    produce the same number for every row. Amendment A15 item 2's warehouse breaks
    the symmetry -- ADRD's second run adds 17 listings NSCLC never had -- and the
    leg that was a prediction is now an assertion, with the base warehouse's
    non-discrimination re-measured in the same test rather than inherited:

      * every dim row equals its own source counted **within its profile**
        (14/14 and 18/18 rows, 0 differing) on both warehouses, which is what
        makes the pooled counterfactual below a faithful reading of how these
        dimensions are built;
      * on the **pooled** counterfactual, 0 of 14 and 0 of 18 rows differ on the
        base fixture (the non-discrimination, measured) and 4 of 14 and 4 of 18
        differ on the divergent one. So this is the leg that catches a per-profile
        row set whose statistic was computed across profiles: that defect puts the
        pooled number on eight rows here and this assertion names all eight. What
        it cannot catch is the cruder form -- literally dropping
        `indication_profile_id` from the dim's group by -- because that model never
        reaches a warehouse: measured 2026-09-06, dbt's binder rejects it (its
        surrogate key still names the column). The source-level grain guard covers
        that instead, and does go red on it, naming `dim_geography`.

    The two examples name which rows those are, since the counts alone would not
    survive a fixture edit as an explanation. CA is the interesting one: its
    `trial_count` differs per profile while its `listed_site_count` does not, so
    the two statistics are independently scoped rather than one derived from the
    other.
    """
    for model, sql in _SCOPING_QUERIES.items():
        assert _rows(divergent_fixture_root, sql) == _SCOPED_MISMATCHES[model], model
        assert _rows(fixture_project_root, sql) == _BASE_SCOPED_MISMATCHES[model], model

    assert _rows(
        divergent_fixture_root,
        "select indication_profile_id, trial_count from main_marts.dim_sponsor"
        " where sponsor_normalized = 'neuropharm inc' order by 1",
    ) == [("adrd", 10), ("oncology_nsclc", 3)]
    assert _rows(
        divergent_fixture_root,
        "select state_code, indication_profile_id, trial_count, listed_site_count"
        " from main_marts.dim_geography where state_code in ('CA', 'NC') order by 1, 2",
    ) == [
        ("CA", "adrd", 8, 4),
        ("CA", "oncology_nsclc", 4, 4),
        ("NC", "adrd", 8, 2),
        ("NC", "oncology_nsclc", 2, 2),
    ]


def test_shared_dimension_contracts_state_the_per_profile_grain() -> None:
    """The three shared dims must declare indication_profile_id as a not-null
    VARCHAR in the position it holds in their select list, dim_geography must
    stop claiming state_code is unique, dim_sponsor and its bridge must hash the
    same inputs, and dim_date must stay global.

    Read from the contract source, not ``dbt_target/manifest.json`` like
    test_segment_mart_contracts_declare_the_profile, so it keeps reporting while a
    build is broken: a manifest-backed version of this test errors rather than
    naming the drift, which is the R27 failure mode that hid this module for six
    tasks (the commits bounding that span are named on
    ``SHARED_DIMS_WITH_PROFILE``). It takes no fixture_project_root for the same
    reason. Contract column order is checked because dbt's enforced contract
    compares the model's columns
    positionally, so a declaration that drifts from select order surfaces as a
    build failure several layers away from the line that caused it.

    Every SQL assertion reads the model through _strip_sql_comments. These are
    string searches over source text, so an un-stripped ``group by
    indication_profile_id`` or key argument list could be supplied by a comment
    while the model did something else entirely; a guard a comment can satisfy is
    decoration. Stripping also keeps the dim_date leg honest in the other
    direction -- a comment there explaining that the calendar spine has no
    profile column is documentation, not a widened grain.

    The bridge leg compares the *ordered lists* of hashed inputs rather than the
    argument strings, because ``dim_sponsor.sql`` references the bare column and
    ``bridge_trial_sponsor.sql`` spells its inputs ``s.``-qualified, and
    ``macros/generate_surrogate_key.sql`` concatenates whatever it is given with
    ``'||'`` over ``coalesce(cast(<field> as varchar), '__null__')``. The inputs
    must agree, and so must their order — the macro folds the list in the order
    it is given, so a one-sided reorder produces a different md5 and orphans the
    bridge rows just as a dropped column would. The qualification is each model's
    own business.
    """
    import yaml

    from src.utils.paths import project_root

    marts = project_root() / "dbt_clinical_trials/models/marts"
    doc = yaml.safe_load((marts / "_marts.yml").read_text(encoding="utf-8"))
    # Looked up by name, never iterated: _marts.yml describes 16 models and this
    # guard covers three of them, so a future description-only block anywhere
    # else in the file cannot make this test fail for an unrelated reason.
    blocks = {model["name"]: model for model in doc["models"]}

    for name in SHARED_DIMS_WITH_PROFILE:
        declared = [c["name"] for c in blocks[name]["columns"]]
        columns = {c["name"]: c for c in blocks[name]["columns"]}
        profile = columns["indication_profile_id"]
        assert profile["data_type"] == "VARCHAR", name
        assert profile["tests"] == ["not_null"], name
        key = declared[0]
        assert key.endswith("_key"), name
        assert columns[key]["tests"] == ["not_null", "unique"], name
        # The key hashes entity x profile, so it is the only thing that can
        # enforce the grain now that the entity alone does not.
        assert declared.index("indication_profile_id") == 1, name
        code = _strip_sql_comments((marts / f"{name}.sql").read_text(encoding="utf-8"))
        assert "group by indication_profile_id" in code, name
        key_inputs = _surrogate_key_inputs(code, key)
        assert key_inputs is not None, f"{name}.sql: {key} is not emitted as a surrogate key"
        assert "indication_profile_id" in key_inputs, (
            f"{name}: key must hash the profile it is grained by: {key_inputs}"
        )

    # state_code loses unique (TX exists once per profile) and keeps not_null.
    geo_columns = {c["name"]: c for c in blocks["dim_geography"]["columns"]}
    assert geo_columns["state_code"]["tests"] == ["not_null"]

    # dim_date is a calendar spine: no profile column in its contract, and none
    # in its code, so the grain cannot be widened by accident later.
    date_columns = {c["name"] for c in blocks["dim_date"]["columns"]}
    assert "indication_profile_id" not in date_columns
    date_code = _strip_sql_comments((marts / "dim_date.sql").read_text(encoding="utf-8"))
    assert "indication_profile_id" not in date_code

    # A19's defect, restated statically: this task re-hashed dim_sponsor.sponsor_key
    # to (sponsor_normalized, indication_profile_id) and bridge_trial_sponsor had to
    # follow, or the bridge's rows point at keys no dim_sponsor row has. dbt's own
    # relationships test on exactly that column (_marts.yml:347) catches the orphan
    # whenever a build runs; this guard is the version that still names the drifted
    # inputs when a build does not run, which is why the assertion is on the key's
    # inputs and not on row counts.
    sponsor_code = _strip_sql_comments((marts / "dim_sponsor.sql").read_text(encoding="utf-8"))
    bridge_code = _strip_sql_comments(
        (marts / "bridge_trial_sponsor.sql").read_text(encoding="utf-8")
    )
    dim_inputs = _surrogate_key_inputs(sponsor_code, "sponsor_key")
    bridge_inputs = _surrogate_key_inputs(bridge_code, "sponsor_key")
    assert dim_inputs is not None, "dim_sponsor.sql: sponsor_key is not a surrogate key"
    assert bridge_inputs is not None, "bridge_trial_sponsor.sql: sponsor_key is not a surrogate key"
    assert dim_inputs == bridge_inputs, (
        "dim_sponsor.sql and bridge_trial_sponsor.sql hash different inputs for "
        f"sponsor_key -- dim_sponsor.sql: {dim_inputs}, "
        f"bridge_trial_sponsor.sql: {bridge_inputs}. Re-hashing one side, or "
        "reordering its arguments, orphans every bridge row: the macro "
        "concatenates in list order, so order is part of the key."
    )


def test_window_frames_partition_by_profile() -> None:
    """No frame in models/marts/ may normalize across the whole warehouse.

    Asserted against the model source, not the built tables, for two reasons. It
    takes no fixture_project_root because a source-level guard must not be able
    to turn into an error when the build breaks -- the failure mode that hid this
    whole module through the R27 blackout. And on the base fixture nothing could
    discriminate the two anyway: every segment holds exactly one recruiting
    trial, so a pooled `percent_rank()` and a per-profile one both return 0.0,
    and a global `rank()` over all-tied scores still yields (1, 1). Task 12's
    divergent warehouse does discriminate --
    test_profile_scores_are_bit_identical_built_alone_or_alongside and
    test_priority_rank_spreads_within_a_profile_on_multi_snapshot_history read
    real, unevenly distributed scores -- so the value assertions and this guard
    now cover each other's blind spots: a frame this file never reads is caught
    there, and a partition that is present but pointed at the wrong column is
    caught here.

    Two rules over MARTS_WITH_PROFILE_SCOPED_WINDOWS -- the five segment marts
    Task 10 re-grained, mart_trial_similarity's top-25 rank and fct_trial_site's
    latest-run qualify -- applied by _window_frames to the whole file (frames
    span lines, so a line-by-line scan would find almost nothing and look green):
    an empty or whitespace-only frame is banned outright, and a frame with a
    `partition by` must partition by the profile. A new model carrying a window
    does not get to skip this check by staying unnamed, which is why the list is
    asserted against the scanned file set below. Not caught: a frame with an
    `order by` and no `partition by` at all, which reads as a deliberate global
    and stays a reviewer's job.
    """
    from src.utils.paths import project_root

    marts = project_root() / "dbt_clinical_trials/models/marts"
    windowed = {
        path.stem
        for path in marts.glob("*.sql")
        if _OVER_KEYWORD_RE.search(_strip_sql_comments(path.read_text(encoding="utf-8")))
    }
    assert windowed <= set(MARTS_WITH_PROFILE_SCOPED_WINDOWS), (
        "models/marts/ carries windows this guard never reads: "
        f"{sorted(windowed - set(MARTS_WITH_PROFILE_SCOPED_WINDOWS))} -- name them "
        "in MARTS_WITH_PROFILE_SCOPED_WINDOWS or make the frame global on purpose"
    )
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


def test_reliability_prefers_the_silver_profile_when_the_manifest_disagrees(
    divergent_fixture_root: Path,
) -> None:
    """`coalesce(t.indication_profile_id, r.indication_profile_id)` — the argument
    order is the claim, so it is what gets asserted.

    `mart_data_reliability` is the one mart whose profile column can come from
    either side of the bronze/silver boundary: the manifest's `profile` field for
    a run whose silver does not exist (an ingest-only profile's bronze-only run),
    or silver's stamped profile for every run that made it. Silver is the
    authoritative side, and the fixture run `UNSTAMPED_NSCLC_RUN_ID` is the case
    that separates the two: its manifest was written with `profile` left at the
    `IngestionManifest` default, exactly as a manifest from before profile
    stamping landed would read, while its silver rows carry `oncology_nsclc`.

    Swapping the coalesce's arguments is therefore a countable change here: the
    run would be filed under a `default` profile that is not a profile at all, and
    `test_profile_scores_are_bit_identical_built_alone_or_alongside`'s exclusion of
    this mart would stop being about grain and start being about a wrong column.
    Taking silver unconditionally instead would keep this row right and lose the
    bronze-only run that the fallback exists for, which is why the staging legs
    below pin *both* sides of the disagreement before the mart's answer.

    The run is dated three days *before* NSCLC's stamped run on purpose, so it can
    only ever be that profile's older row: it never becomes the latest successful
    run `run_reliability` prices the queue with, and -- because
    `int_trial_status_history` joins staging trials to snapshots on
    `(ingestion_run_id, indication_profile_id)` -- it never reaches
    `fct_trial_snapshot` at all, which the currency test's three run ids show.
    """
    # The manifest side really does say `default`, straight from staging.
    run = f"where ingestion_run_id = '{UNSTAMPED_NSCLC_RUN_ID}'"
    assert _rows(
        divergent_fixture_root,
        "select indication_profile_id, status, record_count, snapshot_date"
        f" from main_staging.stg_trial_snapshots {run}",
    ) == [(UNSTAMPED_MANIFEST_PROFILE, "success", 10, date(2026, 8, 25))]
    # ...and silver really does say the profile that produced it.
    assert _rows(
        divergent_fixture_root,
        f"select indication_profile_id, count(*) from main_staging.stg_trials {run} group by 1",
    ) == [("oncology_nsclc", 10)]
    # The mart's answer: silver wins, and every other column is the ordinary
    # reconciliation for a complete run.
    assert _rows(
        divergent_fixture_root,
        "select indication_profile_id, snapshot_date, status, manifest_record_count,"
        " trial_row_count, manifest_reconciled_flag, unique_nct_flag"
        f" from main_marts.mart_data_reliability {run}",
    ) == [("oncology_nsclc", date(2026, 8, 25), "success", 10, 10, True, True)]
    # No row of the built mart is filed under the placeholder, and the four runs
    # are still two per profile -- the fallback did not delete the stamped rows.
    assert _rows(
        divergent_fixture_root,
        "select count(*) from main_marts.mart_data_reliability"
        f" where indication_profile_id = '{UNSTAMPED_MANIFEST_PROFILE}'",
    ) == [(0,)]
    assert _rows(
        divergent_fixture_root,
        "select indication_profile_id, count(*) from main_marts.mart_data_reliability"
        " group by 1 order by 1",
    ) == [("adrd", 2), ("oncology_nsclc", 2)]


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


def test_every_trial_grain_mart_contracts_the_profile() -> None:
    """Composite grain is only real if the contract says so: an undeclared column
    fails the build, but a *declared nullable* column would let a future model
    drop the profile and still compile.

    ``test_marts_contracts_enforced`` proves every mart has an enforced contract
    and never proves *which* columns are in it, so it stays green whether or not
    ``indication_profile_id`` is declared. This closes that gap over the 15
    trial-grain marts; ``dim_date`` is excluded because the calendar spine is
    global on purpose (Task 11). Read from ``_marts.yml`` rather than the built
    manifest so the not_null test is checked where it is authored.

    Takes no fixture on purpose, like the other source-level guards in this file
    (``test_shared_dimension_contracts_state_the_per_profile_grain``,
    ``test_window_frames_partition_by_profile``): it reads ``_marts.yml``, so a
    broken build must not be able to turn this check into an error. With
    ``fixture_project_root`` in the signature it requested the session dbt build
    it never used, and every fact this test asserts was already knowable without
    it -- so the ``result.returncode == 0`` assert at the end of
    ``tests/conftest.py``'s ``_build_fixture_root`` would report ERROR instead of
    running a guard, exactly the blackout failure mode that hid this module for
    six tasks.
    """
    import yaml

    from src.utils.paths import project_root

    doc = yaml.safe_load(
        (project_root() / "dbt_clinical_trials/models/marts/_marts.yml").read_text(encoding="utf-8")
    )
    trial_grain = {name for name in (m["name"] for m in doc["models"]) if name != "dim_date"}
    # Membership, not just cardinality: a pin on len() alone stays green if a
    # mart is renamed or dim_date's exclusion clause goes stale, because the set
    # keeps its size while iterating something else. Names taken from
    # _marts.yml's 16 model blocks minus dim_date, 2026-09-06.
    assert trial_grain == {
        "bridge_trial_condition",
        "bridge_trial_sponsor",
        "dim_condition",
        "dim_geography",
        "dim_sponsor",
        "dim_trial",
        "fct_trial_site",
        "fct_trial_snapshot",
        "mart_condition_geography_trends",
        "mart_data_reliability",
        "mart_feasibility_priority_queue",
        "mart_recruiting_competition",
        "mart_site_overlap",
        "mart_trial_activity",
        "mart_trial_similarity",
    }
    for model in doc["models"]:
        if model["name"] not in trial_grain:
            continue
        column = next(
            (c for c in model.get("columns", []) if c["name"] == "indication_profile_id"),
            None,
        )
        assert column is not None, f"{model['name']} does not contract the profile"
        assert "not_null" in (column.get("tests") or []), model["name"]


def test_similarity_top_25_cap_trims_a_busy_profile(divergent_fixture_root: Path) -> None:
    """`qualify similarity_rank <= 25` in mart_trial_similarity.sql, made countable.

    The cap is invisible on a small profile: with 10 or 12 trials each, every
    trial has 9 or 11 candidates and all of them fit, so deleting the `qualify`
    line changes no row in the warehouse and no test in the repository could
    notice (measured 2026-09-06 on the pre-growth divergent fixture: adrd's
    maximum similarity_rank was 11 over 132 rows). A15 item 2's warehouse grows
    ADRD to 27 listings, which puts every one of its trials over the line -- 26
    candidates against a cap of 25 -- so the cap now trims exactly one row per
    trial and its removal is a countable 702 vs 675.

    A grown fixture rather than a second static guard, deliberately. The static
    guard this module already has (`test_window_frames_partition_by_profile`)
    checks the *partition* of the frame that produces the rank, which is a
    property of the source text; whether the cap *binds* is a property of the
    data, and no amount of reading `mart_trial_similarity.sql` can tell a clause
    that removes 27 rows from one that removes none. A text search for
    `qualify similarity_rank <= 25` would also be satisfied by a cap at any other
    number, by a commented-out one, and by one whose rank column is all nulls.
    Both halves of the claim matter and they are checked in different places: the
    frame's partition statically, the clause's effect here.

    NSCLC is the other half of the assertion and it is not a throwaway: 10 trials
    give each one 9 candidates, so its 90 rows are exactly 10 x 9 and its maximum
    rank is 9. A cap that fired there -- a `<= 25` that became `<= 5`, or a rank
    computed over the whole warehouse instead of per profile -- would delete real
    neighbours from the thin profile, and the 90 would drop.
    """
    assert _rows(
        divergent_fixture_root,
        "select indication_profile_id, count(*), count(distinct nct_id_a),"
        " max(similarity_rank) from main_marts.mart_trial_similarity group by 1 order by 1",
    ) == [("adrd", 675, 27, 25), ("oncology_nsclc", 90, 10, 9)]
    # Candidates, from the model's own input: 27 ADRD trials join to 26 partners
    # each = 702 scored pairs, against the 675 rows the cap lets through.
    assert _rows(
        divergent_fixture_root,
        "select indication_profile_id, count(*), count(distinct nct_id)"
        " from main_intermediate.int_trial_comparability_features group by 1 order by 1",
    ) == [("adrd", 27, 27), ("oncology_nsclc", 10, 10)]
    # Every busy-profile trial sits exactly on the cap, so the trim is uniform
    # rather than one trial's rank column running away: an ADRD trial with any
    # other row count means the cap stopped doing its job.
    assert _rows(
        divergent_fixture_root,
        "select count(*) from (select nct_id_a, count(*) c"
        " from main_marts.mart_trial_similarity where indication_profile_id = 'adrd'"
        " group by 1 having c <> 25)",
    ) == [(0,)]
    assert _rows(
        divergent_fixture_root,
        "select count(*) from main_marts.mart_trial_similarity where similarity_rank > 25",
    ) == [(0,)]
