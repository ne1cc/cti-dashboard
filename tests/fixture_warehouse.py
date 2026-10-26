"""Fixture warehouse creation and standalone entry point for testing and CI.

Supports preparing a hermetic fixture root (config, bronze snapshots, manifests,
silver Parquet entities, and dbt profiles) with or without running `dbt build`.
Refuses to overwrite nonempty destination directories.

CLI entry point:
    python -m tests.fixture_warehouse --root <path> [--prepare-only]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_RUN_ID = "20260901T120000Z_fixture01"
NSCLC_FIXTURE_RUN_ID = "20260901T120001Z_fixture02"

FIXTURE_SNAPSHOT_DAY = datetime(2026, 9, 1, tzinfo=UTC)
ADRD_STARTED_AT = FIXTURE_SNAPSHOT_DAY + timedelta(hours=10)
NSCLC_STARTED_AT = FIXTURE_SNAPSHOT_DAY + timedelta(hours=11)

ADRD_CONDITION = "Alzheimer Disease"
NSCLC_CONDITION = "Non-Small Cell Lung Cancer"

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


@dataclass(frozen=True)
class AddedStudy:
    """One extra study on a *variant* bronze page, cloned from an existing study."""

    nct_id: str
    clone_of: str
    overall_status: str
    phase: str | None = None
    condition: str | None = None
    sponsor: str | None = None
    location: tuple[str, str, str] | None = None  # facility, city, state


@dataclass(frozen=True)
class FixtureRun:
    """One bronze->silver->manifest cycle inside a fixture warehouse."""

    profile: str  # registry profile_id
    run_id: str
    started_at: datetime
    condition: str
    fixture_dir: str
    page: str = "page=00001.json"
    manifest_profile: str | None = None  # None -> same as `profile`
    status_overrides: dict[str, str] = field(default_factory=dict)
    added: tuple[AddedStudy, ...] = ()
    study_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    retrieved_at: str | None = None
    final_status: str = "success"

    @property
    def effective_manifest_profile(self) -> str:
        return self.manifest_profile or self.profile


def base_adrd_run() -> FixtureRun:
    return FixtureRun(
        profile="adrd",
        run_id=FIXTURE_RUN_ID,
        started_at=ADRD_STARTED_AT,
        condition=ADRD_CONDITION,
        fixture_dir="bronze_snapshot",
    )


def base_nsclc_run() -> FixtureRun:
    return FixtureRun(
        profile="oncology_nsclc",
        run_id=NSCLC_FIXTURE_RUN_ID,
        started_at=NSCLC_STARTED_AT,
        condition=NSCLC_CONDITION,
        fixture_dir="bronze_snapshot_nsclc",
    )


def default_runs() -> list[FixtureRun]:
    return [base_adrd_run(), base_nsclc_run()]


def variant_page(
    src: Path,
    *,
    status_overrides: dict[str, str],
    added: tuple[AddedStudy, ...],
) -> dict[str, Any]:
    """The bronze page at `src` with `status_overrides` applied and `added` cloned in."""
    doc: dict[str, Any] = json.loads(src.read_text(encoding="utf-8"))
    studies: list[dict[str, Any]] = doc["studies"]
    by_id = {s["protocolSection"]["identificationModule"]["nctId"]: s for s in studies}
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
        clone: dict[str, Any] = json.loads(json.dumps(template))
        section = clone["protocolSection"]
        ids = section["identificationModule"]
        ids["nctId"] = spec.nct_id
        ids["briefTitle"] = f"{ids.get('briefTitle', 'Fixture study')} [variant {spec.nct_id}]"
        status_mod = section["statusModule"]
        status_mod["overallStatus"] = spec.overall_status
        status_mod["lastKnownStatus"] = spec.overall_status
        if spec.phase is not None:
            section.setdefault("designModule", {})["phases"] = [spec.phase]
        if spec.condition is not None:
            section.setdefault("conditionsModule", {})["conditions"] = [spec.condition]
        if spec.sponsor is not None:
            section.setdefault("sponsorCollaboratorsModule", {}).setdefault("leadSponsor", {})[
                "name"
            ] = spec.sponsor
        if spec.location is not None:
            facility, city, state = spec.location
            section.setdefault("contactsLocationsModule", {})["locations"] = [
                {
                    "facility": facility,
                    "city": city,
                    "state": state,
                    "zip": "00000",
                    "country": "United States",
                    "status": spec.overall_status,
                }
            ]
        studies.append(clone)
    return doc


def _clear_caches() -> None:
    from src.config import get_config
    from src.profiles import get_registry
    from src.transform.normalize_conditions import get_taxonomy
    from src.transform.normalize_locations import get_geography_rules

    for cached in (get_config, get_registry, get_taxonomy, get_geography_rules):
        cached.cache_clear()


def prepare_fixture_root(
    root: Path,
    runs: list[FixtureRun] | None = None,
    *,
    session_timezone: str | None = None,
) -> Path:
    """Prepares directory structure, config, bronze, and silver data without running dbt.

    Refuses to overwrite nonempty destination directories.
    """
    root = root.resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Destination directory already exists and is not empty: {root}")

    runs = runs if runs is not None else default_runs()

    prev_cti_root = os.environ.get("CTI_PROJECT_ROOT")
    os.environ["CTI_PROJECT_ROOT"] = str(root)
    _clear_caches()

    try:
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / "config" / "project_config.yml").write_text(CONFIG_YAML, encoding="utf-8")
        for cfg_name in (
            "geography_rules.yml",
            "shared_paths.yml",
        ):
            shutil.copy(REPO_ROOT / "config" / cfg_name, root / "config" / cfg_name)
        for taxonomy in (REPO_ROOT / "config").glob("condition_taxonomy*.yml"):
            shutil.copy(taxonomy, root / "config" / taxonomy.name)
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

        fixtures_dir = Path(__file__).parent / "fixtures"

        for index, run in enumerate(runs):
            profile_cfg = profiles[run.profile].config
            run_dir = profile_cfg.paths.bronze_api_responses / f"run_id={run.run_id}"
            run_dir.mkdir(parents=True, exist_ok=True)
            page_src = fixtures_dir / run.fixture_dir / run.page
            page_dst = run_dir / run.page
            if run.status_overrides or run.added:
                doc = variant_page(
                    page_src,
                    status_overrides=run.status_overrides,
                    added=run.added,
                )
                page_dst.write_text(json.dumps(doc), encoding="utf-8")
            else:
                shutil.copy(page_src, page_dst)
                doc = json.loads(page_src.read_text(encoding="utf-8"))

            if run.study_overrides:
                for study in doc["studies"]:
                    section = study["protocolSection"]
                    nct_id = section["identificationModule"]["nctId"]
                    for module, fields in run.study_overrides.get(nct_id, {}).items():
                        section[module].update(fields)
                page_dst.write_text(json.dumps(doc), encoding="utf-8")

            if run.retrieved_at:
                metadata_dir = run_dir / "_page_metadata"
                metadata_dir.mkdir(parents=True, exist_ok=True)
                (metadata_dir / run.page).write_text(
                    json.dumps({"retrieved_at_utc": run.retrieved_at}), encoding="utf-8"
                )

            record_count = len(doc["studies"])
            manifest = IngestionManifest(
                ingestion_run_id=run.run_id,
                query_hash=f"fixturequeryhash{index:02d}",
                endpoint="https://clinicaltrials.gov/api/v2/studies",
                condition=run.condition,
                params={"query.cond": run.condition},
                mode="incremental",
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
            transform_result = run_transform(profile=profiles[run.profile])
            assert transform_result == [run.run_id], f"Transform failed for {run.run_id}"
            if run.final_status != "success":
                manifest.status = run.final_status
                write_manifest(profile_cfg.paths.bronze_manifests, manifest)
                write_summary(profile_cfg.paths.bronze_manifests, manifest)

        assert cfg.paths.silver == profiles["adrd"].config.paths.silver

        (root / "profiles.yml").write_text(
            f"""clinical_trials:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: {root}/data/warehouse/clinical_trials.duckdb
      threads: 4
      settings:
        file_search_path: '{root}'
"""
            + (f"        TimeZone: {session_timezone}\n" if session_timezone else ""),
            encoding="utf-8",
        )
        (root / "data" / "warehouse").mkdir(parents=True, exist_ok=True)
        return root
    finally:
        if prev_cti_root is not None:
            os.environ["CTI_PROJECT_ROOT"] = prev_cti_root
        else:
            os.environ.pop("CTI_PROJECT_ROOT", None)
        _clear_caches()


def build_dbt_fixture(
    root: Path,
    *,
    dbt_vars: dict[str, object] | None = None,
    target_path: Path | None = None,
) -> None:
    """Runs dbt build against the prepared fixture root."""
    build_command = [
        sys.executable,
        "-m",
        "dbt.cli.main",
        "build",
        "--project-dir",
        str(REPO_ROOT / "dbt_clinical_trials"),
        "--profiles-dir",
        str(root),
        "--target-path",
        str(target_path or (root / "dbt_target")),
    ]
    if dbt_vars:
        build_command += ["--vars", json.dumps(dbt_vars)]
    result = subprocess.run(
        build_command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"dbt build failed (exit code {result.returncode}):\n"
            f"{result.stdout[-3000:]}\n{result.stderr[-1000:]}"
        )


def build_fixture_warehouse(
    root: Path,
    runs: list[FixtureRun] | None = None,
    *,
    session_timezone: str | None = None,
    dbt_vars: dict[str, object] | None = None,
) -> Path:
    """Prepares fixture root and executes dbt build."""
    prepare_fixture_root(root, runs=runs, session_timezone=session_timezone)
    build_dbt_fixture(root, dbt_vars=dbt_vars)
    return root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.fixture_warehouse",
        description="Prepare or build a fixture warehouse for testing and CI.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Target root directory for the fixture warehouse",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Prepare directory structure, bronze, silver, and config without running dbt",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    target_root: Path = args.root.resolve()

    if target_root.exists() and any(target_root.iterdir()):
        sys.stderr.write(
            f"Error: Refusing to overwrite nonempty destination directory: {target_root}\n"
        )
        return 1

    try:
        if args.prepare_only:
            prepare_fixture_root(target_root)
            print(f"Fixture warehouse prepared at {target_root} (prepare-only)")
        else:
            build_fixture_warehouse(target_root)
            print(f"Fixture warehouse built at {target_root}")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Error preparing fixture warehouse: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
