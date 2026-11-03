import dataclasses
import json
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq
import pytest

from src.config import ApiConfig, IngestionConfig, PathsConfig, ProjectConfig
from src.ingest.snapshot_manifest import IngestionManifest, write_manifest
from src.profiles import get_registry
from src.quality.profiling import profile_run
from src.transform import build_silver_entities
from src.transform.build_silver_entities import (
    BronzeRebuildMismatch,
    _already_transformed,
    build_silver_for_run,
)
from src.transform.export_parquet import ENTITY_COLUMNS
from src.transform.silver_stats import stats_path
from src.utils.dates import utc_now


def make_config(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(
        api=ApiConfig(base_url="https://ctg.test/api/v2"),
        paths=PathsConfig(
            bronze_api_responses=tmp_path / "bronze" / "api_responses",
            bronze_manifests=tmp_path / "bronze" / "manifests",
            silver=tmp_path / "silver",
            gold=tmp_path / "gold",
            duckdb=tmp_path / "warehouse" / "warehouse.duckdb",
            quarantine=tmp_path / "quarantine",
        ),
        ingestion=IngestionConfig(),
    )


def make_manifest(run_id: str, record_count: int) -> IngestionManifest:
    return IngestionManifest(
        ingestion_run_id=run_id,
        query_hash="abc123",
        endpoint="https://ctg.test/api/v2/studies",
        params={},
        status="success",
        started_at_utc=utc_now(),
        ended_at_utc=utc_now(),
        record_count=record_count,
    )


def write_bronze_page(run_dir: Path, page: int, studies: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"page={page:05d}.json").write_text(json.dumps({"studies": studies}))


def make_study(nct_id: str) -> dict:
    return {"protocolSection": {"identificationModule": {"nctId": nct_id}}}


def test_build_silver_dedups_across_flush_boundary(tmp_path: Path, monkeypatch):
    cfg = make_config(tmp_path)
    run_id = "r_dedup"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, [make_study("NCT00000001"), make_study("NCT00000002")])
    write_bronze_page(run_dir, 2, [make_study("NCT00000003")])
    write_bronze_page(run_dir, 3, [make_study("NCT00000002"), make_study("NCT00000001")])
    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 2)

    row_counts = build_silver_for_run(make_manifest(run_id, record_count=5), cfg)

    assert row_counts["silver_trials"] == 3
    path = cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet"
    assert pd.read_parquet(path)["nct_id"].tolist() == [
        "NCT00000001",
        "NCT00000002",
        "NCT00000003",
    ]
    assert pq_num_row_groups(path) == 2


def test_build_silver_writes_schema_only_files_for_empty_entities(tmp_path: Path):
    cfg = make_config(tmp_path)
    run_id = "r_single"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, [make_study("NCT00000001")])

    row_counts = build_silver_for_run(make_manifest(run_id, record_count=1), cfg)

    assert row_counts["silver_trials"] == 1
    assert row_counts["silver_trial_conditions"] == 0
    for entity in ENTITY_COLUMNS:
        assert (cfg.paths.silver / entity / f"run_id={run_id}.parquet").exists()
    path = cfg.paths.silver / "silver_trial_conditions" / f"run_id={run_id}.parquet"
    con = duckdb.connect(":memory:")
    result = con.execute(f"select * from read_parquet('{path.as_posix()}')").fetchall()
    assert result == []
    assert [d[0] for d in con.description] == ENTITY_COLUMNS["silver_trial_conditions"]


def test_build_silver_skips_records_without_nct_id(tmp_path: Path):
    cfg = make_config(tmp_path)
    run_id = "r_skip"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, [make_study("NCT00000001"), {"protocolSection": {}}])

    row_counts = build_silver_for_run(make_manifest(run_id, record_count=2), cfg)

    assert row_counts["silver_trials"] == 1
    path = cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet"
    assert pd.read_parquet(path)["nct_id"].tolist() == ["NCT00000001"]


def test_build_silver_publishes_nothing_when_a_study_raises(tmp_path: Path, monkeypatch):
    cfg = make_config(tmp_path)
    run_id = "r_fail"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, [make_study(f"NCT{i:08d}") for i in range(1, 6)])
    # Flush on every row so a tmp file definitely exists before the failure.
    monkeypatch.setattr(build_silver_entities, "FLUSH_ROWS", 1)

    real_flatten = build_silver_entities.flatten_study

    def explode_on_third(study, *args, **kwargs):
        rows = real_flatten(study, *args, **kwargs)
        if rows["silver_trials"][0]["nct_id"] == "NCT00000003":
            raise RuntimeError("flatten blew up")
        return rows

    monkeypatch.setattr(build_silver_entities, "flatten_study", explode_on_third)
    with pytest.raises(RuntimeError, match="flatten blew up"):
        build_silver_for_run(make_manifest(run_id, record_count=5), cfg)

    assert list(cfg.paths.silver.glob(f"*/run_id={run_id}.parquet")) == []
    assert list(cfg.paths.silver.glob("*/run_id=*.parquet.tmp")) == []
    assert not _already_transformed(cfg, run_id)

    monkeypatch.setattr(build_silver_entities, "flatten_study", real_flatten)
    counts = build_silver_for_run(make_manifest(run_id, record_count=5), cfg)
    assert counts["silver_trials"] == 5
    assert _already_transformed(cfg, run_id)


def test_build_silver_refuses_ingest_only_profile(tmp_path: Path):
    """An ingest_only profile must never be transformed into the shared silver tree."""
    cfg = make_config(tmp_path)
    run_id = "r_ingest_only"
    study = make_study("NCT00000001")
    write_bronze_page(cfg.paths.bronze_api_responses / f"run_id={run_id}", 1, [study])

    full_catalog = get_registry().get("full_catalog")
    assert full_catalog.ingest_only
    assert full_catalog.taxonomy is None

    with pytest.raises(ValueError, match="is ingest_only"):
        build_silver_for_run(make_manifest(run_id, record_count=1), cfg, profile=full_catalog)
    assert list(cfg.paths.silver.glob("**/*.parquet")) == []


def test_build_silver_allows_a_named_profile_with_no_taxonomy_when_not_ingest_only(tmp_path: Path):
    """A non-ingest-only profile without a taxonomy overlay transforms cleanly (Issue #52).

    Condition text rows fall back to unmapped (mapping_source text_fallback)
    while the registry's MeSH browse module carries condition identity.
    """
    cfg = make_config(tmp_path)
    run_id = "r_no_taxonomy"
    study = make_study("NCT00000001")
    study["protocolSection"]["conditionsModule"] = {"conditions": ["Some Condition"]}
    write_bronze_page(cfg.paths.bronze_api_responses / f"run_id={run_id}", 1, [study])

    full_catalog = get_registry().get("full_catalog")
    assert full_catalog.taxonomy is None
    # Simulate a profile that is NOT ingest_only and has no taxonomy
    non_ingest_profile = dataclasses.replace(full_catalog, ingest_only=False)

    row_counts = build_silver_for_run(
        make_manifest(run_id, record_count=1), cfg, profile=non_ingest_profile
    )
    assert row_counts["silver_trials"] == 1
    assert row_counts["silver_trial_conditions"] == 1

    conditions = pd.read_parquet(
        cfg.paths.silver / "silver_trial_conditions" / f"run_id={run_id}.parquet"
    )
    assert conditions["mapping_source"].tolist() == ["text_fallback"]
    assert conditions["condition_group"].tolist() == ["unmapped"]
    assert conditions["condition_normalized"].tolist() == ["some condition"]
    assert list(cfg.paths.silver.glob("**/*.parquet.tmp")) == []


def test_run_transform_skips_a_retained_run_whose_bronze_was_pruned(tmp_path: Path):
    """A pipeline upgrade must not fail on retained pre-feature silver (Finding 1).

    Adding silver_trial_condition_mesh to ENTITY_NAMES marks every pre-upgrade run as
    not-yet-transformed, but a run whose bronze was pruned (bronze_runs_to_keep: 1)
    can never be rebuilt: run_transform must skip it and keep its silver
    exactly as written instead of raising FileNotFoundError.
    """
    cfg = make_config(tmp_path)
    run_id = "r_upgraded_volume"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, [make_study("NCT00000001")])
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(cfg.paths.bronze_manifests, make_manifest(run_id, record_count=1))
    build_silver_for_run(make_manifest(run_id, record_count=1), cfg)

    # Rewind to pre-upgrade shape: delete the new silver_trial_condition_mesh file and prune bronze
    trials_path = cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet"
    (cfg.paths.silver / "silver_trial_condition_mesh" / f"run_id={run_id}.parquet").unlink()
    shutil.rmtree(run_dir)
    before = pd.read_parquet(trials_path)

    # run_transform must safely skip the pruned run rather than raising FileNotFoundError
    assert build_silver_entities.run_transform(config=cfg) == []
    # --force also skips when bronze is pruned and legacy silver is intact
    assert build_silver_entities.run_transform(config=cfg, force=True) == []

    after = pd.read_parquet(trials_path)
    assert list(after.columns) == list(before.columns)
    assert after["nct_id"].tolist() == before["nct_id"].tolist()


def test_run_transform_fails_on_partial_silver_loss_when_bronze_pruned(tmp_path: Path):
    """When bronze is pruned and silver is partially missing, run_transform fails (Finding 1)."""
    cfg = make_config(tmp_path)
    run_id = "r_partial_loss"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, [make_study("NCT00000001")])
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(cfg.paths.bronze_manifests, make_manifest(run_id, record_count=1))
    build_silver_for_run(make_manifest(run_id, record_count=1), cfg)

    # Prune bronze and delete one silver entity (silver_trial_conditions)
    shutil.rmtree(run_dir)
    (cfg.paths.silver / "silver_trial_conditions" / f"run_id={run_id}.parquet").unlink()

    # Plain and forced must both raise FileNotFoundError rather than silently skipping
    with pytest.raises(FileNotFoundError, match=f"Bronze pages missing for run {run_id}"):
        build_silver_entities.run_transform(config=cfg)
    with pytest.raises(FileNotFoundError, match=f"Bronze pages missing for run {run_id}"):
        build_silver_entities.run_transform(config=cfg, force=True)


def test_run_transform_fails_on_total_silver_loss_when_bronze_pruned(tmp_path: Path):
    """When bronze is pruned and silver is totally missing, run_transform fails (Finding 1)."""
    cfg = make_config(tmp_path)
    run_id = "r_total_loss"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, [make_study("NCT00000001")])
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(cfg.paths.bronze_manifests, make_manifest(run_id, record_count=1))
    build_silver_for_run(make_manifest(run_id, record_count=1), cfg)

    # Prune bronze and delete all silver entity files
    shutil.rmtree(run_dir)
    for entity in build_silver_entities.ENTITY_NAMES:
        path = cfg.paths.silver / entity / f"run_id={run_id}.parquet"
        if path.exists():
            path.unlink()

    with pytest.raises(FileNotFoundError, match=f"Bronze pages missing for run {run_id}"):
        build_silver_entities.run_transform(config=cfg)
    with pytest.raises(FileNotFoundError, match=f"Bronze pages missing for run {run_id}"):
        build_silver_entities.run_transform(config=cfg, force=True)


def test_run_transform_fails_on_mixed_runs_when_one_lacks_silver(tmp_path: Path):
    """Mixed runs: an intact legacy run skips; a second run with missing silver fails."""
    cfg = make_config(tmp_path)
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)

    # Run 1: legacy intact (bronze pruned, mesh file deleted, 6 pre-upgrade entities intact)
    r1 = "r_intact"
    d1 = cfg.paths.bronze_api_responses / f"run_id={r1}"
    write_bronze_page(d1, 1, [make_study("NCT00000001")])
    write_manifest(cfg.paths.bronze_manifests, make_manifest(r1, record_count=1))
    build_silver_for_run(make_manifest(r1, record_count=1), cfg)
    (cfg.paths.silver / "silver_trial_condition_mesh" / f"run_id={r1}.parquet").unlink()
    shutil.rmtree(d1)

    # Run 2: partial loss (bronze pruned, silver_trial_conditions deleted)
    r2 = "r_corrupt"
    d2 = cfg.paths.bronze_api_responses / f"run_id={r2}"
    write_bronze_page(d2, 1, [make_study("NCT00000002")])
    write_manifest(cfg.paths.bronze_manifests, make_manifest(r2, record_count=1))
    build_silver_for_run(make_manifest(r2, record_count=1), cfg)
    (cfg.paths.silver / "silver_trial_conditions" / f"run_id={r2}.parquet").unlink()
    shutil.rmtree(d2)

    # run_transform must raise for r2 rather than silently skipping both
    with pytest.raises(FileNotFoundError, match=f"Bronze pages missing for run {r2}"):
        build_silver_entities.run_transform(config=cfg)


def _rewind_to_pre_upgrade_shape(cfg: ProjectConfig, run_id: str) -> None:
    """Drop the mesh entity file so the run looks pre-upgrade (implicit upgrade path)."""
    (cfg.paths.silver / "silver_trial_condition_mesh" / f"run_id={run_id}.parquet").unlink()


def _retained_silver_bytes(cfg: ProjectConfig, run_id: str) -> dict[Path, bytes]:
    return {
        path: path.read_bytes()
        for path in sorted(cfg.paths.silver.glob(f"*/run_id={run_id}.parquet"))
    }


@pytest.mark.parametrize("force", [False, True])
def test_implicit_upgrade_refuses_a_missing_bronze_page(tmp_path: Path, force: bool):
    """Round-4 finding 1: incomplete bronze must never overwrite retained silver.

    A legacy run whose bronze lost a page rebuilds fewer studies than the
    manifest recorded; replacing the retained silver with that partial rebuild
    would destroy the only surviving copy of the run. The transform must abort
    before close() swaps any file in, both plain and with --force.
    """
    cfg = make_config(tmp_path)
    run_id = "r_upgrade_missing_page"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(
        run_dir,
        1,
        [make_study("NCT00000001"), make_study("NCT00000002"), make_study("NCT00000003")],
    )
    write_bronze_page(run_dir, 2, [make_study("NCT00000004")])
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(cfg.paths.bronze_manifests, make_manifest(run_id, record_count=4))
    build_silver_for_run(make_manifest(run_id, record_count=4), cfg)
    _rewind_to_pre_upgrade_shape(cfg, run_id)

    (run_dir / "page=00002.json").unlink()  # directory remains, one page gone

    before = _retained_silver_bytes(cfg, run_id)
    assert len(before) == 6
    # Round-6 finding 2: the whole abort sentence is pinned, not a fragment.
    expected_message = (
        f"Run {run_id}: the bronze rebuild produced 3 studies "
        "(4 records, 0 repeated NCT IDs, 0 without an NCT ID), but the manifest "
        "expects 4 studies, the retained silver_trials file holds 4 trials and the "
        "retained transform stats expect 4 trials; nothing was written. Restore this "
        "run's bronze pages (from a backup or a re-fetch of the same query written "
        "back under this run_id) and re-run the transform. Removing this run's bronze "
        "directory is irreversible and only appropriate when the lost studies cannot "
        "be restored and every retained silver entity file for the run is present; if "
        "a retained entity is also missing, the run can never be rebuilt and the "
        "retained silver is the only surviving copy."
    )
    with pytest.raises(BronzeRebuildMismatch) as excinfo:
        build_silver_entities.run_transform(config=cfg, force=force)
    assert str(excinfo.value) == expected_message
    assert _retained_silver_bytes(cfg, run_id) == before
    assert list(cfg.paths.silver.glob("*/run_id=*.parquet.tmp")) == []


@pytest.mark.parametrize("force", [False, True])
def test_implicit_upgrade_refuses_a_truncated_bronze_page(tmp_path: Path, force: bool):
    """Round-4 finding 1: a truncated page is the same data-loss path as a lost one."""
    cfg = make_config(tmp_path)
    run_id = "r_upgrade_truncated"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(
        run_dir,
        1,
        [make_study("NCT00000001"), make_study("NCT00000002"), make_study("NCT00000003")],
    )
    write_bronze_page(run_dir, 2, [make_study("NCT00000004")])
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(cfg.paths.bronze_manifests, make_manifest(run_id, record_count=4))
    build_silver_for_run(make_manifest(run_id, record_count=4), cfg)
    _rewind_to_pre_upgrade_shape(cfg, run_id)

    (run_dir / "page=00001.json").write_text(json.dumps({"studies": [make_study("NCT00000001")]}))

    before = _retained_silver_bytes(cfg, run_id)
    with pytest.raises(BronzeRebuildMismatch, match="the bronze rebuild produced 2 studies"):
        build_silver_entities.run_transform(config=cfg, force=force)
    assert _retained_silver_bytes(cfg, run_id) == before
    assert list(cfg.paths.silver.glob("*/run_id=*.parquet.tmp")) == []


def test_implicit_upgrade_completes_when_bronze_reproduces_the_manifest(tmp_path: Path):
    """Positive arm of the count guard: a complete bronze upgrades retained silver.

    Duplicates and missing-NCT records are legitimate exclusions the guard
    subtracts on both sides, so a complete rebuild (4 manifest records − 1
    duplicate − 1 no-NCT = 2 studies) proceeds and gains the mesh entity.
    """
    cfg = make_config(tmp_path)
    run_id = "r_upgrade_ok"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(
        run_dir,
        1,
        [
            make_study("NCT00000001"),
            make_study("NCT00000002"),
            {"protocolSection": {}},
        ],
    )
    write_bronze_page(run_dir, 2, [make_study("NCT00000001")])  # duplicate NCT ID
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(cfg.paths.bronze_manifests, make_manifest(run_id, record_count=4))
    build_silver_for_run(make_manifest(run_id, record_count=4), cfg)
    assert pd.read_parquet(cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet")[
        "nct_id"
    ].tolist() == ["NCT00000001", "NCT00000002"]
    _rewind_to_pre_upgrade_shape(cfg, run_id)

    assert build_silver_entities.run_transform(config=cfg) == [run_id]

    mesh_path = cfg.paths.silver / "silver_trial_condition_mesh" / f"run_id={run_id}.parquet"
    assert mesh_path.exists()
    assert _already_transformed(cfg, run_id)
    assert list(cfg.paths.silver.glob("*/run_id=*.parquet.tmp")) == []


def test_incomplete_bronze_without_retained_silver_still_transforms(tmp_path: Path):
    """The count guard protects retained silver only.

    A fresh (never-transformed) run transforms whatever bronze holds; the
    pre-existing post-close warning covers its shortfall, and explicitly
    requested partial-run transforms keep working.
    """
    cfg = make_config(tmp_path)
    run_id = "r_fresh_truncated"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, [make_study("NCT00000001")])

    row_counts = build_silver_for_run(make_manifest(run_id, record_count=4), cfg)

    assert row_counts["silver_trials"] == 1
    assert (cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet").exists()


def test_run_transform_fails_on_mixed_runs_when_one_bronze_is_truncated(tmp_path: Path):
    """Mixed runs: a pruned-bronze legacy run skips; a truncated-bronze run aborts."""
    cfg = make_config(tmp_path)
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)

    # Run 1: legacy intact, bronze pruned — the safe skip from round 3.
    r1 = "r_pruned_intact"
    d1 = cfg.paths.bronze_api_responses / f"run_id={r1}"
    write_bronze_page(d1, 1, [make_study("NCT00000001")])
    write_manifest(cfg.paths.bronze_manifests, make_manifest(r1, record_count=1))
    build_silver_for_run(make_manifest(r1, record_count=1), cfg)
    _rewind_to_pre_upgrade_shape(cfg, r1)
    shutil.rmtree(d1)

    # Run 2: legacy shape kept, but its bronze lost a page.
    r2 = "r_truncated"
    d2 = cfg.paths.bronze_api_responses / f"run_id={r2}"
    write_bronze_page(d2, 1, [make_study("NCT00000002"), make_study("NCT00000003")])
    write_manifest(cfg.paths.bronze_manifests, make_manifest(r2, record_count=2))
    build_silver_for_run(make_manifest(r2, record_count=2), cfg)
    _rewind_to_pre_upgrade_shape(cfg, r2)
    (d2 / "page=00001.json").write_text(json.dumps({"studies": [make_study("NCT00000002")]}))

    with pytest.raises(BronzeRebuildMismatch, match=f"Run {r2}"):
        build_silver_entities.run_transform(config=cfg)


def _ten_trial_run(cfg: ProjectConfig, run_id: str) -> Path:
    """A legacy-shaped run with two 5-study pages, silver built once, mesh removed."""
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, [make_study(f"NCT{i:08d}") for i in range(1, 6)])
    write_bronze_page(run_dir, 2, [make_study(f"NCT{i:08d}") for i in range(6, 11)])
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(cfg.paths.bronze_manifests, make_manifest(run_id, record_count=10))
    row_counts = build_silver_for_run(make_manifest(run_id, record_count=10), cfg)
    assert row_counts["silver_trials"] == 10
    _rewind_to_pre_upgrade_shape(cfg, run_id)
    return run_dir


def _retained_trials_rows(cfg: ProjectConfig, run_id: str) -> int:
    path = cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet"
    return pq.ParquetFile(path).metadata.num_rows


@pytest.mark.parametrize("force", [False, True])
def test_implicit_upgrade_refuses_a_page_replaced_by_a_copy_of_another(tmp_path: Path, force: bool):
    """Round-5 finding 1: equal raw record counts hide lost studies.

    Page 2 overwritten with a copy of page 1 keeps the manifest's record count
    (10 raw records read) and burns the difference on dedup, so the manifest
    check alone passes (5 == 10 − 5 duplicates). The rebuild must also be
    compared against what is retained: 5 rebuilt studies may not replace 10
    retained trials.
    """
    cfg = make_config(tmp_path)
    run_id = "r_upgrade_copied_page"
    run_dir = _ten_trial_run(cfg, run_id)

    shutil.copy(run_dir / "page=00001.json", run_dir / "page=00002.json")

    before = _retained_silver_bytes(cfg, run_id)
    # Round-6 finding 2: the whole abort sentence is pinned, not a fragment —
    # it must state the rebuilt count, both exclusions, and the retained counts.
    expected_message = (
        f"Run {run_id}: the bronze rebuild produced 5 studies "
        "(10 records, 5 repeated NCT IDs, 0 without an NCT ID), but the retained "
        "silver_trials file holds 10 trials and the retained transform stats expect "
        "10 trials; nothing was written. No bronze page is missing: the bronze "
        "reproduces its manifest but holds fewer studies than the retained silver, "
        "so the retained silver is the only copy of the studies the bronze can no "
        "longer produce. Restore the original page content (re-fetch the same query) "
        "and re-run the transform, or keep the retained silver as written. Removing "
        "this run's bronze directory is irreversible and only appropriate when the "
        "lost studies cannot be restored and every retained silver entity file for "
        "the run is present; if a retained entity is also missing, the run can never "
        "be rebuilt and the retained silver is the only surviving copy."
    )
    with pytest.raises(BronzeRebuildMismatch) as excinfo:
        build_silver_entities.run_transform(config=cfg, force=force)
    assert str(excinfo.value) == expected_message
    assert stats_path(cfg, run_id).exists()  # retained stats survive a guard abort
    assert _retained_silver_bytes(cfg, run_id) == before
    assert _retained_trials_rows(cfg, run_id) == 10
    assert list(cfg.paths.silver.glob("*/run_id=*.parquet.tmp")) == []


@pytest.mark.parametrize("force", [False, True])
def test_implicit_upgrade_refuses_a_page_whose_studies_lose_their_nct_id(
    tmp_path: Path, force: bool
):
    """Round-5 finding 1: studies replaced by NCT-less records hide behind exclusions too.

    Page 2's five studies lose their NCT IDs: all 10 raw records are read and
    the five losses count as legitimate missing-NCT exclusions, so 5 == the
    manifest's expected 10 − 0 − 5. With the run's stats removed, the retained
    silver_trials row count alone must abort the upgrade.
    """
    cfg = make_config(tmp_path)
    run_id = "r_upgrade_nctless_page"
    run_dir = _ten_trial_run(cfg, run_id)

    write_bronze_page(run_dir, 2, [{"protocolSection": {}} for _ in range(5)])
    stats_path(cfg, run_id).unlink()

    before = _retained_silver_bytes(cfg, run_id)
    with pytest.raises(
        BronzeRebuildMismatch, match="the retained silver_trials file holds 10 trials"
    ):
        build_silver_entities.run_transform(config=cfg, force=force)
    assert _retained_silver_bytes(cfg, run_id) == before
    assert _retained_trials_rows(cfg, run_id) == 10
    assert list(cfg.paths.silver.glob("*/run_id=*.parquet.tmp")) == []


def test_interrupted_close_marks_the_run_for_a_full_rebuild(tmp_path: Path, monkeypatch):
    """Round-5 finding 4: a swap failure mid-close() must not strand a mixed run.

    close() replaces final files one entity at a time; an I/O failure there can
    leave some entities rebuilt and others legacy. The run's stats are removed
    before the first swap, so _already_transformed is false on that path and the
    next plain transform rebuilds the run whole from the (already
    count-verified) bronze.
    """
    import src.transform.export_parquet as export_parquet_module

    cfg = make_config(tmp_path)
    run_id = "r_upgrade_swap_failure"
    _ten_trial_run(cfg, run_id)

    real_replace = export_parquet_module.os.replace

    def fail_on_interventions(src, dst):
        if "silver_trial_interventions" in str(dst):
            raise OSError("disk died mid-swap")
        return real_replace(src, dst)

    monkeypatch.setattr(export_parquet_module.os, "replace", fail_on_interventions)
    with pytest.raises(OSError, match="disk died mid-swap"):
        build_silver_entities.run_transform(config=cfg)
    assert not _already_transformed(cfg, run_id)
    assert not stats_path(cfg, run_id).exists()

    monkeypatch.undo()
    assert build_silver_entities.run_transform(config=cfg) == [run_id]
    assert _already_transformed(cfg, run_id)
    assert stats_path(cfg, run_id).exists()
    assert (cfg.paths.silver / "silver_trial_condition_mesh" / f"run_id={run_id}.parquet").exists()
    assert _retained_trials_rows(cfg, run_id) == 10
    assert list(cfg.paths.silver.glob("*/run_id=*.parquet.tmp")) == []


KILL_MID_SWAP_SCRIPT = """\
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[3])

from src.config import ApiConfig, IngestionConfig, PathsConfig, ProjectConfig
from src.transform import build_silver_entities

cfg = ProjectConfig(
    api=ApiConfig(base_url="https://ctg.test/api/v2"),
    paths=PathsConfig(
        bronze_api_responses=Path(sys.argv[1]) / "bronze" / "api_responses",
        bronze_manifests=Path(sys.argv[1]) / "bronze" / "manifests",
        silver=Path(sys.argv[1]) / "silver",
        gold=Path(sys.argv[1]) / "gold",
        duckdb=Path(sys.argv[1]) / "warehouse" / "warehouse.duckdb",
        quarantine=Path(sys.argv[1]) / "quarantine",
    ),
    ingestion=IngestionConfig(),
)

swaps = 0
real_replace = os.replace


def kill_before_nth_swap(src, dst):
    global swaps
    swaps += 1
    if swaps == int(sys.argv[2]):
        os.kill(os.getpid(), signal.SIGKILL)
    return real_replace(src, dst)


os.replace = kill_before_nth_swap
build_silver_entities.run_transform(config=cfg)
"""


@pytest.mark.parametrize("kill_before_swap", [4, 6])
def test_a_killed_process_mid_swap_is_rebuilt_whole_on_the_next_plain_run(
    tmp_path: Path, kill_before_swap: int
):
    """Round-6 finding 1: SIGKILL during close() must not strand a mixed run.

    A killed process cannot run the except-path cleanup, so the stats removal
    must happen before the first swap. The subprocess kills itself from a
    patched os.replace before the Nth entity swap, leaving a mixed tree with no
    stats; a plain rerun then rebuilds the run whole with 10 trials.
    """
    cfg = make_config(tmp_path)
    run_id = "r_upgrade_killed"
    _ten_trial_run(cfg, run_id)

    repo_root = Path(__file__).resolve().parents[1]
    script = tmp_path / "kill_mid_swap.py"
    script.write_text(KILL_MID_SWAP_SCRIPT)
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            str(tmp_path),
            str(kill_before_swap),
            str(repo_root),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == -9, proc.stderr
    assert not stats_path(cfg, run_id).exists()
    assert not _already_transformed(cfg, run_id)
    # Swaps before the kill already replaced earlier entities: the tree is mixed.
    assert (cfg.paths.silver / "silver_trial_condition_mesh" / f"run_id={run_id}.parquet").exists()
    assert _retained_trials_rows(cfg, run_id) == 10

    assert build_silver_entities.run_transform(config=cfg) == [run_id]
    assert _already_transformed(cfg, run_id)
    assert stats_path(cfg, run_id).exists()
    assert (cfg.paths.silver / "silver_trial_condition_mesh" / f"run_id={run_id}.parquet").exists()
    assert _retained_trials_rows(cfg, run_id) == 10
    assert list(cfg.paths.silver.glob("*/run_id=*.parquet.tmp")) == []


@pytest.mark.parametrize("force", [False, True])
def test_implicit_upgrade_aborts_when_no_retained_reference_is_readable(
    tmp_path: Path, force: bool
):
    """Round-6 finding 4: unbounded exclusions must not fail the guard open.

    With the retained silver_trials file unreadable (here: deleted) and no
    transform stats, a rebuild that excluded records could be shrinking
    retained silver for legitimate or illegitimate reasons — indistinguishable.
    The guard must abort instead of replacing retained silver.
    """
    cfg = make_config(tmp_path)
    run_id = "r_upgrade_unreadable_refs"
    run_dir = _ten_trial_run(cfg, run_id)

    shutil.copy(run_dir / "page=00001.json", run_dir / "page=00002.json")
    stats_path(cfg, run_id).unlink()
    (cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet").unlink()

    before = _retained_silver_bytes(cfg, run_id)
    with pytest.raises(
        BronzeRebuildMismatch,
        match=(
            "neither a readable retained silver_trials file nor retained transform "
            "stats bounds its 5 excluded records"
        ),
    ):
        build_silver_entities.run_transform(config=cfg, force=force)
    assert _retained_silver_bytes(cfg, run_id) == before
    assert list(cfg.paths.silver.glob("*/run_id=*.parquet.tmp")) == []


def test_build_silver_still_transforms_a_named_profile_with_a_taxonomy(tmp_path: Path):
    """Positive arm of the guard: a refreshable profile is unaffected."""
    cfg = make_config(tmp_path)
    run_id = "r_named"
    write_bronze_page(cfg.paths.bronze_api_responses / f"run_id={run_id}", 1, [make_study("NCT1")])

    indication_profile = get_registry().get("oncology_nsclc")
    assert indication_profile.taxonomy is not None

    counts = build_silver_for_run(
        make_manifest(run_id, record_count=1), cfg, profile=indication_profile
    )

    assert counts["silver_trials"] == 1
    frame = pd.read_parquet(cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet")
    assert frame["indication_profile_id"].unique().tolist() == ["oncology_nsclc"]


def test_build_silver_legacy_call_without_a_profile_keeps_the_global_taxonomy(tmp_path: Path):
    """Pin against over-tightening: `profile=None` callers predate the registry."""
    cfg = make_config(tmp_path)
    run_id = "r_legacy"
    write_bronze_page(cfg.paths.bronze_api_responses / f"run_id={run_id}", 1, [make_study("NCT1")])

    counts = build_silver_for_run(make_manifest(run_id, record_count=1), cfg)

    assert counts["silver_trials"] == 1
    frame = pd.read_parquet(cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet")
    assert frame["indication_profile_id"].unique().tolist() == ["adrd"]


def test_profile_run_treats_dedup_shortfall_as_expected(tmp_path: Path, monkeypatch):
    cfg = make_config(tmp_path)
    run_id = "r_recon"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, [make_study("NCT00000001"), make_study("NCT00000002")])
    write_bronze_page(run_dir, 2, [make_study("NCT00000002")])
    monkeypatch.setattr(build_silver_entities, "FLUSH_ROWS", 1)
    build_silver_for_run(make_manifest(run_id, record_count=3), cfg)

    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(cfg.paths.bronze_manifests, make_manifest(run_id, record_count=3))
    report = profile_run(run_id, config=cfg)

    recon = report["reconciliation"]
    assert recon["manifest_found"] is True
    assert recon["manifest_record_count"] == 3
    assert recon["silver_trials_row_count"] == 2
    assert recon["excluded_records"] == 1
    assert recon["expected_silver_rows"] == 2
    assert recon["rows_match_expectation"] is True
    assert recon["nct_ids_unique"] is True

    stats = json.loads(stats_path(cfg, run_id).read_text(encoding="utf-8"))
    assert stats["duplicate_nct_ids_dropped"] == 1
    assert stats["records_without_nct_id"] == 0
    assert stats["row_counts"]["silver_trials"] == 2


def test_profile_run_records_an_absent_manifest_instead_of_nothing_to_do(tmp_path: Path):
    """A run missing from the manifest directory must not read as reconcilable-clean.

    `report` was seeded with an empty `reconciliation` dict, so a profile whose
    manifest lives in another profile's tree — exactly what `orchestrate`
    produced before it passed the profile's config — wrote a block indistinguishable
    from "nothing to reconcile". The marker is the difference between "checked and
    empty" and "could not check".
    """
    cfg = make_config(tmp_path)
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(cfg.paths.bronze_manifests, make_manifest("r_other_run", record_count=9))

    report = profile_run("r_not_in_manifest", config=cfg)

    assert report["reconciliation"] == {"manifest_found": False}
    written = json.loads(
        (cfg.paths.silver / "_profiles" / "profile_r_not_in_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert written["reconciliation"]["manifest_found"] is False


def pq_num_row_groups(path: Path) -> int:
    return pq.ParquetFile(path).metadata.num_row_groups


@pytest.mark.parametrize("with_metadata", [True, False])
def test_build_silver_preserves_exact_page_record_provenance(tmp_path, with_metadata):
    import hashlib

    from src.transform.flatten_studies import iter_bronze_studies

    cfg = make_config(tmp_path)
    run_id = "r_provenance"
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    first = make_study("NCT00000001")
    second = make_study("NCT00000002")
    write_bronze_page(run_dir, 1, [first, second])
    # Duplicate ID in another page must keep its original page/index.
    write_bronze_page(run_dir, 2, [second])
    if with_metadata:
        metadata_dir = run_dir / "_page_metadata"
        metadata_dir.mkdir()
        (metadata_dir / "page=00001.json").write_text(
            json.dumps({"retrieved_at_utc": "2026-10-02T10:01:00+00:00"})
        )
    assert list(iter_bronze_studies(run_dir)) == [first, second, second]
    build_silver_for_run(make_manifest(run_id, 3), cfg)
    rows = (
        pq.ParquetFile(cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet")
        .read()
        .to_pylist()
    )
    assert [row["raw_study_ordinal"] for row in rows] == [0, 1]
    for row, study in zip(rows, [first, second], strict=True):
        assert row["snapshot_id"] == run_id
        assert row["raw_page_reference"] == "run_id=r_provenance/page=00001.json"
        assert row["retrieved_at_utc"] == ("2026-10-02T10:01:00+00:00" if with_metadata else None)
        expected_hash = hashlib.sha256(
            json.dumps(study, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        assert row["source_json_hash"] == expected_hash
        original_page = json.loads(
            (cfg.paths.bronze_api_responses / row["raw_page_reference"]).read_text()
        )
        assert original_page["studies"][row["raw_study_ordinal"]] == study
