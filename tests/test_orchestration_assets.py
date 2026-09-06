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

    def fake_run_ingestion(full_refresh=False, max_pages=None, config=None):
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

    def fake_run_ingestion(full_refresh=False, max_pages=None, config=None):
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

    def fake_run_ingestion(full_refresh=False, max_pages=None, config=None):
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

    def fake_run_ingestion(full_refresh=False, max_pages=None, config=None):
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

    def fake_run_ingestion(full_refresh=False, max_pages=None, config=None):
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

    def fake_run_ingestion(full_refresh=False, max_pages=None, config=None):
        return manifest

    monkeypatch.setattr("src.orchestration.assets.bronze.run_ingestion", fake_run_ingestion)


def _write_manifest_for_every_profile(manifest: IngestionManifest) -> None:
    """manifest_integrity loops every refreshable profile now, so a test that
    wants the gate green must seed all of them, not just the default tree.

    Each tree gets its own run id and counts, not the same payload stamped
    twice: identical manifests made tree selection unobservable by
    construction — "loop both trees" and "read one tree twice" report the
    same details either way, which is how the reviewer's M3 probe stayed
    green over the whole suite (task-13-review-quality.md §2B).
    """
    from src.profiles import get_registry

    for index, indication_profile in enumerate(get_registry().refreshable()):
        per_profile = manifest.model_copy(
            update={
                "ingestion_run_id": f"{manifest.ingestion_run_id[:-1]}{index}",
                "page_count": manifest.page_count + index,
                "record_count": manifest.record_count + index,
                "total_count_reported": manifest.total_count_reported + index,
            }
        )
        write_manifest(indication_profile.config.paths.bronze_manifests, per_profile)


def _seed_reconcilable_state() -> dict[str, str]:
    """Fabricate one consistent bronze→silver→warehouse chain per refreshable
    profile under the temp root, and return {profile_id: ingestion_run_id}.

    The checks loop the registry, so a chain for one profile is a chain that
    fails — on `warehouse_covers_latest_silver` (no `success` manifest for that
    profile) and on A25's `warehouse_profile_has_trials`, never on
    `warehouse_exists`, which is a predicate on the shared warehouse file.

    Each profile is seeded with a *different* trial count (2 + index) and its
    own NCT ids, and the similarity mart gets one pair per profile. Identical
    per-profile state made tree selection unobservable by construction — the
    reviewer's M2+M3 mutants collapsed both blocking gates onto one tree and
    the whole suite stayed green (task-13-review-quality.md §2A/§2B). With
    asymmetric seeds, "read one tree twice" produces the same wrong number for
    both profiles, and the tests below pin each profile's own counts.
    """
    from src.config import load_config
    from src.ingest.snapshot_manifest import write_manifest
    from src.profiles import get_registry

    profiles = get_registry().refreshable()
    run_ids: dict[str, str] = {}
    dim_values: list[str] = []
    flag_values: list[str] = []
    sim_values: list[str] = []
    for index, indication_profile in enumerate(profiles):
        cfg = indication_profile.config
        run_id = f"20260904T120000Z_abc1234{index}"
        run_ids[indication_profile.profile_id] = run_id
        n_trials = 2 + index
        nct_ids = [f"NCT000000{index}{j}" for j in range(n_trials)]
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
                page_count=1 + index,
                record_count=n_trials,
                total_count_reported=n_trials,
            ),
        )
        silver_dir = cfg.paths.silver / "silver_trials"
        silver_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"nct_id": nct, "brief_title": "T"} for nct in nct_ids]).to_parquet(
            silver_dir / f"run_id={run_id}.parquet", index=False
        )
        pid = indication_profile.profile_id
        dim_values += [f"('{nct}', '{pid}')" for nct in nct_ids]
        flag_values += [f"(true, '{pid}')" for _ in nct_ids]
        sim_values.append(f"('{pid}', '{nct_ids[0]}', '{nct_ids[1]}')")

    shared_cfg = load_config()
    shared_cfg.paths.duckdb.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(shared_cfg.paths.duckdb))
    con.execute("create schema main_marts")
    con.execute(
        "create table main_marts.dim_trial as from values "
        + ", ".join(dim_values)
        + " as t(nct_id, indication_profile_id)"
    )
    con.execute(
        "create table main_marts.fct_trial_snapshot as from values "
        + ", ".join(flag_values)
        + " as t(current_record_flag, indication_profile_id)"
    )
    con.execute(
        "create table main_marts.mart_trial_similarity "
        "(indication_profile_id varchar, nct_id_a varchar, nct_id_b varchar)"
    )
    con.execute("insert into main_marts.mart_trial_similarity values " + ", ".join(sim_values))
    con.close()
    return run_ids


def test_silver_asset_materializes_processed_runs(project_root_tmp, monkeypatch) -> None:
    _seed_reconcilable_state()
    _patch_quiet_bronze(monkeypatch)

    def fake_run_transform(run_id=None, force=False, profile=None):
        return ["20260904T120000Z_abc12345"]

    monkeypatch.setattr("src.orchestration.assets.silver.run_transform", fake_run_transform)
    result = materialize(
        assets=[ctg_raw_pages, silver_entities],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
    )
    assert result.success
    assert len(result.get_asset_materialization_events()) == 2


def test_bronze_asset_refreshes_every_profile(project_root_tmp, monkeypatch) -> None:
    """One materialization must cover everything `make pipeline` covers. Ingesting
    ADRD only and calling itself a refresh is how a second profile silently ages
    behind a green UI."""
    from src.profiles import get_registry

    manifest = _success_manifest()
    seen: list[str] = []

    def fake_run_ingestion(full_refresh=False, max_pages=None, config=None):
        seen.append(config.profile_id)
        return manifest

    monkeypatch.setattr("src.orchestration.assets.bronze.run_ingestion", fake_run_ingestion)
    result = materialize(
        assets=[ctg_raw_pages],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
    )
    assert result.success
    assert seen == [p.profile_id for p in get_registry().refreshable()]
    # The line above passes when refreshable() is empty; this pin does not, and an
    # empty registry is the failure worth catching in a test about scope.
    assert set(seen) == {"adrd", "oncology_nsclc"}


def test_silver_asset_transforms_every_profile(project_root_tmp, monkeypatch) -> None:
    """`profile=` is how run_transform picks its bronze tree. Called bare it
    re-transforms ADRD and reports success for NSCLC."""
    from src.profiles import get_registry

    _patch_quiet_bronze(monkeypatch)
    seen: list[str] = []

    def fake_run_transform(run_id=None, force=False, profile=None):
        seen.append(profile.profile_id if profile else "<default>")
        return []

    monkeypatch.setattr("src.orchestration.assets.silver.run_transform", fake_run_transform)
    result = materialize(
        assets=[ctg_raw_pages, silver_entities],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
    )
    assert result.success
    assert seen == [p.profile_id for p in get_registry().refreshable()]
    assert "<default>" not in seen


def test_ingest_params_has_no_condition() -> None:
    """Profiles own their condition scope. One free-text `condition` on the job
    config would be applied to every profile in turn — NSCLC queried with an
    Alzheimer's string."""
    from src.orchestration.assets.bronze import IngestParams

    assert "condition" not in IngestParams.model_fields
    assert set(IngestParams.model_fields) == {"full_refresh", "max_pages"}


def test_bronze_silver_check_passes_on_consistent_state(project_root_tmp, monkeypatch) -> None:
    _seed_reconcilable_state()
    _patch_quiet_bronze(monkeypatch)

    def fake_run_transform(run_id=None, force=False, profile=None):
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

    def fake_run_transform(run_id=None, force=False, profile=None):
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
    assert per_profile_rows == {"adrd": 2, "oncology_nsclc": 3}, (
        "each profile's legs must count only its own dim_trial rows: the seed holds "
        "a *different* count per profile, so an unscoped query reports the total (5) "
        "for both — and every other leg here would still pass on the superset."
    )


def test_manifest_integrity_checks_each_profiles_own_tree(project_root_tmp, monkeypatch) -> None:
    """A blocking publish gate that certified every profile from one bronze
    tree was the reviewer's M3 probe: the whole suite stayed green while
    `oncology_nsclc` was graded on `adrd`'s manifests
    (task-13-review-quality.md §2B).

    `passed=True` cannot see that — an aggregate is green if every profile is
    green, whichever tree the greens came from. The gate's own details
    metadata is what names the tree: each profile must report *its own* run
    id and counts, and profiles_checked must be a floor on the registry, not
    a count that survives reading one tree twice."""
    from src.profiles import get_registry

    manifest = _success_manifest()

    def fake_run_ingestion(full_refresh=False, max_pages=None, config=None):
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
    profiles = get_registry().refreshable()
    assert len(profiles) >= 2, "a one-profile tree cannot show tree selection"
    assert evaluation.metadata["profiles_checked"].value == len(profiles)
    details = evaluation.metadata["details"].value
    for index, indication_profile in enumerate(profiles):
        pid = indication_profile.profile_id
        # mirrors _write_manifest_for_every_profile's per-tree differentiation
        assert details[pid]["ingestion_run_id"] == f"{manifest.ingestion_run_id[:-1]}{index}", (
            f"the {pid} verdict names another profile's run: the gate read the wrong bronze tree"
        )
        assert details[pid]["record_count"] == manifest.record_count + index
        assert details[pid]["page_count"] == manifest.page_count + index


def test_bronze_silver_gate_blames_the_profile_whose_data_is_short(
    project_root_tmp, monkeypatch
) -> None:
    """The other half of M2: `bronze_silver_checks` takes the tree from its
    caller, so a gate that handed every profile the same cfg would certify
    `oncology_nsclc` on `adrd`'s silver while labelling the checks
    `oncology_nsclc`. Nothing caught it (task-13-review-quality.md §2A).

    Here exactly one profile's silver partition is truncated below its
    manifest's record count; the failure must be attributed to that profile
    and name that profile's run id."""
    from src.profiles import get_registry

    run_ids = _seed_reconcilable_state()
    assert len(run_ids) >= 2, "the victim must differ from the tree a collapsed gate reads"
    _patch_quiet_bronze(monkeypatch)

    def fake_run_transform(run_id=None, force=False, profile=None):
        return []

    monkeypatch.setattr("src.orchestration.assets.silver.run_transform", fake_run_transform)
    victim = get_registry().refreshable()[-1]
    victim_run = run_ids[victim.profile_id]
    short_silver = victim.config.paths.silver / "silver_trials" / f"run_id={victim_run}.parquet"
    pd.DataFrame([{"nct_id": "NCT00000010", "brief_title": "T"}]).to_parquet(
        short_silver, index=False
    )

    result = materialize_with_checks(
        assets=[ctg_raw_pages, silver_entities],
        asset_checks=[bronze_silver_reconciliation],
        run_config={"ops": {"ctg_raw_pages": {"config": IngestParams().model_dump()}}},
        raise_on_error=False,
    )
    evaluation = check_evaluation(result, "bronze_silver_reconciliation")
    assert evaluation is not None and not evaluation.passed
    failed = evaluation.metadata["failed_checks"].value
    assert failed, "the gate must name the checks it failed on"
    assert {f["profile"] for f in failed} == {victim.profile_id}, (
        "a short silver table for one profile must fail that profile only; a gate "
        "reading one tree for everybody reports every profile green here"
    )
    assert {f["run_id"] for f in failed} == {victim_run}


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
