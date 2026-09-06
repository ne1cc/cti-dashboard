"""Cross-layer reconciliation must account for the transform's legitimate
exclusions without becoming a weaker data-loss guard."""

import json
from pathlib import Path

import pytest

from src.ingest.snapshot_manifest import write_manifest
from src.quality.reconciliation import bronze_silver_checks, reconcile_profile, warehouse_checks
from src.transform.build_silver_entities import build_silver_for_run
from src.transform.silver_stats import (
    expected_trial_rows,
    load_transform_stats,
    stats_path,
)
from tests.test_build_silver import make_config, make_manifest, make_study, write_bronze_page


def _check(cfg, name: str):
    return next(c for c in bronze_silver_checks(cfg, "adrd") if c.check == name)


def _build_deduped_run(tmp_path: Path, run_id: str):
    """Three bronze records, one repeated NCT ID -> two silver trial rows."""
    cfg = make_config(tmp_path)
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    write_bronze_page(run_dir, 1, [make_study("NCT00000001"), make_study("NCT00000002")])
    write_bronze_page(run_dir, 2, [make_study("NCT00000002")])
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(cfg.paths.bronze_manifests, make_manifest(run_id, record_count=3))
    build_silver_for_run(make_manifest(run_id, record_count=3), cfg)
    return cfg


def test_reconciliation_accounts_for_recorded_dedup(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 1)
    cfg = _build_deduped_run(tmp_path, "r_dedup")

    check = _check(cfg, "bronze_manifest_vs_silver_rows")
    assert check.expected == 2, "expectation must subtract the transform's dedup"
    assert check.actual == 2
    assert check.passed, "a legitimate dedup must not fail the data-loss guard"
    assert check.note == ""


def test_reconciliation_without_stats_stays_strict(tmp_path: Path, monkeypatch):
    """Deleting the sidecar must not silently downgrade the check to 'any
    shortfall is fine'."""
    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 1)
    cfg = _build_deduped_run(tmp_path, "r_nostats")
    stats_path(cfg, "r_nostats").unlink()

    check = _check(cfg, "bronze_manifest_vs_silver_rows")
    assert check.expected == 3
    assert check.actual == 2
    assert not check.passed
    assert "make transform --force" in check.note


def test_reconciliation_fails_on_loss_beyond_recorded_exclusions(tmp_path: Path, monkeypatch):
    """With stats claiming nothing was excluded, a shortfall is a failure even
    though it would satisfy a 'rows <= records' rule."""
    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 1)
    cfg = _build_deduped_run(tmp_path, "r_loss")
    path = stats_path(cfg, "r_loss")
    stats = json.loads(path.read_text(encoding="utf-8"))
    stats["duplicate_nct_ids_dropped"] = 0
    path.write_text(json.dumps(stats), encoding="utf-8")

    check = _check(cfg, "bronze_manifest_vs_silver_rows")
    assert check.expected == 3
    assert check.actual == 2
    assert not check.passed


def test_silver_nct_ids_unique_still_passes_after_dedup(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 1)
    cfg = _build_deduped_run(tmp_path, "r_unique")
    assert _check(cfg, "silver_nct_ids_unique").passed


def test_stale_stats_for_a_different_record_count_are_rejected():
    """A reused run_id must not have its expectation drawn from another run's
    exclusions."""
    stats = {
        "manifest_record_count": 999,
        "duplicate_nct_ids_dropped": 997,
        "records_without_nct_id": 0,
    }
    assert expected_trial_rows(stats, record_count=3) is None


def test_missing_stats_yield_no_expectation():
    assert expected_trial_rows(None, record_count=3) is None


def test_unreadable_stats_yield_no_expectation(tmp_path: Path):
    cfg = make_config(tmp_path)
    path = stats_path(cfg, "r_bad")
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    assert load_transform_stats(cfg, "r_bad") is None
    assert expected_trial_rows(load_transform_stats(cfg, "r_bad"), record_count=3) is None


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"manifest_record_count": 3, "duplicate_nct_ids_dropped": "not-a-number"},
    ],
)
def test_malformed_stats_fields_yield_no_expectation(bad):
    assert expected_trial_rows(bad, record_count=3) is None


def test_reconcile_profile_composes_both_layers(tmp_path: Path, monkeypatch):
    """The composer, not the layer functions, is what `data_quality_report`
    calls — it must still report the warehouse layer when there is no
    warehouse to check."""
    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 1)
    cfg = _build_deduped_run(tmp_path, "r_compose")

    names = {c.check for c in reconcile_profile("adrd", cfg)}
    assert "bronze_manifest_vs_silver_rows" in names
    assert "warehouse_exists" in names, "tmp cfg has no warehouse; that layer still reports"
    assert {c.profile_id for c in reconcile_profile("adrd", cfg)} == {"adrd"}


def test_every_check_carries_its_profile(tmp_path: Path, monkeypatch):
    """An unattributed failure is unactionable once two profiles share the
    warehouse: '2618 != 5200' means nothing without the profile it is about."""
    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 1)
    cfg = _build_deduped_run(tmp_path, "r_label")

    for check in bronze_silver_checks(cfg, "oncology_nsclc"):
        assert check.profile_id == "oncology_nsclc"


def test_run_reconciliation_loops_every_refreshable_profile(monkeypatch):
    """No cfg argument survives: an unscoped reconciliation is meaningless at
    composite grain, so the composer must derive its scopes from the registry."""
    from src.profiles import get_registry
    from src.quality.reconciliation import run_reconciliation

    seen: list[str] = []

    def fake_reconcile(profile_id, cfg):
        seen.append(profile_id)
        assert cfg is get_registry().get(profile_id).config
        return []

    monkeypatch.setattr("src.quality.reconciliation.reconcile_profile", fake_reconcile)
    run_reconciliation()
    assert seen == [p.profile_id for p in get_registry().refreshable()]


def test_coverage_check_allows_retained_history(tmp_path: Path, monkeypatch):
    """dim_trial legitimately holds trials from earlier snapshots. Count
    equality fails every warehouse older than its latest run, so a superset
    must pass and only a trial missing from dim_trial may fail."""
    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 1)
    cfg = _build_deduped_run(tmp_path, "r_hist")
    _seed_warehouse(tmp_path, cfg, {"NCT00000001", "NCT00000002", "NCT00000099"})

    coverage = next(
        c for c in warehouse_checks(cfg, "adrd") if c.check == "warehouse_covers_latest_silver"
    )
    assert coverage.passed, "history the latest run lacks is not data loss"

    _seed_warehouse(tmp_path, cfg, {"NCT00000002"})
    coverage = next(
        c for c in warehouse_checks(cfg, "adrd") if c.check == "warehouse_covers_latest_silver"
    )
    assert not coverage.passed, "a trial in the latest silver run must be in dim_trial"
    assert "NCT00000001" in coverage.note


def _seed_warehouse(
    tmp_path: Path, cfg, dim_trial_ncts: set[str], *, similarity_rows: int = 1
) -> None:
    """Minimal main_marts at the composite grain Task 12 leaves behind.

    The column must be spelled indication_profile_id — that is the name
    warehouse_checks filters on, and a misnamed column would make the test
    'pass' on a Binder error it never inspects.

    `mart_trial_similarity` is created because warehouse_checks counts rows in
    it on every call that reaches a warehouse: the query sits outside the
    >= 2-trial guard, so a helper that omitted the table would raise a catalog
    error instead of producing checks.
    """
    import duckdb

    del tmp_path  # cfg already points at it
    cfg.paths.duckdb.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(cfg.paths.duckdb))
    con.execute("drop schema if exists main_marts cascade")
    con.execute("create schema main_marts")
    if dim_trial_ncts:
        values = ", ".join(f"('{n}', 'adrd')" for n in sorted(dim_trial_ncts))
        con.execute(
            f"create table main_marts.dim_trial as from values {values} "
            "as t(nct_id, indication_profile_id)"
        )
    else:
        con.execute(
            "create table main_marts.dim_trial (nct_id varchar, indication_profile_id varchar)"
        )
    con.execute(
        "create table main_marts.fct_trial_snapshot as select "
        "indication_profile_id, true as current_record_flag from main_marts.dim_trial"
    )
    con.execute(
        "create table main_marts.mart_trial_similarity "
        "(indication_profile_id varchar, nct_id_a varchar, nct_id_b varchar)"
    )
    con.execute(
        "insert into main_marts.mart_trial_similarity "
        "select 'adrd', 'NCT00000001', 'NCT00000002' from range(?)",
        [similarity_rows],
    )
    con.close()


def test_both_sides_empty_is_caught_by_the_floor(tmp_path: Path) -> None:
    """The vacuous-green case A25 exists for.

    Not `_build_deduped_run`: its run holds two silver trials, so an empty
    dim_trial makes coverage *fail* (two missing NCTs) and the test would prove
    nothing about the hole. Here bronze reports a successful run whose silver
    partition holds nothing, which is what an empty upstream result set or a
    scope change actually looks like from this layer's point of view.
    """
    import pandas as pd

    cfg = make_config(tmp_path)
    run_id = "20260904T120000Z_aaaaaa"
    cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
    write_manifest(cfg.paths.bronze_manifests, make_manifest(run_id, record_count=0))
    silver_trials = cfg.paths.silver / "silver_trials"
    silver_trials.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"nct_id": []}).to_parquet(silver_trials / f"run_id={run_id}.parquet", index=False)
    _seed_warehouse(tmp_path, cfg, set())

    legs = {c.check: c for c in warehouse_checks(cfg, "adrd")}
    assert legs["warehouse_covers_latest_silver"].passed, (
        "this assert documents the hole: coverage is a set difference, and "
        "set() - set() has nothing missing"
    )
    assert legs["warehouse_one_current_record_per_trial"].passed, "0 <= 0"
    assert not legs["warehouse_profile_has_trials"].passed
    assert legs["warehouse_profile_has_trials"].actual == 0


def test_similarity_floor_tracks_the_two_trial_boundary(tmp_path: Path, monkeypatch):
    """Both directions of the guard, because each fails differently.

    `pairs` joins the features model to itself on `a.nct_id != b.nct_id`, so one
    current trial has no pair to find and the leg must not exist at all — the
    assertion is on the check's absence, not on a passing check, so a leg that
    fires and passes cannot be mistaken for the guard working. At two current
    trials a pair exists by construction, so an empty mart is the failure.
    """
    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 1)
    cfg = _build_deduped_run(tmp_path, "r_pair_floor")
    _seed_warehouse(tmp_path, cfg, {"NCT00000001"}, similarity_rows=0)
    assert "warehouse_profile_has_similar_trials" not in {
        c.check for c in warehouse_checks(cfg, "adrd")
    }

    _seed_warehouse(tmp_path, cfg, {"NCT00000001", "NCT00000002"}, similarity_rows=0)
    legs = {c.check: c for c in warehouse_checks(cfg, "adrd")}
    assert not legs["warehouse_profile_has_similar_trials"].passed
    assert "mart_trial_similarity" in legs["warehouse_profile_has_similar_trials"].note


def test_report_reconciliation_table_names_the_profile(project_root_tmp, monkeypatch):
    """The Markdown report is the artifact a human reads on a Friday. A failed
    check with no profile column is a wrong diagnosis waiting to happen.

    Requested under `project_root_tmp` rather than a bare `tmp_path` because
    `build_report` loops the registry for its schema-drift section: resolved at
    the repo root, that loop reads this checkout's real `data/bronze/` trees —
    and on a tree holding a `success` manifest whose bronze pages no longer exist
    it raises from `check_drift` instead of reporting.
    """
    from src.quality.data_quality_report import build_report
    from src.quality.reconciliation import ReconciliationCheck

    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 1)
    cfg = _build_deduped_run(project_root_tmp, "r_report")
    monkeypatch.setattr(
        "src.quality.data_quality_report.run_reconciliation",
        lambda: [
            ReconciliationCheck(
                profile_id="oncology_nsclc",
                check="warehouse_covers_latest_silver",
                run_id="r1",
                expected=2,
                actual=1,
                passed=False,
                note="Not in dim_trial: NCT00000001",
            )
        ],
    )

    text = build_report(cfg, output_path=project_root_tmp / "report.md").read_text(encoding="utf-8")
    assert "| check | profile | run | expected | actual | passed | note |" in text
    assert "| warehouse_covers_latest_silver | oncology_nsclc | r1 | 2 | 1 | no |" in text
    assert "### " in text, "schema drift is reported per profile"
