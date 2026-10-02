import json
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
from src.transform.build_silver_entities import _already_transformed, build_silver_for_run
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


def test_build_silver_refuses_a_named_profile_with_no_taxonomy(tmp_path: Path):
    """A named profile with no taxonomy is a caller bug, not a licence to borrow.

    `build_silver_for_run` fell back to the global ADRD singleton whenever the
    profile's taxonomy was falsy, so an ingest_only profile that reached it
    wrote ADRD-shaped claims stamped with *that* profile's
    `indication_profile_id` into the shared silver tree dbt globs — invisible
    to the completeness assertion, which excludes ingest_only profiles. The CLI
    now refuses them upstream, so this raise is the last line rather than the
    first; the real shipped profile is used because it is the one that has no
    taxonomy.
    """
    cfg = make_config(tmp_path)
    run_id = "r_no_taxonomy"
    write_bronze_page(
        cfg.paths.bronze_api_responses / f"run_id={run_id}", 1, [make_study("NCT00000001")]
    )

    full_catalog = get_registry().get("full_catalog")
    assert full_catalog.ingest_only, "the guard is pointless if no shipped profile lacks a taxonomy"
    assert full_catalog.taxonomy is None

    with pytest.raises(ValueError, match="has no condition_taxonomy"):
        build_silver_for_run(make_manifest(run_id, record_count=1), cfg, profile=full_catalog)

    assert list(cfg.paths.silver.glob("**/*.parquet")) == []
    assert list(cfg.paths.silver.glob("**/*.parquet.tmp")) == []


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
