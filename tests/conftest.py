import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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
# A run whose manifest predates profile stamping: `profile` stays at the
# IngestionManifest default "default" while its silver rows carry
# oncology_nsclc. Dated *before* NSCLC's stamped run so it can only ever be the
# older row of that profile, never the one `run_reliability` selects.
UNSTAMPED_NSCLC_RUN_ID = "20260825T090000Z_fixture04"
UNSTAMPED_NSCLC_AT = datetime(2026, 8, 25, 9, tzinfo=UTC)
UNSTAMPED_MANIFEST_PROFILE = "default"

ADRD_CONDITION = "Alzheimer Disease"
NSCLC_CONDITION = "Non-Small Cell Lung Cancer"


@dataclass(frozen=True)
class AddedStudy:
    """One extra study on a *variant* bronze page, cloned from a study that
    already exists on the base page.

    The divergent fixture needs its second ADRD run to differ from its first in
    something the models can see: an identical page two weeks later only proves
    that `lag()` returns null again. Cloning keeps every field the transform
    reads (dates, eligibility, interventions) valid without hand-writing a
    second 10-study JSON fixture.
    """

    nct_id: str
    clone_of: str
    overall_status: str
    phase: str | None = None
    condition: str | None = None
    sponsor: str | None = None
    location: tuple[str, str, str] | None = None  # facility, city, state


@dataclass(frozen=True)
class FixtureRun:
    """One bronze→silver→manifest cycle inside a fixture warehouse."""

    profile: str  # registry profile_id: stamps silver and selects the bronze dir
    run_id: str
    started_at: datetime
    condition: str
    fixture_dir: str
    page: str = "page=00001.json"
    manifest_profile: str | None = None  # None -> same as `profile`
    status_overrides: dict[str, str] = field(default_factory=dict)  # nct_id -> status
    added: tuple[AddedStudy, ...] = ()

    @property
    def effective_manifest_profile(self) -> str:
        return self.manifest_profile or self.profile


def _variant_page(
    src: Path,
    *,
    status_overrides: dict[str, str],
    added: tuple[AddedStudy, ...],
) -> dict:
    """The bronze page at `src` with `status_overrides` applied and `added` cloned in.

    Returns the document; the caller writes it. Nothing here is guessed: an
    unknown nct_id in either collection raises rather than silently producing a
    page that is the same as the base one (which would make the divergent
    fixture divergent in date only, and quietly).
    """
    doc = json.loads(src.read_text(encoding="utf-8"))
    by_id = {s["protocolSection"]["identificationModule"]["nctId"]: s for s in doc["studies"]}
    for nct_id, status in status_overrides.items():
        study = by_id.get(nct_id)
        if study is None:
            raise KeyError(f"{src} has no {nct_id} to re-status")
        study["protocolSection"]["statusModule"]["overallStatus"] = status
        study["protocolSection"]["statusModule"]["lastKnownStatus"] = status
    for spec in added:
        template = by_id.get(spec.clone_of)
        if template is None:
            raise KeyError(f"{src} has no {spec.clone_of} to clone")
        clone = json.loads(json.dumps(template))
        section = clone["protocolSection"]
        ids = section["identificationModule"]
        ids["nctId"] = spec.nct_id
        ids["briefTitle"] = f"{ids.get('briefTitle', 'Fixture study')} [variant {spec.nct_id}]"
        status = section["statusModule"]
        status["overallStatus"] = spec.overall_status
        status["lastKnownStatus"] = spec.overall_status
        if spec.phase is not None:
            section["designModule"]["phases"] = [spec.phase]
        if spec.condition is not None:
            section["conditionsModule"]["conditions"] = [spec.condition]
        if spec.sponsor is not None:
            section["sponsorCollaboratorsModule"]["leadSponsor"]["name"] = spec.sponsor
        if spec.location is not None:
            facility, city, state = spec.location
            section["contactsLocationsModule"]["locations"] = [
                {
                    "facility": facility,
                    "city": city,
                    "state": state,
                    "zip": "00000",
                    "country": "United States",
                    "status": spec.overall_status,
                }
            ]
        doc["studies"].append(clone)
    return doc


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
) -> Path:
    """Bronze→silver→gold for `runs`, then one `dbt build`; returns the scratch root.

    Shared by every warehouse fixture in this module. dbt runs in a subprocess:
    dbt's in-process adapter keeps the DuckDB file open, which would block the
    read-only connections the assertions use. The project dir is the repo's real
    `dbt_clinical_trials/`, so the models under test are the committed ones; only
    the data, the profiles.yml and the target dir are per-root (A22: nothing here
    may write under the repo's own `data/`).
    """
    root = tmp_path_factory.mktemp(name)
    mp = pytest.MonkeyPatch()
    mp.setenv("CTI_PROJECT_ROOT", str(root))
    try:
        (root / "config").mkdir()
        (root / "config" / "project_config.yml").write_text(CONFIG_YAML, encoding="utf-8")
        for cfg_name in (
            "condition_taxonomy.yml",
            "condition_taxonomy_nsclc.yml",
            "geography_rules.yml",
            "score_weights.yml",
            "roi_assumptions.yml",
            "shared_paths.yml",
        ):
            shutil.copy(REPO_ROOT / "config" / cfg_name, root / "config" / cfg_name)
        shutil.copytree(REPO_ROOT / "config" / "profiles", root / "config" / "profiles")

        from src.config import load_config
        from src.ingest.snapshot_manifest import (
            IngestionManifest,
            write_manifest,
            write_summary,
        )
        from src.profiles import load_profile, load_shared_paths
        from src.transform.build_silver_entities import run_transform

        cfg = load_config()
        shared = load_shared_paths(root / "config" / "shared_paths.yml")
        profiles = {
            "adrd": load_profile(root / "config/profiles/adrd.yml", shared=shared),
            "oncology_nsclc": load_profile(
                root / "config/profiles/oncology_nsclc.yml", shared=shared
            ),
        }

        for index, run in enumerate(runs):
            profile_cfg = profiles[run.profile].config
            run_dir = profile_cfg.paths.bronze_api_responses / f"run_id={run.run_id}"
            run_dir.mkdir(parents=True)
            page_src = Path(__file__).parent / "fixtures" / run.fixture_dir / run.page
            page_dst = run_dir / run.page
            if run.status_overrides or run.added:
                doc = _variant_page(
                    page_src,
                    status_overrides=run.status_overrides,
                    added=run.added,
                )
                page_dst.write_text(json.dumps(doc), encoding="utf-8")
            else:
                shutil.copy(page_src, page_dst)
                doc = json.loads(page_src.read_text(encoding="utf-8"))
            record_count = len(doc["studies"])
            manifest = IngestionManifest(
                ingestion_run_id=run.run_id,
                # Unique per run: two runs of one profile must not share a query
                # hash, which is what the incremental reuser matches on.
                query_hash=f"fixturequeryhash{index:02d}",
                endpoint="https://clinicaltrials.gov/api/v2/studies",
                condition=run.condition,
                params={"query.cond": run.condition},
                mode="incremental",
                # Mirrors src/ingest/extract_studies.py, which stamps the manifest
                # with profile.profile_id. The bronze glob in _sources.yml and the
                # `profile as indication_profile_id` alias in stg_trial_snapshots
                # read exactly this field, so a fixture that left it at the
                # "default" fallback would make the reliability mart's per-profile
                # grain unprovable.
                #
                # `UNSTAMPED_NSCLC_RUN_ID` deliberately *does* leave it there: that
                # run exists to reproduce a manifest written before profile
                # stamping landed, so mart_data_reliability's coalesce ordering
                # (silver first) is the only thing keeping its profile correct.
                profile=run.effective_manifest_profile,
                status="success",
                started_at_utc=run.started_at,
                ended_at_utc=run.started_at + timedelta(minutes=30),
                page_count=1,
                record_count=record_count,
                total_count_reported=record_count,
            )
            write_manifest(profile_cfg.paths.bronze_manifests, manifest)
            write_summary(profile_cfg.paths.bronze_manifests, manifest)
            assert run_transform(profile=profiles[run.profile]) == [run.run_id]
        assert cfg.paths.silver == profiles["adrd"].config.paths.silver  # one silver tree

        (root / "profiles.yml").write_text(
            """clinical_trials:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: data/warehouse/clinical_trials.duckdb
      threads: 4
""",
            encoding="utf-8",
        )
        (root / "data/warehouse").mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "dbt.cli.main",
                "build",
                "--project-dir",
                str(REPO_ROOT / "dbt_clinical_trials"),
                "--profiles-dir",
                str(root),
                "--target-path",
                str(root / "dbt_target"),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, (
            f"dbt build failed:\n{result.stdout[-3000:]}\n{result.stderr[-1000:]}"
        )
        return root
    finally:
        mp.undo()


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
