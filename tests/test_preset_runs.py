"""Presets over the registry-wide run (issue #147).

One registry-wide run holds every study; a preset (MeSH node(s) + default
filters) selects its studies locally and writes an ordinary per-profile run.
"""

import dataclasses
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.config import ApiConfig, IngestionConfig, PathsConfig, ProjectConfig
from src.ingest.snapshot_manifest import (
    IngestionManifest,
    load_manifests,
    write_manifest,
)
from src.profiles import REGISTRY_PROFILE_ID, IndicationProfile, get_registry
from src.transform.build_silver_entities import (
    BronzeRebuildMismatch,
    PresetSource,
    build_silver_for_run,
)
from src.transform.preset_runs import (
    derive_preset_runs,
    pending_source_runs,
    preset_selector,
    registry_run_is_incremental,
)
from src.transform.silver_stats import load_transform_stats
from src.utils.dates import utc_now
from tests.test_build_silver import write_bronze_page


def mesh_study(
    nct_id: str,
    direct: tuple[str, ...] = (),
    ancestors: tuple[str, ...] = (),
    study_type: str = "INTERVENTIONAL",
    status: str = "RECRUITING",
) -> dict[str, Any]:
    """A bronze study with CTG-derived MeSH (`derivedSection.conditionBrowseModule`)."""
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct_id},
            "designModule": {"studyType": study_type},
            "statusModule": {"overallStatus": status},
        },
        "derivedSection": {
            "conditionBrowseModule": {
                "meshes": [{"id": f"D{i}", "term": t} for i, t in enumerate(direct)],
                "ancestors": [{"id": f"A{i}", "term": t} for i, t in enumerate(ancestors)],
            }
        },
    }


def tree_config(root: Path, name: str, scope: dict[str, Any] | None = None) -> ProjectConfig:
    """A profile's bronze tree under `root/name`; silver is per-profile for isolation."""
    return ProjectConfig(
        api=ApiConfig(base_url="https://ctg.test/api/v2"),
        paths=PathsConfig(
            bronze_api_responses=root / name / "bronze" / "api_responses",
            bronze_manifests=root / name / "bronze" / "manifests",
            silver=root / name / "silver",
            gold=root / name / "gold",
            duckdb=root / name / "warehouse" / "warehouse.duckdb",
            quarantine=root / name / "quarantine",
        ),
        ingestion=IngestionConfig(),
        scope=scope or {},
    )


def make_profile(
    root: Path, base_id: str, pid: str, mesh_terms: tuple[str, ...] = ()
) -> IndicationProfile:
    """Real profile `base_id` (taxonomy, shared paths) re-homed under `root`."""
    base = get_registry().get(base_id)
    return dataclasses.replace(
        base,
        profile_id=pid,
        config=tree_config(root, pid, scope=base.config.scope),
        mesh_terms=mesh_terms or base.mesh_terms,
    )


def write_registry_run(
    registry: IndicationProfile,
    run_id: str,
    studies: list[dict[str, Any]],
    *,
    incremental_since: str | None = None,
    minutes: int = 0,
    status: str = "success",
    params: dict[str, str] | None = None,
) -> IngestionManifest:
    """Write one registry-wide run: a page of studies plus its manifest."""
    run_dir = registry.config.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, studies)
    manifest = IngestionManifest(
        ingestion_run_id=run_id,
        query_hash="q",
        endpoint="https://ctg.test/api/v2/studies",
        params=params or {},
        profile=registry.profile_id,
        status=status,
        started_at_utc=utc_now() + timedelta(minutes=minutes),
        ended_at_utc=utc_now(),
        record_count=len(studies),
        updated_since_utc=incremental_since,
    )
    write_manifest(registry.config.paths.bronze_manifests, manifest)
    return manifest


@pytest.fixture
def registry_profile(tmp_path: Path) -> IndicationProfile:
    return make_profile(tmp_path, REGISTRY_PROFILE_ID, REGISTRY_PROFILE_ID)


def silver_ncts(preset: IndicationProfile, run_id: str) -> list[str]:
    path = preset.config.paths.silver / "silver_trials" / f"run_id={run_id}.parquet"
    return sorted(pd.read_parquet(path)["nct_id"])


# ---- preset_selector -------------------------------------------------------


def test_selector_matches_direct_and_ancestor_mesh_terms(tmp_path: Path):
    adrd = make_profile(tmp_path, "adrd", "adrd")
    select = preset_selector(adrd)

    assert select(mesh_study("NCT00000001", direct=("Alzheimer Disease",)))
    # CTG lists the closure: a dementia subtype reaches "Dementia" as an ancestor.
    assert select(mesh_study("NCT00000002", direct=("Vascular Dementia",), ancestors=("Dementia",)))
    # Text normalization: case and punctuation do not matter.
    assert select(mesh_study("NCT00000003", direct=("lewy body disease",)))
    assert not select(mesh_study("NCT00000004", direct=("Breast Neoplasms",)))


def test_selector_applies_the_presets_default_filters(tmp_path: Path):
    adrd = make_profile(tmp_path, "adrd", "adrd")
    select = preset_selector(adrd)
    mesh = ("Alzheimer Disease",)

    assert select(mesh_study("NCT00000001", mesh, status="COMPLETED"))
    assert not select(mesh_study("NCT00000002", mesh, study_type="OBSERVATIONAL"))
    assert not select(mesh_study("NCT00000003", mesh, status="UNKNOWN"))


def test_selector_never_matches_a_study_without_derived_mesh(tmp_path: Path):
    select = preset_selector(make_profile(tmp_path, "adrd", "adrd"))

    assert not select({"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}})
    assert not select(mesh_study("NCT00000002"))


def test_selector_refuses_a_profile_without_mesh_terms(registry_profile: IndicationProfile):
    with pytest.raises(ValueError, match="no preset.mesh_terms"):
        preset_selector(registry_profile)


@pytest.mark.parametrize("profile_id", ["adrd", "oncology_nsclc"])
def test_mesh_terms_are_anchored_in_the_condition_taxonomy(profile_id: str):
    """The ADRD and NSCLC MeSH sets derive from config/condition_taxonomy*.yml:
    each term must land in one of that taxonomy's named groups, not its default."""
    profile = get_registry().get(profile_id)
    assert profile.taxonomy is not None
    assert profile.mesh_terms
    for term in profile.mesh_terms:
        assert profile.taxonomy.map_condition(term).mapping_confidence != "low", term


def test_every_refreshable_profile_declares_mesh_terms():
    registry = get_registry()
    assert [p.profile_id for p in registry.refreshable() if not p.mesh_terms] == []
    assert registry.registry_wide().profile_id == REGISTRY_PROFILE_ID
    assert registry.registry_wide().mesh_terms == ()


# ---- build_silver_for_run with a PresetSource ------------------------------


def test_preset_silver_keeps_only_selected_studies(tmp_path: Path, registry_profile):
    adrd = make_profile(tmp_path, "adrd", "adrd")
    manifest = write_registry_run(
        registry_profile,
        "reg1",
        [
            mesh_study("NCT00000001", direct=("Alzheimer Disease",)),
            mesh_study("NCT00000002", direct=("Breast Neoplasms",)),
            {"protocolSection": {}},  # no NCT id: counted, skipped, as in any run
        ],
    )
    derived = manifest.model_copy(update={"ingestion_run_id": "adrd1", "profile": "adrd"})
    source = PresetSource(
        registry_profile.config.paths.bronze_api_responses / "run_id=reg1",
        preset_selector(adrd),
    )

    counts = build_silver_for_run(derived, adrd.config, profile=adrd, source=source)

    assert counts["silver_trials"] == 1
    assert silver_ncts(adrd, "adrd1") == ["NCT00000001"]
    # The run's own total is the selected count, not the registry total.
    assert (load_transform_stats(adrd.config, "adrd1") or {})["manifest_record_count"] == 1
    assert derived.record_count == 3  # the caller's manifest is left alone


def test_preset_silver_refuses_pages_that_disagree_with_the_registry_manifest(
    tmp_path: Path, registry_profile
):
    adrd = make_profile(tmp_path, "adrd", "adrd")
    manifest = write_registry_run(
        registry_profile, "reg1", [mesh_study("NCT00000001", direct=("Alzheimer Disease",))]
    )
    truncated = manifest.model_copy(
        update={"ingestion_run_id": "adrd1", "profile": "adrd", "record_count": 2}
    )
    source = PresetSource(
        registry_profile.config.paths.bronze_api_responses / "run_id=reg1",
        preset_selector(adrd),
    )

    with pytest.raises(BronzeRebuildMismatch, match="hold 1 studies"):
        build_silver_for_run(truncated, adrd.config, profile=adrd, source=source)
    assert list(adrd.config.paths.silver.glob("*/run_id=adrd1.parquet")) == []


# ---- pending_source_runs / derive_preset_runs ------------------------------


def test_registry_run_is_incremental_only_after_a_success(registry_profile):
    assert registry_run_is_incremental(registry_profile, full_refresh=False) is False
    write_registry_run(registry_profile, "bad", [], status="failed")
    assert registry_run_is_incremental(registry_profile, full_refresh=False) is False
    write_registry_run(registry_profile, "ok", [mesh_study("NCT00000001")])
    assert registry_run_is_incremental(registry_profile, full_refresh=False) is True
    assert registry_run_is_incremental(registry_profile, full_refresh=True) is False


def test_one_registry_run_yields_both_adrd_and_nsclc_presets(tmp_path: Path, registry_profile):
    """The acceptance criterion in miniature: a single registry-wide run carries
    the studies both marts need, and each preset's pass writes its own run."""
    adrd = make_profile(tmp_path, "adrd", "adrd")
    nsclc = make_profile(tmp_path, "oncology_nsclc", "oncology_nsclc")
    write_registry_run(
        registry_profile,
        "reg1",
        [
            mesh_study("NCT00000001", direct=("Alzheimer Disease",)),
            mesh_study("NCT00000002", direct=("Carcinoma, Non-Small-Cell Lung",)),
            mesh_study("NCT00000003", direct=("Breast Neoplasms",)),
        ],
    )

    adrd_runs = derive_preset_runs(registry_profile, adrd)
    nsclc_runs = derive_preset_runs(registry_profile, nsclc)

    assert silver_ncts(adrd, adrd_runs[0]) == ["NCT00000001"]
    assert silver_ncts(nsclc, nsclc_runs[0]) == ["NCT00000002"]
    # Each preset's own manifest tree records the run, so reconciliation,
    # freshness and retention keep reading one run per profile.
    (manifest,) = load_manifests(adrd.config.paths.bronze_manifests)
    assert (manifest.status, manifest.profile, manifest.source_run_id) == (
        "success",
        "adrd",
        "reg1",
    )
    assert manifest.record_count == 1
    assert list(adrd.config.paths.bronze_manifests.glob("summary_*.parquet"))
    # Idempotent: the same registry run is not derived twice.
    assert derive_preset_runs(registry_profile, adrd) == []


def test_incremental_registry_run_upserts_onto_the_presets_previous_snapshot(
    tmp_path: Path, registry_profile
):
    adrd = make_profile(tmp_path, "adrd", "adrd")
    write_registry_run(
        registry_profile,
        "reg1",
        [
            mesh_study("NCT00000001", direct=("Alzheimer Disease",)),
            mesh_study("NCT00000002", direct=("Dementia",)),
        ],
    )
    (first,) = derive_preset_runs(registry_profile, adrd)
    write_registry_run(
        registry_profile,
        "reg2",
        [mesh_study("NCT00000003", direct=("Dementia",))],
        incremental_since="2020-01-01",
        minutes=5,
    )

    (second,) = derive_preset_runs(registry_profile, adrd)

    assert silver_ncts(adrd, first) == ["NCT00000001", "NCT00000002"]
    assert silver_ncts(adrd, second) == ["NCT00000001", "NCT00000002", "NCT00000003"]


@pytest.mark.parametrize(
    "changed",
    [
        {"study_type": "OBSERVATIONAL"},
        {"status": "UNKNOWN"},
        {"direct": ("Neoplasms",)},
    ],
    ids=["study-type", "status", "mesh"],
)
def test_incremental_run_drops_a_touched_study_that_no_longer_matches(
    tmp_path: Path, registry_profile, changed
):
    """The registry run re-sent NCT00000002 and it now fails the preset: its old
    row must go, not be carried forward under the new run id and timestamp."""
    adrd = make_profile(tmp_path, "adrd", "adrd")
    write_registry_run(
        registry_profile,
        "reg1",
        [
            mesh_study("NCT00000001", direct=("Alzheimer Disease",)),
            mesh_study("NCT00000002", direct=("Dementia",)),
        ],
    )
    (first,) = derive_preset_runs(registry_profile, adrd)
    write_registry_run(
        registry_profile,
        "reg2",
        [mesh_study("NCT00000002", **{"direct": ("Dementia",), **changed})],
        incremental_since="2020-01-01",
        minutes=5,
    )

    (second,) = derive_preset_runs(registry_profile, adrd)

    assert silver_ncts(adrd, first) == ["NCT00000001", "NCT00000002"]
    assert silver_ncts(adrd, second) == ["NCT00000001"]


def test_pending_runs_start_at_the_latest_full_snapshot(tmp_path: Path, registry_profile):
    adrd = make_profile(tmp_path, "adrd", "adrd")
    study = [mesh_study("NCT00000001", direct=("Dementia",))]
    write_registry_run(registry_profile, "old_full", study)
    write_registry_run(registry_profile, "new_full", study, minutes=5)
    write_registry_run(registry_profile, "inc", study, incremental_since="2020-01-01", minutes=10)

    pending = pending_source_runs(registry_profile, adrd)

    assert [m.ingestion_run_id for m in pending] == ["new_full", "inc"]


def test_an_older_full_snapshot_the_first_pass_skipped_is_never_derived_later(
    tmp_path: Path, registry_profile
):
    """Order is monotonic: once r2 is derived, the superseded r1 stays skipped."""
    adrd = make_profile(tmp_path, "adrd", "adrd")
    r1 = [mesh_study("NCT00000001", direct=("Dementia",))]
    write_registry_run(registry_profile, "r1_old_full", r1)
    write_registry_run(
        registry_profile,
        "r2_new_full",
        r1 + [mesh_study("NCT00000002", direct=("Dementia",))],
        minutes=5,
    )
    (first,) = derive_preset_runs(registry_profile, adrd)
    write_registry_run(
        registry_profile,
        "r3_inc",
        [mesh_study("NCT00000003", direct=("Dementia",))],
        incremental_since="2020-01-01",
        minutes=10,
    )

    assert [m.ingestion_run_id for m in pending_source_runs(registry_profile, adrd)] == ["r3_inc"]
    (second,) = derive_preset_runs(registry_profile, adrd)

    assert silver_ncts(adrd, first) == ["NCT00000001", "NCT00000002"]
    assert silver_ncts(adrd, second) == ["NCT00000001", "NCT00000002", "NCT00000003"]


def test_pending_runs_refuse_when_only_incremental_runs_remain(tmp_path: Path, registry_profile):
    adrd = make_profile(tmp_path, "adrd", "adrd")
    write_registry_run(
        registry_profile,
        "inc",
        [mesh_study("NCT00000001", direct=("Dementia",))],
        incremental_since="2020-01-01",
    )

    with pytest.raises(ValueError, match="--full-refresh"):
        pending_source_runs(registry_profile, adrd)


def test_a_failed_derivation_is_recorded_and_publishes_no_silver(
    tmp_path: Path, registry_profile, monkeypatch
):
    adrd = make_profile(tmp_path, "adrd", "adrd")
    write_registry_run(registry_profile, "reg1", [mesh_study("NCT00000001", direct=("Dementia",))])

    def explode(*args, **kwargs):
        raise RuntimeError("flatten blew up")

    monkeypatch.setattr("src.transform.preset_runs.build_silver_for_run", explode)
    with pytest.raises(RuntimeError, match="flatten blew up"):
        derive_preset_runs(registry_profile, adrd)

    (manifest,) = load_manifests(adrd.config.paths.bronze_manifests)
    assert manifest.status == "failed"
    assert "flatten blew up" in (manifest.error or "")
    assert list(adrd.config.paths.silver.glob("*/*.parquet")) == []
    # A failed run is not "derived": the next pass retries the same registry run.
    assert [m.ingestion_run_id for m in pending_source_runs(registry_profile, adrd)] == ["reg1"]


NO_MESH_FIELDS = {"fields": "protocolSection.identificationModule|hasResults"}


def test_a_registry_run_without_mesh_terms_is_not_derived_from(tmp_path: Path, registry_profile):
    """A run pulled before the MeSH field was projected would select 0 studies and
    still look like a success; deriving from it must fail, publishing nothing."""
    adrd = make_profile(tmp_path, "adrd", "adrd")
    write_registry_run(
        registry_profile,
        "pre_full",
        [mesh_study("NCT00000001", direct=("Dementia",))],
        params=NO_MESH_FIELDS,
    )

    with pytest.raises(ValueError, match="--full-refresh"):
        derive_preset_runs(registry_profile, adrd)

    assert load_manifests(adrd.config.paths.bronze_manifests) == []


def test_the_next_registry_pull_is_full_when_the_newest_run_lacks_mesh_terms(registry_profile):
    write_registry_run(registry_profile, "pre_full", [], params=NO_MESH_FIELDS)
    assert not registry_run_is_incremental(registry_profile, full_refresh=False)

    mesh_fields = {"fields": "hasResults|derivedSection.conditionBrowseModule"}
    write_registry_run(registry_profile, "new_full", [], params=mesh_fields, minutes=5)
    assert registry_run_is_incremental(registry_profile, full_refresh=False)


def test_a_preset_behind_a_pruned_registry_run_fails_instead_of_skipping_it(
    tmp_path: Path, registry_profile
):
    """r2 moved NCT1 out of the preset and was pruned before the preset derived it.
    Deriving r3 on top of r1 would keep NCT1, so the gap must be an error."""
    adrd = make_profile(tmp_path, "adrd", "adrd")
    write_registry_run(
        registry_profile, "r1_full", [mesh_study("NCT00000001", direct=("Dementia",))]
    )
    derive_preset_runs(registry_profile, adrd)
    # r3 asks for changes since r2 started (less the overlap); r2's files are gone.
    r2_start = utc_now() + timedelta(days=7)
    since = (r2_start - timedelta(days=1)).date().isoformat()
    write_registry_run(
        registry_profile,
        "r3_inc",
        [mesh_study("NCT00000002", direct=("Dementia",))],
        incremental_since=since,
        minutes=2 * 7 * 24 * 60,
    )

    with pytest.raises(ValueError, match="pruned before this preset derived it"):
        derive_preset_runs(registry_profile, adrd)


def test_a_new_full_snapshot_recovers_a_preset_behind_a_pruned_registry_run(
    tmp_path: Path, registry_profile
):
    adrd = make_profile(tmp_path, "adrd", "adrd")
    write_registry_run(
        registry_profile, "r1_full", [mesh_study("NCT00000001", direct=("Dementia",))]
    )
    derive_preset_runs(registry_profile, adrd)
    write_registry_run(
        registry_profile,
        "r3_inc",
        [mesh_study("NCT00000002", direct=("Dementia",))],
        incremental_since="2999-01-01",
        minutes=10,
    )
    write_registry_run(
        registry_profile,
        "r4_full",
        [mesh_study("NCT00000002", direct=("Dementia",))],
        minutes=20,
    )

    (run,) = derive_preset_runs(registry_profile, adrd)

    assert silver_ncts(adrd, run) == ["NCT00000002"]
