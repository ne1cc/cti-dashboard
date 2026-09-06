import shutil
import subprocess
import sys
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


@pytest.fixture
def project_root_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "project_config.yml").write_text(CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Clear the cached config *and* profile registry around every test.

    Both are ``lru_cache``d singletons resolved against ``CTI_PROJECT_ROOT``, so a
    registry left warm by an earlier test module hands a later one profiles
    pointing at the repo's real ``data/`` tree — fatal for any test that runs a
    real (non-dry) prune. Repo-wide rather than per-module so no future prune test
    has to remember it. Called unconditionally, not via ``getattr``: if an
    accessor stops being a cached singleton this fails loudly instead of quietly
    removing the pin.
    """
    from src.config import get_config
    from src.profiles import get_registry

    get_config.cache_clear()
    get_registry.cache_clear()
    yield
    get_config.cache_clear()
    get_registry.cache_clear()


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


@pytest.fixture(scope="session")
def fixture_project_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build bronze→silver→gold from the fixture snapshot; dbt build once per session.

    dbt runs in a subprocess: dbt's in-process adapter keeps the DuckDB file
    open, which would block the read-only connections the assertions use.
    """
    root = tmp_path_factory.mktemp("fixture_project")
    mp = pytest.MonkeyPatch()
    mp.setenv("CTI_PROJECT_ROOT", str(root))
    try:
        (root / "config").mkdir()
        (root / "config" / "project_config.yml").write_text(CONFIG_YAML, encoding="utf-8")
        for name in (
            "condition_taxonomy.yml",
            "geography_rules.yml",
            "score_weights.yml",
            "roi_assumptions.yml",
        ):
            shutil.copy(REPO_ROOT / "config" / name, root / "config" / name)

        from src.config import load_config
        from src.ingest.snapshot_manifest import (
            IngestionManifest,
            write_manifest,
            write_summary,
        )
        from src.utils.dates import utc_now

        cfg = load_config()
        run_dir = cfg.paths.bronze_api_responses / f"run_id={FIXTURE_RUN_ID}"
        run_dir.mkdir(parents=True)
        shutil.copy(
            Path(__file__).parent / "fixtures/bronze_snapshot/page=00001.json",
            run_dir / "page=00001.json",
        )

        manifest = IngestionManifest(
            ingestion_run_id=FIXTURE_RUN_ID,
            query_hash="fixturequeryhash0001",
            endpoint="https://clinicaltrials.gov/api/v2/studies",
            condition="Alzheimer Disease",
            params={"query.cond": "Alzheimer Disease"},
            mode="incremental",
            status="success",
            started_at_utc=utc_now(),
            ended_at_utc=utc_now(),
            page_count=1,
            record_count=10,
            total_count_reported=10,
        )
        write_manifest(cfg.paths.bronze_manifests, manifest)
        write_summary(cfg.paths.bronze_manifests, manifest)

        from src.transform.build_silver_entities import run_transform

        assert run_transform(config=cfg) == [FIXTURE_RUN_ID]

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
