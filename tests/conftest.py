import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fixture_warehouse import (
    AddedStudy,
    FixtureRun,
    build_fixture_warehouse,
    variant_page,
)

CONFIG_YAML = """
api:
  base_url: "https://clinicaltrials.gov/api/v2"
paths:
  bronze_api_responses: "data/bronze/adrd/api_responses"
  bronze_manifests: "data/bronze/adrd/manifests"
  silver: "data/silver"
  gold: "data/gold"
  duckdb: "data/warehouse/clinical_trials.duckdb"
  quarantine: "data/quarantine"
ingestion:
  mode_default: "incremental"
  reuse_window_hours: 24
scope:
  refresh_cadence: "weekly"
"""

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_RUN_ID = "20260901T120000Z_fixture01"
NSCLC_FIXTURE_RUN_ID = "20260901T120001Z_fixture02"
# Pinned, not utc_now(): both runs must land on ONE snapshot_date for the
# grain collapse to reproduce, and utc_now() crosses midnight. nsclc is one
# hour later so the pre-migration `order by snapshot_timestamp_utc desc`
# deterministically drops ADRD — silent loss, not a coin flip.
#
# This sameness is the property Amendment A15 item 2 says must be broken
# *somewhere*, because while both profiles share one date nothing in the suite
# can tell a per-profile max(snapshot_date) from a global one. It is not broken
# here: at least ten assertions in tests/test_dbt_fixture_build.py pin this
# warehouse's cardinalities, so re-dating it would look like A8-violating
# loosening even if it were not. `divergent_fixture_root` below is a second,
# separate warehouse that carries the divergent dates instead.
FIXTURE_SNAPSHOT_DAY = datetime(2026, 9, 1, tzinfo=UTC)
ADRD_STARTED_AT = FIXTURE_SNAPSHOT_DAY + timedelta(hours=10)
NSCLC_STARTED_AT = FIXTURE_SNAPSHOT_DAY + timedelta(hours=11)

# --- divergent-date fixture (Amendment A15 item 2) ---------------------------
# ADRD's second run is a week after its first, and NSCLC's only complete run is
# the *earlier* date. So ADRD's per-profile latest (2026-09-08) is strictly
# greater than NSCLC's (2026-09-01) and therefore than the global max only for
# ADRD: a global `max(snapshot_date)` marks NSCLC's ten rows stale and leaves
# that profile with zero current records, which is the collapse
# assert_one_current_record_per_trial.sql was rewritten to catch and could not
# catch on the single-date warehouse.
DIVERGENT_ADRD_RUN2_ID = "20260908T100000Z_fixture03"
DIVERGENT_ADRD_RUN2_AT = datetime(2026, 9, 8, 10, tzinfo=UTC)
# The same second ADRD run, re-dated a full month later (see
# `two_month_fixture_root` below). Only the started_at moves: the run id and
# every bronze page stay the divergent seed's, so the *content* of ADRD's second
# snapshot is unchanged -- what changes is which `activity_month` it lands in.
# Two distinct months is the one thing no other committed warehouse has: on
# `divergent_fixture_root` ADRD's two runs (2026-09-01, 2026-09-08) both truncate
# to 2026-09, so `months_in_window` is 1 on every row there and the mart's
# `when months_in_window >= 2` positive arm is never taken -- the residual the
# positive-arm assertion in tests/test_dbt_fixture_build.py closes.
TWO_MONTH_ADRD_RUN2_AT = datetime(2026, 10, 6, 10, tzinfo=UTC)
# A run whose manifest predates profile stamping: `profile` stays at the
# IngestionManifest default "default" while its silver rows carry
# oncology_nsclc. Dated *before* NSCLC's stamped run so it can only ever be the
# older row of that profile, never the one `run_reliability` selects.
UNSTAMPED_NSCLC_RUN_ID = "20260825T090000Z_fixture04"
UNSTAMPED_NSCLC_AT = datetime(2026, 8, 25, 9, tzinfo=UTC)
UNSTAMPED_MANIFEST_PROFILE = "default"

ADRD_CONDITION = "Alzheimer Disease"
NSCLC_CONDITION = "Non-Small Cell Lung Cancer"


_variant_page = variant_page


@pytest.fixture
def project_root_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temp project root carrying the real config/ tree.

    ProfileRegistry resolves config/profiles/*.yml and config/shared_paths.yml
    against the project root, so a temp root without them cannot build a
    profile at all. Every path in those files is relative, which re-scopes
    bronze/silver/warehouse inside tmp_path. project_config.yml is then
    replaced by CONFIG_YAML so the single-config callers keep their fixture.

    Do not assert `cfg.paths.X.is_relative_to(tmp_path)`: tmp_path and the
    resolved root differ by macOS's /var -> /private/var symlink. Assert that
    files exist where the config says they should.
    """
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    (tmp_path / "config" / "project_config.yml").write_text(CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Clear every cached singleton that resolved a path at first call.

    All four are ``lru_cache``d singletons resolved against
    ``CTI_PROJECT_ROOT``, so one left warm by an earlier test module hands a
    later one profiles — or a taxonomy, or a geography rule set — pointing at
    the repo's real ``data/`` tree, fatal for any test that runs a real
    (non-dry) prune. Repo-wide rather than per-module so no future prune test
    has to remember it. Called unconditionally, not via ``getattr``, and the
    same holds for the tuple-and-loop form below: iterating four known
    accessors without probing them means an accessor that stops being a cached
    singleton fails loudly on every test instead of quietly dropping out of the
    clearing.
    """
    from src.config import get_config
    from src.profiles import get_registry
    from src.transform.normalize_conditions import get_taxonomy
    from src.transform.normalize_locations import get_geography_rules

    singletons = (get_config, get_registry, get_taxonomy, get_geography_rules)
    for cached in singletons:
        cached.cache_clear()
    yield
    for cached in singletons:
        cached.cache_clear()


def materialize_with_checks(*, assets, asset_checks, run_config=None, raise_on_error=False):
    from dagster import Definitions

    defs = Definitions(assets=list(assets), asset_checks=list(asset_checks))
    job = defs.resolve_implicit_global_asset_job_def()
    return job.execute_in_process(run_config=run_config or {}, raise_on_error=raise_on_error)


def check_evaluation(result, check_name):
    for evaluation in result.get_asset_check_evaluations():
        if evaluation.check_name == check_name:
            return evaluation
    return None


@pytest.fixture(scope="session")
def dbt_manifest(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = Path(__file__).resolve().parents[1]
    dbt_dir = root / "dbt_clinical_trials"
    if not (dbt_dir / "profiles.yml").exists():
        subprocess.run(
            ["cp", str(dbt_dir / "profiles.yml.example"), str(dbt_dir / "profiles.yml")],
            check=True,
        )
    if (dbt_dir / "packages.yml").exists():
        subprocess.run(
            [
                "uv",
                "run",
                "dbt",
                "deps",
                "--project-dir",
                str(dbt_dir),
                "--profiles-dir",
                str(dbt_dir),
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )
    subprocess.run(
        [
            "uv",
            "run",
            "dbt",
            "parse",
            "--project-dir",
            str(dbt_dir),
            "--profiles-dir",
            str(dbt_dir),
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    manifest = dbt_dir / "target" / "manifest.json"
    assert manifest.exists(), "dbt parse did not produce target/manifest.json"
    return manifest


def _build_fixture_root(
    tmp_path_factory: pytest.TempPathFactory,
    name: str,
    runs: list[FixtureRun],
    *,
    session_timezone: str | None = None,
    dbt_vars: dict[str, object] | None = None,
) -> Path:
    """Bronze->silver->gold for `runs`, then one `dbt build`; returns the scratch root."""
    root = tmp_path_factory.mktemp(name)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CTI_PROJECT_ROOT", str(root))
    try:
        build_fixture_warehouse(
            root,
            runs=runs,
            session_timezone=session_timezone,
            dbt_vars=dbt_vars,
        )
        return root
    finally:
        monkeypatch.undo()


def _base_adrd_run() -> FixtureRun:
    return FixtureRun(
        profile="adrd",
        run_id=FIXTURE_RUN_ID,
        started_at=ADRD_STARTED_AT,
        condition=ADRD_CONDITION,
        fixture_dir="bronze_snapshot",
    )


def _base_nsclc_run() -> FixtureRun:
    return FixtureRun(
        profile="oncology_nsclc",
        run_id=NSCLC_FIXTURE_RUN_ID,
        started_at=NSCLC_STARTED_AT,
        condition=NSCLC_CONDITION,
        fixture_dir="bronze_snapshot_nsclc",
    )


# Filler listings so the similarity mart's top-25 cap can actually bind.
# `qualify similarity_rank <= 25` is invisible on a 10- or 12-trial profile: each
# trial then has 9 or 11 candidates, every one of them inside the cap, so
# deleting the qualify changes no row in the warehouse and no test notices
# (measured 2026-09-06: adrd max(similarity_rank) = 11 on the 12-trial page).
# 27 ADRD listings give each trial 26 candidates, so the cap trims exactly one
# row per trial and its removal is a countable difference. On real data the cap is
# what bounds the mart's size: main's warehouse holds 65 450 similarity rows over
# 2 618 dim_trial rows -- measured 2026-09-06, read-only -- and 65 450 is exactly
# 25 x 2 618, so the cap is trimming every single trial there. A fixture the cap
# cannot bind on therefore cannot see the clause at all.
# Statuses, phases, sponsors and states cycle so the fillers are distinct
# entities in the dimensions rather than 17 copies of one row.
FILLER_STATUSES = ("COMPLETED", "ACTIVE_NOT_RECRUITING")
FILLER_PHASES = ("PHASE2", "PHASE3")
FILLER_SPONSORS = ("NeuroPharm Inc", "MetaDiab Labs", "Cedar Cognitive Institute")
FILLER_LOCATIONS = (
    ("Duke Memory Center", "Durham", "North Carolina"),
    ("UCSF Memory Clinic", "San Francisco", "CA"),
    ("Mass General Cognitive Unit", "Boston", "Massachusetts"),
    ("Lone Star Neurology Trial Site", "Austin", "Texas"),
)


def _filler_studies(first_index: int, count: int) -> tuple[AddedStudy, ...]:
    return tuple(
        AddedStudy(
            nct_id=f"NCT{first_index + i:08d}",
            clone_of="NCT00000001",
            overall_status=FILLER_STATUSES[i % len(FILLER_STATUSES)],
            phase=FILLER_PHASES[i % len(FILLER_PHASES)],
            condition="Alzheimer Disease",
            sponsor=FILLER_SPONSORS[i % len(FILLER_SPONSORS)],
            location=FILLER_LOCATIONS[i % len(FILLER_LOCATIONS)],
        )
        for i in range(count)
    )


# The second ADRD snapshot, a week later: two trials change status and two new
# recruiting listings appear, one of which lands in the same (condition group,
# state, phase) segment as NCT00000001 so that segment holds two recruiting
# trials while its profile's others hold one. Uneven segment sizes are what make
# the queue's min-max normalization non-degenerate, and therefore what makes a
# per-profile partition observable at all (a single-recruiting-trial fixture ties
# every score at 0.0 and every rank at 1, which is why Task 10 could only
# *predict* a rank spread). Both recruiting additions keep a sponsor the NSCLC
# page also lists, so the shared dimensions' per-profile trial_count differs from
# the pooled one -- the value scoping Task 11 could not assert.
DIVERGENT_ADRD_RUN2 = FixtureRun(
    profile="adrd",
    run_id=DIVERGENT_ADRD_RUN2_ID,
    started_at=DIVERGENT_ADRD_RUN2_AT,
    condition=ADRD_CONDITION,
    fixture_dir="bronze_snapshot",
    status_overrides={"NCT00000002": "RECRUITING", "NCT00000003": "COMPLETED"},
    added=(
        AddedStudy(
            nct_id="NCT00000011",
            clone_of="NCT00000001",
            overall_status="RECRUITING",
            phase="PHASE3",
            condition="Alzheimer Disease",
            sponsor="NeuroPharm Inc",
            location=("Duke Memory Center", "Durham", "North Carolina"),
        ),
        AddedStudy(
            nct_id="NCT00000012",
            clone_of="NCT00000001",
            overall_status="RECRUITING",
            phase="PHASE2",
            condition="Alzheimer Disease",
            sponsor="NeuroPharm Inc",
            location=("Duke Memory Center", "Durham", "North Carolina"),
        ),
        *_filler_studies(13, 15),
    ),
)

# The divergent second ADRD run, re-dated into October by moving *only* its
# started_at. `dataclasses.replace` rather than a re-literalised FixtureRun so a
# future edit to the divergent seed's status overrides or added studies flows
# through here automatically: this root's job is to give that same content a
# second calendar month, not to fork a second copy of it that can drift.
TWO_MONTH_ADRD_RUN2 = replace(DIVERGENT_ADRD_RUN2, started_at=TWO_MONTH_ADRD_RUN2_AT)

# Same bronze, manifest left at the pre-stamping default. Silver still says
# oncology_nsclc, because src/transform stamps from the resolved profile.
UNSTAMPED_NSCLC_RUN = FixtureRun(
    profile="oncology_nsclc",
    run_id=UNSTAMPED_NSCLC_RUN_ID,
    started_at=UNSTAMPED_NSCLC_AT,
    condition=NSCLC_CONDITION,
    fixture_dir="bronze_snapshot_nsclc",
    manifest_profile=UNSTAMPED_MANIFEST_PROFILE,
)


# The similarity-mart fixtures (tests/test_similarity_candidates.py). The ADRD
# run is the dense one: 52 cloned listings sharing "Alzheimer Disease",
# alternating PHASE2/PHASE3, put 26 trials in the (alzheimers_disease, PHASE2)
# token and 28 in (alzheimers_disease, PHASE3), so a trial's candidate set
# outnumbers the top-25 cap and the qualify actually binds inside the
# candidates -- the same
# reason _filler_studies exists for the divergent fixture. Two Lewy Body
# Dementia clones give a second, small token group, which is what the
# similarity_max_token_df override drops the dominant token against: at df 4
# both alzheimers tokens leave discovery while the lewy token (3) survives.
# Both roots enable mart_trial_similarity_reference (the unbounded pre-2026-10
# self-join, var-gated because it is the 28 GB run at registry scale) as the
# equivalence oracle.
SIMILARITY_RUN_ID = "20261012T090000Z_fixture06"
SIMILARITY_STARTED_AT = datetime(2026, 10, 12, 9, tzinfo=UTC)


def _similarity_adrd_run() -> FixtureRun:
    return FixtureRun(
        profile="adrd",
        run_id=SIMILARITY_RUN_ID,
        started_at=SIMILARITY_STARTED_AT,
        condition=ADRD_CONDITION,
        fixture_dir="bronze_snapshot",
        added=(
            *_filler_studies(20, 24),  # NCT00000020..NCT00000043, Alzheimer Disease
            *_filler_studies(46, 28),  # NCT00000046..NCT00000073, past the Lewy clones
            AddedStudy(
                nct_id="NCT00000044",
                clone_of="NCT00000006",
                overall_status="RECRUITING",
                condition="Lewy Body Dementia",
            ),
            AddedStudy(
                nct_id="NCT00000045",
                clone_of="NCT00000006",
                overall_status="COMPLETED",
                condition="Lewy Body Dementia",
            ),
        ),
    )


def _similarity_nsclc_run() -> FixtureRun:
    # The base page's ten studies share no (condition group, phase) token, so
    # NSCLC has no similarity rows without these two PHASE3 clones of NCT00000001.
    return replace(
        _base_nsclc_run(),
        added=tuple(
            AddedStudy(nct_id=nct_id, clone_of="NCT00000001", overall_status="COMPLETED")
            for nct_id in ("NCT00000090", "NCT00000091")
        ),
    )


@pytest.fixture(scope="session")
def similarity_equivalence_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Default candidate cap; the reference self-join built alongside."""
    return _build_fixture_root(
        tmp_path_factory,
        "similarity_equivalence",
        [_similarity_adrd_run(), _similarity_nsclc_run()],
        dbt_vars={"build_similarity_reference": True, "similarity_reference_top_n": 1000},
    )


@pytest.fixture(scope="session")
def similarity_cap_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """similarity_max_token_df: 4 drops the dominant alzheimers token."""
    return _build_fixture_root(
        tmp_path_factory,
        "similarity_cap",
        [_similarity_adrd_run(), _base_nsclc_run()],
        dbt_vars={"build_similarity_reference": True, "similarity_max_token_df": 4},
    )


@pytest.fixture(scope="session")
def similarity_phase_split_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """similarity_max_token_df: 27 sits between the alzheimers PHASE2 token
    (df 26) and the PHASE3 token (df 28)."""
    return _build_fixture_root(
        tmp_path_factory,
        "similarity_phase_split",
        [_similarity_adrd_run(), _base_nsclc_run()],
        dbt_vars={"similarity_max_token_df": 27},
    )


@pytest.fixture(scope="session")
def fixture_project_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The original two-profile, one-snapshot-date warehouse. See its header note."""
    return _build_fixture_root(
        tmp_path_factory,
        "fixture_project",
        [_base_adrd_run(), _base_nsclc_run()],
    )


@pytest.fixture(scope="session")
def divergent_fixture_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Two ADRD runs a week apart, NSCLC a week behind, plus an un-stamped run.

    Amendment A15 item 2's fixture, built lazily: session-scoped like
    `fixture_project_root`, but requested only by the tests that need
    multi-snapshot or asymmetric data, so a run of the module that asks for
    neither pays no second `dbt build`.
    """
    return _build_fixture_root(
        tmp_path_factory,
        "fixture_divergent",
        [_base_adrd_run(), DIVERGENT_ADRD_RUN2, _base_nsclc_run(), UNSTAMPED_NSCLC_RUN],
    )


@pytest.fixture(scope="session")
def two_month_fixture_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The divergent seed set with ADRD's second run moved into October, so ADRD
    spans two `activity_month`s (2026-09, 2026-10) and NSCLC still one.

    Built lazily and session-scoped like the other roots: only the positive-arm
    assertion in tests/test_dbt_fixture_build.py requests it, so the rest of the
    suite does not pay the extra `dbt build`. Nothing here changes an existing
    seeded date -- it is a fourth, separate warehouse derived from
    `DIVERGENT_ADRD_RUN2` by moving one run's `started_at` forward a month. That
    single month of separation is the whole point: `assert_trends_growth_needs_two_months.sql`
    proves the negative arm (no non-null growth on a one-month window) but no
    other committed fixture ever reaches `months_in_window >= 2`, so nulling
    `recruiting_growth_3m` on *every* row would pass CI forever. This root is the
    first data the guard's positive arm can be checked against.
    """
    return _build_fixture_root(
        tmp_path_factory,
        "fixture_two_month",
        [_base_adrd_run(), TWO_MONTH_ADRD_RUN2, _base_nsclc_run(), UNSTAMPED_NSCLC_RUN],
    )


@pytest.fixture(scope="session")
def solo_fixture_roots(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One warehouse per profile, each holding that profile's runs and nothing else.

    The control half of score invariance: a profile's numbers can only be shown
    unaffected by a second profile by building it both ways. Two more `dbt
    build`s, so it is a fixture the invariance test asks for and nothing else
    does.

    What each root holds is not symmetric, and the asymmetry is worth knowing
    before reading the comparison. ADRD's solo build carries exactly the two ADRD
    runs `divergent_fixture_root` carries. NSCLC's carries only its base run --
    `UNSTAMPED_NSCLC_RUN` is absent, because that run exists to make a manifest
    disagree with silver about which profile owns it, a concern of the divergent
    build alone. So NSCLC's side moves two variables at once (a second profile
    loaded, and one more same-profile run) and its assertion is the stronger of the
    two: a profile's numbers must survive a run *and* a neighbour arriving. That
    one passes, which is the measured fact that the extra run contributes nothing to
    the invaried models. The narrow single-variable claim -- NSCLC unchanged by
    another profile alone, same runs -- would need a third root built from the base
    page only; it is not asserted here.
    """
    return {
        "adrd": _build_fixture_root(
            tmp_path_factory, "fixture_solo_adrd", [_base_adrd_run(), DIVERGENT_ADRD_RUN2]
        ),
        "oncology_nsclc": _build_fixture_root(
            tmp_path_factory, "fixture_solo_nsclc", [_base_nsclc_run()]
        ),
    }


AUDIT_RUN2_ID = "20260901T130000Z_audit02"
AUDIT_FAILED_RUN_ID = "20260902T100000Z_auditfailed"


def _audit_run() -> FixtureRun:
    """Keep legacy base fixtures unchanged; adversarial audit data is isolated."""
    locations = [
        {
            "facility": "Repeated site",
            "city": "Durham",
            "state": "NC",
            "country": "United States",
            "status": "COMPLETED",
        },
        {
            "facility": "Repeated site",
            "city": "Durham",
            "state": "NC",
            "country": "United States",
            "status": "RECRUITING",
        },
        {"city": "Boston", "state": "MA", "country": "United States"},
    ]
    return replace(
        _base_adrd_run(),
        retrieved_at="2026-09-01T10:01:00+00:00",
        study_overrides={
            "NCT00000001": {
                "statusModule": {
                    "statusVerifiedDate": "2026-03",
                    "lastUpdatePostDateStruct": {"date": "2026-03-05"},
                },
                "contactsLocationsModule": {"locations": locations},
                "designModule": {"enrollmentInfo": {"count": 101, "type": "ESTIMATED"}},
            },
            "NCT00000002": {
                "statusModule": {
                    "overallStatus": "UNKNOWN",
                    "statusVerifiedDate": "bad",
                    "lastUpdatePostDateStruct": {"date": "2026-03-04"},
                },
                "contactsLocationsModule": {"locations": []},
                "designModule": {"enrollmentInfo": {"count": 202, "type": "ACTUAL"}},
            },
            "NCT00000003": {
                "statusModule": {"lastUpdatePostDateStruct": {}, "statusVerifiedDate": None},
                "contactsLocationsModule": {"locations": [{"country": "United States"}]},
                "designModule": {"enrollmentInfo": {}},
            },
            "NCT00000004": {
                "contactsLocationsModule": {
                    "locations": [{"state": "Ontario", "country": "Canada"}]
                },
                "designModule": {"enrollmentInfo": {"count": 303}},
            },
            "NCT00000005": {
                "statusModule": {
                    "statusVerifiedDate": "2026",
                    "lastUpdatePostDateStruct": {"date": "2026-03"},
                },
                "designModule": {"studyType": None},
            },
            "NCT00000006": {
                "statusModule": {
                    "statusVerifiedDate": "2026-02-30",
                    "lastUpdatePostDateStruct": {"date": "invalid"},
                },
            },
            "NCT00000007": {
                "designModule": {"enrollmentInfo": {"count": 404, "type": "UNRECOGNIZED"}},
            },
            "NCT00000009": {
                "contactsLocationsModule": {"locations": []},
            },
            "NCT00000010": {
                "contactsLocationsModule": {
                    "locations": [{"state": "Atlantis", "country": "United States"}]
                },
            },
        },
    )


@pytest.fixture(scope="session")
def audit_fixture_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    run = _audit_run()
    return _build_fixture_root(
        tmp_path_factory,
        "fixture_audit",
        [
            run,
            replace(
                run,
                run_id=AUDIT_RUN2_ID,
                started_at=datetime(2026, 9, 1, 0, 15, tzinfo=UTC),
                retrieved_at=None,
            ),
            _base_nsclc_run(),
            replace(
                run,
                run_id=AUDIT_FAILED_RUN_ID,
                started_at=run.started_at + timedelta(days=1),
                final_status="failed",
            ),
        ],
        session_timezone="America/Los_Angeles",
    )


@pytest.fixture(scope="session")
def audit_event_fixture_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = _base_adrd_run()
    return _build_fixture_root(
        tmp_path_factory,
        "fixture_audit_events",
        [
            replace(
                base,
                run_id="20260101T001500Z_event_before",
                started_at=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
            ),
            replace(
                base,
                run_id="20260901T001500Z_event_current",
                started_at=datetime(2026, 9, 1, 0, 15, tzinfo=UTC),
                status_overrides={"NCT00000002": "RECRUITING"},
                study_overrides={
                    "NCT00000001": {
                        "statusModule": {"studyFirstPostDateStruct": {"date": "2026-06-03"}}
                    },
                    "NCT00000002": {
                        "statusModule": {"studyFirstPostDateStruct": {"date": "2026-06-02"}}
                    },
                },
            ),
        ],
        session_timezone="America/Los_Angeles",
    )
