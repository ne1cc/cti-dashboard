import shutil
import subprocess
import sys
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
FIXTURE_SNAPSHOT_DAY = datetime(2026, 9, 1, tzinfo=UTC)
ADRD_STARTED_AT = FIXTURE_SNAPSHOT_DAY + timedelta(hours=10)
NSCLC_STARTED_AT = FIXTURE_SNAPSHOT_DAY + timedelta(hours=11)


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
            "condition_taxonomy_nsclc.yml",
            "geography_rules.yml",
            "score_weights.yml",
            "roi_assumptions.yml",
            "shared_paths.yml",
        ):
            shutil.copy(REPO_ROOT / "config" / name, root / "config" / name)
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

        runs = [
            ("adrd", FIXTURE_RUN_ID, "page=00001.json", ADRD_STARTED_AT, "Alzheimer Disease"),
            (
                "oncology_nsclc",
                NSCLC_FIXTURE_RUN_ID,
                "page=00001.json",
                NSCLC_STARTED_AT,
                "Non-Small Cell Lung Cancer",
            ),
        ]
        for pid, run_id, page_file, started_at, condition in runs:
            profile_cfg = profiles[pid].config
            fixture_dir = "bronze_snapshot_nsclc" if pid != "adrd" else "bronze_snapshot"
            run_dir = profile_cfg.paths.bronze_api_responses / f"run_id={run_id}"
            run_dir.mkdir(parents=True)
            shutil.copy(
                Path(__file__).parent / "fixtures" / fixture_dir / page_file,
                run_dir / page_file,
            )
            manifest = IngestionManifest(
                ingestion_run_id=run_id,
                query_hash=f"fixturequeryhash{pid[-1:]}01",
                endpoint="https://clinicaltrials.gov/api/v2/studies",
                condition=condition,
                params={"query.cond": condition},
                mode="incremental",
                # Mirrors src/ingest/extract_studies.py, which stamps the manifest
                # with profile.profile_id. The bronze glob in _sources.yml and the
                # `profile as indication_profile_id` alias in stg_trial_snapshots
                # read exactly this field, so a fixture that left it at the
                # "default" fallback would make the reliability mart's per-profile
                # grain unprovable.
                profile=pid,
                status="success",
                started_at_utc=started_at,
                ended_at_utc=started_at + timedelta(minutes=30),
                page_count=1,
                record_count=10,
                total_count_reported=10,
            )
            write_manifest(profile_cfg.paths.bronze_manifests, manifest)
            write_summary(profile_cfg.paths.bronze_manifests, manifest)
            assert run_transform(profile=profiles[pid]) == [run_id]
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
