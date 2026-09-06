"""Run-directory retention: what may be deleted, and what must never be.

The invariant under test is not "old runs disappear" — it is that a *refreshable*
profile's bronze is never removed while its silver is absent, because
build_silver_entities raises FileNotFoundError for a missing bronze dir and the
transform then has no input to re-derive that run from
(src/transform/build_silver_entities.py:59). An ``ingest_only`` profile has no
silver and no transform ever, so that guard is skipped for it and its bronze
honors ``bronze_runs_to_keep`` — otherwise the largest consumer on the volume
would be unbounded.

Every deletion in this file lands inside a pytest ``tmp_path``: the fixtures
point ``CTI_PROJECT_ROOT`` at a temp copy of the config tree, ``tests/conftest.py``
clears the cached config and profile registry around every test, and the one test
that drives a real prune through the CLI injects a registry it built itself.
"""

import re
import shutil
from pathlib import Path

import pytest

from src.ingest.snapshot_manifest import IngestionManifest, write_manifest, write_summary
from src.utils.dates import utc_now
from src.utils.retention import (
    PrunedRun,
    RetentionConfig,
    RetentionError,
    prune_all,
    prune_profile,
    runs_to_prune,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ADRD_PROFILE_YAML = REPO_ROOT / "config/profiles/adrd.yml"
ONCOLOGY_PROFILE_YAML = REPO_ROOT / "config/profiles/oncology_nsclc.yml"
FULL_CATALOG_PROFILE_YAML = REPO_ROOT / "config/profiles/full_catalog.yml"


MINIMAL_RETENTION_YAML = """\
profile:
  id: test_ind
  display_name: Test Indication
  ingest_only: true

api:
  base_url: https://clinicaltrials.gov/api/v2
  query_params: {}

paths:
  bronze_api_responses: data/bronze/test_ind/api_responses
  bronze_manifests: data/bronze/test_ind/manifests
  silver: data/silver
  gold: data/gold
  duckdb: data/warehouse/x.duckdb
  quarantine: data/bronze/test_ind/manifests/quarantine

retention:
  bronze_runs_to_keep: 3
  snapshot_runs_to_keep: 4
"""


def _temp_config_tree(root: Path) -> Path:
    """Copy the real ``config/`` tree into a temp project root.

    ``load_profile()`` resolves a profile's taxonomy and
    ``config/shared_paths.yml`` against ``project_root()``, and the registry
    discovers profiles from ``project_root()/config/profiles`` — so a temp root
    needs those files before a profile can be pointed at it.
    ``project_config.yml`` is deliberately *not* copied: the
    ``project_root_tmp`` fixture writes its own, without a retention block, so
    the defaults stay under test.

    ``dirs_exist_ok`` because ``project_root_tmp`` now copies the same tree
    itself: without it the re-copy raises ``FileExistsError`` on the directory
    that is already there, and the files it rewrites are identical anyway.
    """
    dst = root / "config"
    dst.mkdir(parents=True, exist_ok=True)
    for src in sorted((REPO_ROOT / "config").glob("*.yml")):
        if src.name != "project_config.yml":
            shutil.copy(src, dst / src.name)
    shutil.copytree(REPO_ROOT / "config/profiles", dst / "profiles", dirs_exist_ok=True)
    return dst


def _mk_run(cfg, run_id: str, *, status: str = "success", with_silver: bool = True) -> None:
    manifest = IngestionManifest(
        ingestion_run_id=run_id,
        query_hash=f"hash{run_id[-4:]}",
        endpoint="https://clinicaltrials.gov/api/v2/studies",
        condition="Alzheimer Disease",
        params={"query.cond": "Alzheimer Disease"},
        mode="incremental",
        status=status,
        started_at_utc=utc_now(),
        ended_at_utc=utc_now(),
        page_count=1,
        record_count=10,
        total_count_reported=10,
    )
    write_manifest(cfg.paths.bronze_manifests, manifest)
    write_summary(cfg.paths.bronze_manifests, manifest)
    page_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "page=00001.json").write_text('{"studies": []}', encoding="utf-8")
    if with_silver:
        entity_dir = cfg.paths.silver / "silver_trials"
        entity_dir.mkdir(parents=True, exist_ok=True)
        (entity_dir / f"run_id={run_id}.parquet").write_bytes(b"PAR1")
        # The per-run reports src/quality/profiling.py and
        # src/transform/silver_stats.py write, in the underscore-prefixed dirs
        # that are reports rather than dbt-globbed entities.
        reports = cfg.paths.silver / "_profiles"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / f"profile_{run_id}.json").write_text("{}", encoding="utf-8")
        stats = cfg.paths.silver / "_transform_stats"
        stats.mkdir(parents=True, exist_ok=True)
        (stats / f"run_id={run_id}.json").write_text("{}", encoding="utf-8")


@pytest.fixture()
def profile_with_runs(project_root_tmp: Path, request):
    """A real adrd-shaped profile tree with N success runs."""
    from src.profiles import load_profile

    count = getattr(request, "param", 4)
    _temp_config_tree(project_root_tmp)
    profile = load_profile(ADRD_PROFILE_YAML)
    cfg = profile.config
    for i in range(count):
        _mk_run(cfg, f"2026090{i + 1}T000000Z_run{i:08d}")
    return profile


# ---------------------------------------------------------------------------
# runs_to_prune: the ordering the whole module rests on
# ---------------------------------------------------------------------------


def test_runs_to_prune_keeps_the_newest():
    ids = [f"2026090{i}T000000Z_x" for i in range(1, 6)]
    assert runs_to_prune(ids, keep=2) == ids[:3]
    assert runs_to_prune(ids, keep=10) == []
    assert runs_to_prune([], keep=1) == []


def test_runs_to_prune_returns_oldest_first_for_unordered_input():
    """ingestion_run_id is UTC-compact, so sorted() *is* chronological; a caller
    that hands over a shuffled list still gets the oldest victims first."""
    ids = ["20260903T000000Z_c", "20260901T000000Z_a", "20260902T000000Z_b"]
    assert runs_to_prune(ids, keep=1) == ["20260901T000000Z_a", "20260902T000000Z_b"]


def test_runs_to_prune_rejects_a_negative_keep():
    with pytest.raises(RetentionError, match="keep must be >= 0"):
        runs_to_prune(["20260901T000000Z_a"], keep=-1)


# ---------------------------------------------------------------------------
# The two horizons
# ---------------------------------------------------------------------------


def test_bronze_prune_removes_run_dir_and_manifest_files(profile_with_runs):
    removed = prune_profile(
        profile_with_runs, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=6)
    )
    assert len(removed) == 3
    assert all(isinstance(r, PrunedRun) for r in removed)
    assert removed[0].profile_id == "adrd"

    cfg = profile_with_runs.config
    kept = sorted(p.name for p in (cfg.paths.bronze_api_responses).iterdir())
    assert kept == ["run_id=20260904T000000Z_run00000003"]


def test_snapshot_prune_removes_silver_and_manifest_together(profile_with_runs):
    # 4 runs, snapshot depth 2: silver keeps the 2 newest. Bronze depth 1 also
    # drops the 3rd-newest run's raw pages, so 3 runs are touched in total.
    removed = prune_profile(
        profile_with_runs, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=2)
    )
    assert len(removed) == 3
    cfg = profile_with_runs.config
    assert sorted(p.stem for p in (cfg.paths.silver / "silver_trials").iterdir()) == [
        "run_id=20260903T000000Z_run00000002",
        "run_id=20260904T000000Z_run00000003",
    ]
    # The manifests dir is part of the snapshot record: a run whose silver is
    # gone loses its manifest_*.json and its summary_* files with it.
    manifests = sorted(p.name for p in cfg.paths.bronze_manifests.iterdir())
    assert all("20260903" in m or "20260904" in m for m in manifests), manifests
    # The underscore-prefixed silver dirs are reports, not entities: the
    # directories survive, their per-run files follow the snapshot horizon.
    assert (cfg.paths.silver / "_profiles").is_dir()
    assert (cfg.paths.silver / "_transform_stats").is_dir()
    assert sorted(p.name for p in (cfg.paths.silver / "_profiles").iterdir()) == [
        "profile_20260903T000000Z_run00000002.json",
        "profile_20260904T000000Z_run00000003.json",
    ]
    assert sorted(p.name for p in (cfg.paths.silver / "_transform_stats").iterdir()) == [
        "run_id=20260903T000000Z_run00000002.json",
        "run_id=20260904T000000Z_run00000003.json",
    ]


def test_the_manifest_index_follows_the_snapshot_horizon(profile_with_runs):
    """``load_manifests`` is how prune_profile knows which runs exist, and silver
    is bounded by snapshot depth. If a bronze prune deleted the manifests of runs
    whose silver survives, the next prune would see a single run and could never
    bound silver again — it would grow one run per week forever."""
    cfg = profile_with_runs.config
    prune_profile(
        profile_with_runs, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=6)
    )
    assert len(list(cfg.paths.bronze_manifests.glob("manifest_*.json"))) == 4

    # A weekly run lands, then the snapshot horizon bites on the fifth one.
    _mk_run(cfg, "20260905T000000Z_run00000004")
    prune_profile(
        profile_with_runs, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=2)
    )
    assert sorted(p.stem for p in (cfg.paths.silver / "silver_trials").iterdir()) == [
        "run_id=20260904T000000Z_run00000003",
        "run_id=20260905T000000Z_run00000004",
    ]
    assert len(list(cfg.paths.bronze_manifests.glob("manifest_*.json"))) == 2


def test_failed_runs_are_never_pruned_candidates(tmp_path, monkeypatch):
    """A partial/failed run is the forensic record of a bad ingest."""
    from src.config import get_config
    from src.profiles import load_profile

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    get_config.cache_clear()
    _temp_config_tree(tmp_path)
    profile = load_profile(ADRD_PROFILE_YAML)
    cfg = profile.config
    _mk_run(cfg, "20260901T000000Z_old00000", status="failed")
    _mk_run(cfg, "20260902T000000Z_old10000", status="success")
    _mk_run(cfg, "20260903T000000Z_old20000", status="success")

    prune_profile(profile, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=6))
    assert (cfg.paths.bronze_api_responses / "run_id=20260901T000000Z_old00000").is_dir()
    assert not (cfg.paths.bronze_api_responses / "run_id=20260902T000000Z_old10000").is_dir()


def test_bronze_deeper_than_snapshots_is_refused(profile_with_runs):
    """`bronze_runs_to_keep > snapshot_runs_to_keep` strands bytes: the runs
    between the two horizons lose the manifest that is the prune's only index, so
    their page directories are invisible to every later prune. The guard used to
    refuse the opposite, harmless direction (snapshot deeper than bronze — that is
    what the shipped 1/6 is); this asserts the reversal in both directions."""
    with pytest.raises(RetentionError, match="bronze_runs_to_keep.*exceeds"):
        prune_profile(
            profile_with_runs, RetentionConfig(bronze_runs_to_keep=8, snapshot_runs_to_keep=1)
        )

    # The safe direction — snapshots deeper than bronze — must not raise. It is
    # what every shipped config does.
    prune_profile(
        profile_with_runs, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=8)
    )


def test_an_ingest_only_profile_honors_its_bronze_horizon(tmp_path, monkeypatch):
    """An `ingest_only` profile has no silver — never had, never will — so the
    missing-silver guard is skipped for it and `bronze_runs_to_keep` is what
    bounds its raw pages. Reverses the earlier assertion, which let the guard
    collapse full_catalog's bronze depth to the snapshot depth and left ~6 runs of
    a whole-registry pull on a 1 GB volume."""
    from src.profiles import load_profile

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    _temp_config_tree(tmp_path)
    profile = load_profile(FULL_CATALOG_PROFILE_YAML)
    assert profile.ingest_only
    cfg = profile.config
    assert not cfg.paths.silver.exists()
    for i in range(3):
        _mk_run(cfg, f"2026090{i + 1}T000000Z_run{i:08d}", with_silver=False)

    removed = prune_profile(
        profile, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=2)
    )
    # Bronze depth 1: both older page directories go, including the one the
    # snapshot horizon keeps a manifest for. Nothing is unrecoverable — there is
    # no transform for this profile to re-derive anything.
    assert {r.run_id for r in removed} == {
        "20260901T000000Z_run00000000",
        "20260902T000000Z_run00000001",
    }
    assert not (cfg.paths.bronze_api_responses / "run_id=20260901T000000Z_run00000000").exists()
    assert not (cfg.paths.bronze_api_responses / "run_id=20260902T000000Z_run00000001").exists()
    assert (cfg.paths.bronze_api_responses / "run_id=20260903T000000Z_run00000002").is_dir()
    # The two horizons stay independent: the manifest of the run inside the
    # snapshot depth survives, so the prune can still see it next week.
    manifests = sorted(p.name for p in cfg.paths.bronze_manifests.glob("manifest_*.json"))
    assert manifests == [
        "manifest_20260902T000000Z_run00000001.json",
        "manifest_20260903T000000Z_run00000002.json",
    ]


def test_a_refreshable_profile_keeps_bronze_while_its_silver_is_missing(tmp_path, monkeypatch):
    """The other half of the ruling: for a profile that does have a transform, a
    run whose silver was never built must keep its bronze, or the run becomes
    unrecoverable — so its pages are pruned only where the snapshot horizon prunes
    too."""
    from src.profiles import load_profile

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    _temp_config_tree(tmp_path)
    profile = load_profile(ADRD_PROFILE_YAML)
    assert not profile.ingest_only
    cfg = profile.config
    for i in range(3):
        _mk_run(cfg, f"2026090{i + 1}T000000Z_run{i:08d}", with_silver=False)

    removed = prune_profile(
        profile, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=2)
    )
    assert {r.run_id for r in removed} == {"20260901T000000Z_run00000000"}
    assert (cfg.paths.bronze_api_responses / "run_id=20260902T000000Z_run00000001").is_dir()
    assert (cfg.paths.bronze_api_responses / "run_id=20260903T000000Z_run00000002").is_dir()


# ---------------------------------------------------------------------------
# What retention must never touch
# ---------------------------------------------------------------------------


def test_quarantine_is_untouched(profile_with_runs):
    cfg = profile_with_runs.config
    qdir = cfg.paths.quarantine
    qdir.mkdir(parents=True, exist_ok=True)
    victim = qdir / "quarantine_20260901T000000Z_run00000000.json"
    victim.write_text("[]", encoding="utf-8")
    prune_profile(
        profile_with_runs, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=2)
    )
    assert victim.exists()


def test_schema_baseline_and_drift_reports_survive(profile_with_runs):
    """_schema_baseline.json lives in bronze_api_responses.parent and must never
    match a prune pattern."""
    cfg = profile_with_runs.config
    baseline = cfg.paths.bronze_api_responses.parent / "_schema_baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    prune_profile(
        profile_with_runs, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=6)
    )
    assert baseline.exists()


def test_a_manifest_cannot_name_a_target_outside_its_run_dir(tmp_path, monkeypatch):
    """``ingestion_run_id`` is read out of manifest JSON and used to build every
    deletion path, so a corrupt or hostile value must be refused, not joined.

    ``bronze_api_responses / "run_id=../../"`` resolves back to
    ``bronze_api_responses`` itself once a directory literally named ``run_id=..``
    exists — one manifest line could otherwise rmtree a profile's whole page tree.
    """
    from src.profiles import load_profile

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    _temp_config_tree(tmp_path)
    profile = load_profile(ADRD_PROFILE_YAML)
    cfg = profile.config
    _mk_run(cfg, "20260901T000000Z_run00000000")
    _mk_run(cfg, "20260902T000000Z_run00000001")
    (cfg.paths.bronze_api_responses / "run_id=..").mkdir()

    hostile = IngestionManifest(
        ingestion_run_id="../../",
        query_hash="hashdeadbeef",
        endpoint="https://clinicaltrials.gov/api/v2/studies",
        params={},
        status="success",
        started_at_utc=utc_now(),
        ended_at_utc=utc_now(),
    )
    (cfg.paths.bronze_manifests / "manifest_hostile.json").write_text(
        hostile.model_dump_json(), encoding="utf-8"
    )

    removed = prune_profile(
        profile, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=2)
    )
    assert [r.run_id for r in removed] == ["20260901T000000Z_run00000000"]
    assert cfg.paths.bronze_api_responses.is_dir()
    assert (cfg.paths.bronze_api_responses / "run_id=..").is_dir()
    for entry in removed:
        for path in entry.removed:
            assert path.is_relative_to(cfg.paths.bronze_api_responses) or path.is_relative_to(
                cfg.paths.bronze_manifests
            )


def test_the_deletion_layer_refuses_anything_outside_the_profile_roots(profile_with_runs):
    """The containment check in `_remove` is the last line of defence, so it is
    tested directly with paths the code above it must never produce: a root's own
    parent, another profile's page tree, and a path outside the data tree.

    ``silver`` is shared by every profile, so what keeps a prune inside one profile
    is that its candidates are named by that profile's own manifests — this layer
    is what stops a bug above it from becoming a cross-profile deletion.
    """
    from src.utils.retention import _remove

    cfg = profile_with_runs.config
    legit = cfg.paths.bronze_api_responses / "run_id=20260901T000000Z_run00000000"
    assert (legit / "page=00001.json").is_file()  # the fixture's populated run dir
    out_of_roots = [
        cfg.paths.bronze_api_responses.parent,
        cfg.paths.bronze_api_responses.parent.parent / "full_catalog" / "api_responses",
        cfg.paths.bronze_api_responses.parents[2] / "outside",
    ]
    for path in out_of_roots:
        path.mkdir(parents=True, exist_ok=True)

    deleted = _remove(cfg.paths, [legit, *out_of_roots], profile_with_runs, "run")
    assert deleted == [legit]
    assert not legit.exists()
    assert all(path.is_dir() for path in out_of_roots)


def test_pruning_one_profile_leaves_another_profile_silver_alone(tmp_path, monkeypatch):
    """`config/shared_paths.yml` gives every refreshable profile the *same* silver
    root, and a silver file is named by run id alone — nothing in the path says
    which profile owns it. This is the test that states the property out loud:
    a profile's candidates come from its own manifests, so an adrd prune cannot
    eat the oncology history it shares a directory with.
    """
    from src.profiles import load_profile

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    _temp_config_tree(tmp_path)
    adrd = load_profile(ADRD_PROFILE_YAML)
    nsclc = load_profile(ONCOLOGY_PROFILE_YAML)
    assert adrd.config.paths.silver == nsclc.config.paths.silver, "silver is shared by design"
    for i in range(4):
        _mk_run(adrd.config, f"2026090{i + 1}T000000Z_adrd000{i}")
        _mk_run(nsclc.config, f"2026090{i + 1}T000000Z_onc000{i}")

    prune_profile(adrd, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=2))

    trials = sorted(p.name for p in (adrd.config.paths.silver / "silver_trials").iterdir())
    assert [t for t in trials if "_onc" in t] == [
        f"run_id=2026090{i}T000000Z_onc000{i - 1}.parquet" for i in (1, 2, 3, 4)
    ]
    assert [t for t in trials if "_adrd" in t] == [
        "run_id=20260903T000000Z_adrd0002.parquet",
        "run_id=20260904T000000Z_adrd0003.parquet",
    ]
    reports = sorted(p.name for p in (adrd.config.paths.silver / "_profiles").iterdir())
    assert len([r for r in reports if "_onc" in r]) == 4
    # Each profile's own bronze/manifest tree is likewise untouched by the other.
    assert len(list(nsclc.config.paths.bronze_manifests.glob("manifest_*.json"))) == 4


def test_dry_run_names_the_paths_and_deletes_nothing(profile_with_runs):
    cfg = profile_with_runs.config
    removed = prune_profile(
        profile_with_runs,
        RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=2),
        dry_run=True,
    )
    assert len(removed) == 3
    assert removed[0].removed
    assert all((cfg.paths.bronze_api_responses / f"run_id={r.run_id}").is_dir() for r in removed)
    assert len(list((cfg.paths.silver / "silver_trials").iterdir())) == 4


# ---------------------------------------------------------------------------
# prune_all / config plumbing
# ---------------------------------------------------------------------------


class _FakeRegistry:
    def __init__(self, profiles: list) -> None:
        self._profiles = profiles

    def all(self) -> list:
        return list(self._profiles)


def test_prune_all_reaches_every_profile_including_ingest_only(tmp_path, monkeypatch):
    """full_catalog's pages are the largest single consumer on the volume, so
    prune_all must not restrict itself to the refreshable profiles."""
    from src.profiles import load_profile

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    _temp_config_tree(tmp_path)
    adrd = load_profile(ADRD_PROFILE_YAML)
    catalog = load_profile(FULL_CATALOG_PROFILE_YAML)
    assert catalog.ingest_only
    for i in range(2):
        _mk_run(adrd.config, f"2026090{i + 1}T000000Z_run{i:08d}")
    # Past the *bronze* depth, and there is no silver anywhere: only the
    # ingest_only exemption on the missing-silver guard can act on these.
    for i in range(8):
        _mk_run(catalog.config, f"2026091{i}T000000Z_run{i:08d}", with_silver=False)

    removed = prune_all(_FakeRegistry([adrd, catalog]))
    assert {r.profile_id for r in removed} == {"adrd", "full_catalog"}
    pages = sorted(p.name for p in catalog.config.paths.bronze_api_responses.iterdir())
    assert pages == ["run_id=20260917T000000Z_run00000007"], "bronze_runs_to_keep must bite"
    # The snapshot horizon is untouched by that: six runs still have an index.
    assert len(list(catalog.config.paths.bronze_manifests.glob("manifest_*.json"))) == 6


def test_prune_all_refuses_an_empty_registry(tmp_path, monkeypatch):
    """Same posture as orchestrate and init-data-dirs: an empty registry is a
    mis-mounted config/profiles/, not a prune that landed."""
    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    with pytest.raises(RetentionError, match="no profiles"):
        prune_all(_FakeRegistry([]))


def test_prune_all_continues_after_a_non_retention_failure(tmp_path, monkeypatch):
    """A broken tree on one profile must not stop the others from being pruned."""
    import src.utils.retention as retention
    from src.profiles import load_profile

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    _temp_config_tree(tmp_path)
    adrd = load_profile(ADRD_PROFILE_YAML)
    catalog = load_profile(FULL_CATALOG_PROFILE_YAML)
    for i in range(2):
        _mk_run(adrd.config, f"2026090{i + 1}T000000Z_run{i:08d}")
        _mk_run(catalog.config, f"2026090{i + 1}T000000Z_run{i:08d}")

    real = retention.prune_profile

    def flaky(profile, *args, **kwargs):  # type: ignore[no-untyped-def]
        if profile.profile_id == "full_catalog":
            raise OSError("simulated I/O failure")
        return real(profile, *args, **kwargs)

    monkeypatch.setattr(retention, "prune_profile", flaky)
    removed = prune_all(_FakeRegistry([adrd, catalog]))
    assert {r.profile_id for r in removed} == {"adrd"}


def test_prune_all_propagates_a_retention_refusal(tmp_path, monkeypatch):
    """An incoherent horizon is not a per-profile detail to log and move on: it
    has to reach the caller, which exits non-zero."""
    import src.utils.retention as retention
    from src.profiles import load_profile

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    _temp_config_tree(tmp_path)
    adrd = load_profile(ADRD_PROFILE_YAML)
    catalog = load_profile(FULL_CATALOG_PROFILE_YAML)

    real = retention.prune_profile

    def incoherent(profile, *args, **kwargs):  # type: ignore[no-untyped-def]
        if profile.profile_id == "full_catalog":
            raise RetentionError("incoherent retention")
        return real(profile, *args, **kwargs)

    monkeypatch.setattr(retention, "prune_profile", incoherent)
    with pytest.raises(RetentionError, match="incoherent"):
        prune_all(_FakeRegistry([adrd, catalog]))


def test_profile_yaml_retention_is_parsed(project_root_tmp: Path) -> None:
    """Every profile YAML carries a retention block; if the profile loader
    ignored it those blocks would be decoration."""
    from src.profiles import SharedPaths, load_profile

    root = project_root_tmp
    profile_yaml = root / "config/profiles/test_ind.yml"
    profile_yaml.parent.mkdir(parents=True, exist_ok=True)
    profile_yaml.write_text(MINIMAL_RETENTION_YAML, encoding="utf-8")
    shared = SharedPaths(
        silver=root / "data/silver",
        gold=root / "data/gold",
        duckdb=root / "data/warehouse/x.duckdb",
    )
    profile = load_profile(profile_yaml, shared=shared)
    assert profile.config.retention.bronze_runs_to_keep == 3
    assert profile.config.retention.snapshot_runs_to_keep == 4


def test_retention_config_defaults_when_absent(project_root_tmp: Path) -> None:
    from src.config import load_config

    cfg = load_config()
    assert cfg.retention.bronze_runs_to_keep == 1
    assert cfg.retention.snapshot_runs_to_keep == 6


def test_shipped_configs_agree_on_the_horizon() -> None:
    """One knob, no special cases: the deploy docs and the volume budget both
    assume adrd, oncology_nsclc, full_catalog and the default config say the
    same thing (and that the horizon is one the guard will accept)."""
    from src.config import load_config
    from src.profiles import load_profile

    expected = RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=6)
    assert load_config().retention == expected
    for yml in sorted((REPO_ROOT / "config/profiles").glob("*.yml")):
        assert load_profile(yml).config.retention == expected, yml.name
    assert expected.bronze_runs_to_keep <= expected.snapshot_runs_to_keep, (
        "bronze deeper than the snapshot depth is the ordering prune's coherence "
        "guard refuses, so the shipped horizon must not be inside it"
    )


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_parser_prune_data_is_dry_run_opt_in():
    from src.cli import build_parser

    args = build_parser().parse_args(["prune-data"])
    assert args.command == "prune-data"
    assert args.dry_run is False
    assert build_parser().parse_args(["prune-data", "--dry-run"]).dry_run is True


def test_cli_prune_data_deletes_inside_the_temp_root_only(tmp_path, monkeypatch):
    """End-to-end through ``python -m src.cli prune-data``: the horizon comes
    from config/profiles/adrd.yml and every removed path is inside tmp_path.

    This is the only test in the suite that runs a real (non-dry) prune, so its
    safety is stated in the test body rather than left to global cache state: the
    root the modules compute is asserted to *be* the temp root, and the registry
    ``prune_all`` falls back to is replaced with one built here, holding profiles
    whose deletion roots were checked to sit inside that temp tree. A cached
    singleton from an earlier test module therefore cannot contribute a profile
    pointing at the repo's ``data/``.
    """
    from src.cli import main
    from src.profiles import load_profile
    from src.utils.paths import project_root
    from src.utils.retention import _retention_roots

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    _temp_config_tree(tmp_path)
    adrd = load_profile(ADRD_PROFILE_YAML)
    for i in range(2):
        _mk_run(adrd.config, f"2026090{i + 1}T000000Z_run{i:08d}")

    root = project_root()
    assert root == tmp_path.resolve(), "the prune must not be looking at the real data tree"
    for path in _retention_roots(adrd.config.paths):
        assert path.is_relative_to(root), path
    monkeypatch.setattr("src.profiles.get_registry", lambda: _FakeRegistry([adrd]))

    assert main(["prune-data"]) == 0
    pages = adrd.config.paths.bronze_api_responses
    assert not (pages / "run_id=20260901T000000Z_run00000000").exists()
    assert (pages / "run_id=20260902T000000Z_run00000001").is_dir()


def test_cli_prune_data_reports_a_refusal_as_failure(tmp_path, monkeypatch):
    """A retention block the coherence guard refuses — bronze deeper than the
    snapshot horizon, which strands raw pages — must exit non-zero, not print
    '0 run(s) removed' and let `make pipeline` call it a success."""
    from src.cli import main

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    config_dir = _temp_config_tree(tmp_path)
    adrd = config_dir / "profiles/adrd.yml"
    text, hits = re.subn(
        r"(bronze_runs_to_keep:\s*)\d+",
        r"\g<1>40",
        adrd.read_text(encoding="utf-8"),
    )
    assert hits == 1
    adrd.write_text(text, encoding="utf-8")

    assert main(["prune-data", "--dry-run"]) == 1
