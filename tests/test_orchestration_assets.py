import duckdb
import pandas as pd
from dagster import materialize

from src.ingest.snapshot_manifest import IngestionManifest, write_manifest
from src.orchestration.assets.bronze import IngestParams, ctg_raw_pages
from src.orchestration.assets.silver import silver_entities
from src.orchestration.checks import bronze_silver_reconciliation, manifest_integrity
from src.utils.dates import utc_now
from tests.conftest import check_evaluation, materialize_with_checks


def _success_manifest() -> IngestionManifest:
    return IngestionManifest(
        ingestion_run_id="20260904T120000Z_abc12345",
        query_hash="hash123",
        endpoint="https://clinicaltrials.gov/api/v2/studies",
        params={"query.cond": "Alzheimer Disease"},
        status="success",
        started_at_utc=utc_now(),
        ended_at_utc=utc_now(),
        page_count=2,
        record_count=3,
        total_count_reported=3,
    )


def test_bronze_asset_writes_manifest_and_materializes(project_root_tmp, monkeypatch) -> None:
    manifest = _success_manifest()

    def fake_run_ingestion(condition=None, full_refresh=False, max_pages=None):
        _write_manifest_for_every_profile(manifest)
        return manifest

    monkeypatch.setattr("src.orchestration.assets.bronze.run_ingestion", fake_run_ingestion)
    result = materialize(
        assets=[ctg_raw_pages],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
    )
    assert result.success
    materializations = result.get_asset_materialization_events()
    assert len(materializations) == 1


def test_bronze_asset_raises_on_failed_run(project_root_tmp, monkeypatch) -> None:
    manifest = _success_manifest().model_copy(update={"status": "failed"})

    def fake_run_ingestion(condition=None, full_refresh=False, max_pages=None):
        return manifest

    monkeypatch.setattr("src.orchestration.assets.bronze.run_ingestion", fake_run_ingestion)
    result = materialize(
        assets=[ctg_raw_pages],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
        raise_on_error=False,
    )
    assert not result.success


def test_manifest_integrity_passes_on_success_run(project_root_tmp, monkeypatch) -> None:
    manifest = _success_manifest()

    def fake_run_ingestion(condition=None, full_refresh=False, max_pages=None):
        _write_manifest_for_every_profile(manifest)
        return manifest

    monkeypatch.setattr("src.orchestration.assets.bronze.run_ingestion", fake_run_ingestion)
    result = materialize_with_checks(
        assets=[ctg_raw_pages],
        asset_checks=[manifest_integrity],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
    )
    evaluation = check_evaluation(result, "manifest_integrity")
    assert evaluation is not None and evaluation.passed


def test_manifest_integrity_fails_when_counts_disagree(project_root_tmp, monkeypatch) -> None:
    manifest = _success_manifest().model_copy(
        update={"record_count": 3, "total_count_reported": 999}
    )

    def fake_run_ingestion(condition=None, full_refresh=False, max_pages=None):
        _write_manifest_for_every_profile(manifest)
        return manifest

    monkeypatch.setattr("src.orchestration.assets.bronze.run_ingestion", fake_run_ingestion)
    result = materialize_with_checks(
        assets=[ctg_raw_pages],
        asset_checks=[manifest_integrity],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
        raise_on_error=False,
    )
    evaluation = check_evaluation(result, "manifest_integrity")
    assert evaluation is not None and not evaluation.passed


def test_manifest_integrity_fails_with_no_success_runs(project_root_tmp, monkeypatch) -> None:
    manifest = _success_manifest()

    def fake_run_ingestion(condition=None, full_refresh=False, max_pages=None):
        return manifest  # returns, but never writes a manifest file

    monkeypatch.setattr("src.orchestration.assets.bronze.run_ingestion", fake_run_ingestion)
    result = materialize_with_checks(
        assets=[ctg_raw_pages],
        asset_checks=[manifest_integrity],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
        raise_on_error=False,
    )
    evaluation = check_evaluation(result, "manifest_integrity")
    assert evaluation is not None and not evaluation.passed


def _patch_quiet_bronze(monkeypatch) -> None:
    """Bronze runs but writes nothing: seeded state fully controls the check."""
    manifest = _success_manifest()

    def fake_run_ingestion(condition=None, full_refresh=False, max_pages=None):
        return manifest

    monkeypatch.setattr("src.orchestration.assets.bronze.run_ingestion", fake_run_ingestion)


def _write_manifest_for_every_profile(manifest: IngestionManifest) -> None:
    """manifest_integrity loops every refreshable profile now, so a test that
    wants the gate green must seed all of them, not just the default tree."""
    from src.profiles import get_registry

    for indication_profile in get_registry().refreshable():
        write_manifest(indication_profile.config.paths.bronze_manifests, manifest)


def _seed_reconcilable_state() -> list[str]:
    """Fabricate one consistent bronze→silver→warehouse chain per refreshable
    profile under the temp root, and return the profile ids seeded.

    The checks loop the registry, so a chain for one profile is a chain that
    fails — on `warehouse_covers_latest_silver` (no `success` manifest for that
    profile) and on A25's `warehouse_profile_has_trials`, never on
    `warehouse_exists`, which is a predicate on the shared warehouse file.
    """
    from src.config import load_config
    from src.ingest.snapshot_manifest import write_manifest
    from src.profiles import get_registry

    profiles = get_registry().refreshable()
    profile_ids = [p.profile_id for p in profiles]
    for index, indication_profile in enumerate(profiles):
        cfg = indication_profile.config
        run_id = f"20260904T120000Z_abc1234{index}"
        write_manifest(
            cfg.paths.bronze_manifests,
            IngestionManifest(
                ingestion_run_id=run_id,
                query_hash="hash123",
                endpoint="https://clinicaltrials.gov/api/v2/studies",
                params={"query.cond": indication_profile.profile_id},
                status="success",
                started_at_utc=utc_now(),
                ended_at_utc=utc_now(),
                page_count=1,
                record_count=1,
                total_count_reported=1,
            ),
        )
        silver_dir = cfg.paths.silver / "silver_trials"
        silver_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"nct_id": f"NCT0000000{index}", "brief_title": "T"}]).to_parquet(
            silver_dir / f"run_id={run_id}.parquet", index=False
        )

    shared_cfg = load_config()
    shared_cfg.paths.duckdb.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(shared_cfg.paths.duckdb))
    con.execute("create schema main_marts")
    con.execute(
        "create table main_marts.dim_trial as from values "
        + ", ".join(f"('NCT0000000{i}', '{pid}')" for i, pid in enumerate(profile_ids))
        + " as t(nct_id, indication_profile_id)"
    )
    con.execute(
        "create table main_marts.fct_trial_snapshot as from values "
        + ", ".join(f"(true, '{pid}')" for pid in profile_ids)
        + " as t(current_record_flag, indication_profile_id)"
    )
    con.execute(
        "create table main_marts.mart_trial_similarity "
        "(indication_profile_id varchar, nct_id_a varchar, nct_id_b varchar)"
    )
    con.close()
    return profile_ids


def test_silver_asset_materializes_processed_runs(project_root_tmp, monkeypatch) -> None:
    _seed_reconcilable_state()
    _patch_quiet_bronze(monkeypatch)

    def fake_run_transform(run_id=None, force=False):
        return ["20260904T120000Z_abc12345"]

    monkeypatch.setattr("src.orchestration.assets.silver.run_transform", fake_run_transform)
    result = materialize(
        assets=[ctg_raw_pages, silver_entities],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
    )
    assert result.success
    assert len(result.get_asset_materialization_events()) == 2


def test_bronze_silver_check_passes_on_consistent_state(project_root_tmp, monkeypatch) -> None:
    _seed_reconcilable_state()
    _patch_quiet_bronze(monkeypatch)

    def fake_run_transform(run_id=None, force=False):
        return ["20260904T120000Z_abc12345"]

    monkeypatch.setattr("src.orchestration.assets.silver.run_transform", fake_run_transform)
    result = materialize_with_checks(
        assets=[ctg_raw_pages, silver_entities],
        asset_checks=[bronze_silver_reconciliation],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
    )
    evaluation = check_evaluation(result, "bronze_silver_reconciliation")
    assert evaluation is not None and evaluation.passed


def test_bronze_silver_check_fails_when_silver_missing(project_root_tmp, monkeypatch) -> None:
    _patch_quiet_bronze(monkeypatch)

    def fake_run_transform(run_id=None, force=False):
        return ["20260904T120000Z_abc12345"]

    monkeypatch.setattr("src.orchestration.assets.silver.run_transform", fake_run_transform)
    result = materialize_with_checks(
        assets=[ctg_raw_pages, silver_entities],
        asset_checks=[bronze_silver_reconciliation],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
        raise_on_error=False,
    )
    evaluation = check_evaluation(result, "bronze_silver_reconciliation")
    assert evaluation is not None and not evaluation.passed


def test_warehouse_checks_pass_on_consistent_state(project_root_tmp) -> None:
    """The post-build gate runs once per refreshable profile, and every check
    must say which profile it is about."""
    from src.profiles import get_registry
    from src.quality.reconciliation import warehouse_checks

    _seed_reconcilable_state()
    checks = [
        c
        for indication_profile in get_registry().refreshable()
        for c in warehouse_checks(indication_profile.config, indication_profile.profile_id)
    ]
    assert checks
    assert all(c.passed for c in checks), [(c.profile_id, c.check) for c in checks if not c.passed]
    assert {c.profile_id for c in checks} == {"adrd", "oncology_nsclc"}
    per_profile_rows = {
        c.profile_id: c.actual for c in checks if c.check == "warehouse_profile_has_trials"
    }
    assert per_profile_rows == {"adrd": 1, "oncology_nsclc": 1}, (
        "each profile's legs must count only its own dim_trial rows: the seed holds "
        "one row per profile, so an unscoped query reports 2 for both — and every "
        "other leg here would still pass on the superset."
    )


def test_warehouse_checks_fail_when_warehouse_missing(project_root_tmp) -> None:
    from src.ingest.snapshot_manifest import write_manifest
    from src.profiles import get_registry
    from src.quality.reconciliation import warehouse_checks

    for indication_profile in get_registry().refreshable():
        write_manifest(
            indication_profile.config.paths.bronze_manifests,
            IngestionManifest(
                ingestion_run_id="20260904T120000Z_abc12345",
                query_hash="hash123",
                endpoint="https://clinicaltrials.gov/api/v2/studies",
                params={"query.cond": indication_profile.profile_id},
                status="success",
                started_at_utc=utc_now(),
                ended_at_utc=utc_now(),
                page_count=1,
                record_count=1,
                total_count_reported=1,
            ),
        )
        checks = warehouse_checks(indication_profile.config, indication_profile.profile_id)
        assert [c.check for c in checks if not c.passed] == ["warehouse_exists"]
        assert {c.profile_id for c in checks} == {indication_profile.profile_id}
