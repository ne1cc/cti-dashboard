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
    warehouse: '2618 != 5200' means nothing without the profile it is about.

    The `assert checks` floor is load-bearing, not decoration: the loop over
    a possibly-empty list stayed green while bronze_silver_checks returned
    nothing at all (reviewer probe M5, task-13-review-quality.md §5)."""
    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 1)
    cfg = _build_deduped_run(tmp_path, "r_label")

    checks = bronze_silver_checks(cfg, "oncology_nsclc")
    assert checks, "bronze_silver_checks produced no checks to attribute"
    for check in checks:
        assert check.profile_id == "oncology_nsclc"


def test_run_reconciliation_loops_every_refreshable_profile(project_root_tmp, monkeypatch):
    """No cfg argument survives: an unscoped reconciliation is meaningless at
    composite grain, so the composer must derive its scopes from the registry.

    Resolved under `project_root_tmp`, not the repo root: the old form read
    this checkout's config/profiles/*.yml, so the set it enumerated was
    whatever the working tree happened to hold (reviewer F7)."""
    from src.profiles import get_registry
    from src.quality.reconciliation import run_reconciliation

    seen: list[str] = []

    def fake_reconcile(profile_id, cfg):
        seen.append(profile_id)
        assert cfg is get_registry().get(profile_id).config
        # resolve() on both sides: project_root() resolves, and on macOS
        # tmp_path is reached through the /var -> /private/var symlink.
        assert cfg.paths.duckdb.resolve().is_relative_to(Path(project_root_tmp).resolve()), (
            "the composer's profiles must resolve under the fixture root, not "
            "this checkout's data tree"
        )
        return []

    monkeypatch.setattr("src.quality.reconciliation.reconcile_profile", fake_reconcile)
    run_reconciliation()
    assert len(seen) >= 2, "a one-profile registry makes 'loops every profile' vacuous"
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
    tmp_path: Path,
    cfg,
    dim_trial_ncts: set[str],
    *,
    similarity_rows: int = 1,
    profile: str = "adrd",
    similarity_profile: str | None = None,
) -> None:
    """Minimal main_marts at the composite grain Task 12 leaves behind.

    The column must be spelled indication_profile_id — that is the name
    warehouse_checks filters on, and a misnamed column would make the test
    'pass' on a Binder error it never inspects.

    `profile` stamps the dim_trial rows (and the fct_trial_snapshot derived
    from them); `similarity_profile` stamps the mart_trial_similarity rows and
    defaults to `profile`. They differ only in the one-sided scoping test:
    a warehouse whose similarity pairs belong to another profile than its
    trials is the state that makes a dropped `where indication_profile_id = ?`
    on the mart observable — with every profile seeded identically, scoping is
    unobservable by construction (reviewer F1/F2 root cause).

    `mart_trial_similarity` is created because warehouse_checks counts rows in
    it on every call that reaches a warehouse: the query sits outside the
    >= 2-trial guard, so a helper that omitted the table would raise a catalog
    error instead of producing checks.
    """
    import duckdb

    del tmp_path  # cfg already points at it
    sim_profile = similarity_profile or profile
    cfg.paths.duckdb.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(cfg.paths.duckdb))
    con.execute("drop schema if exists main_marts cascade")
    con.execute("create schema main_marts")
    if dim_trial_ncts:
        values = ", ".join(f"('{n}', '{profile}')" for n in sorted(dim_trial_ncts))
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
        "insert into main_marts.mart_trial_similarity select ?, 'NCT00000001', 'NCT00000002' "
        "from range(?)",
        [sim_profile, similarity_rows],
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


def test_warehouse_legs_count_only_their_own_profile(tmp_path: Path, monkeypatch):
    """One-sided warehouse: the trials belong to oncology_nsclc, the
    similarity pairs to adrd, so "this profile's rows" and "all rows" differ
    on every one of the three `where indication_profile_id = ?` predicates in
    warehouse_checks — and a different leg catches each drop:

    * mart_trial_similarity: checked as oncology_nsclc the leg must report
      actual 0; an unscoped count returns adrd's pairs and certifies one
      profile's similarity data as another profile's (the reviewer's probe 1C
      found nothing catching this, task-13-review-quality.md §1C — F1).
    * dim_trial: checked as adrd, warehouse_profile_has_trials must report
      actual 0; an unscoped count returns oncology's trials and the A25 floor
      passes on foreign rows (F3's substance, guarded here as well).
    * fct_trial_snapshot: checked as adrd, current flags and dim rows are both
      zero, so the inequality is satisfiable only by the predicate; an
      unscoped count is 2 > 0 and fails the leg.

    The control direction reseeds the pairs under the checked profile, so the
    failures above pin the filter, not some incidental emptiness handling.
    """
    monkeypatch.setattr("src.transform.build_silver_entities.FLUSH_ROWS", 1)
    cfg = _build_deduped_run(tmp_path, "r_scope")
    _seed_warehouse(
        tmp_path,
        cfg,
        {"NCT00000001", "NCT00000002"},
        similarity_rows=2,
        profile="oncology_nsclc",
        similarity_profile="adrd",
    )

    onc = {c.check: c for c in warehouse_checks(cfg, "oncology_nsclc")}
    sim = onc["warehouse_profile_has_similar_trials"]
    assert sim.actual == 0, (
        "mart_trial_similarity holds only adrd pairs here; a nonzero count for "
        "oncology_nsclc means the profile predicate on the mart was dropped"
    )
    assert not sim.passed

    adrd = {c.check: c for c in warehouse_checks(cfg, "adrd")}
    trials = adrd["warehouse_profile_has_trials"]
    assert trials.actual == 0, "dim_trial holds only oncology_nsclc rows here"
    assert not trials.passed
    assert adrd["warehouse_one_current_record_per_trial"].passed, (
        "zero current flags for zero dim_trial rows is the consistent empty state"
    )

    _seed_warehouse(
        tmp_path,
        cfg,
        {"NCT00000001", "NCT00000002"},
        similarity_rows=2,
        profile="oncology_nsclc",
        similarity_profile="oncology_nsclc",
    )
    sim = {c.check: c for c in warehouse_checks(cfg, "oncology_nsclc")}[
        "warehouse_profile_has_similar_trials"
    ]
    assert sim.passed and sim.actual == 2


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

    report = build_report(cfg, output_path=project_root_tmp / "report.md")
    text = report.path.read_text(encoding="utf-8")
    assert "| check | profile | run | expected | actual | passed | note |" in text
    assert "| warehouse_covers_latest_silver | oncology_nsclc | r1 | 2 | 1 | no |" in text
    assert "### " in text, "schema drift is reported per profile"
    # The exit code `make pipeline` gates on comes from these counts, so they
    # have to be the same numbers the table above printed.
    assert (report.checks_total, report.checks_failed) == (1, 1)


def test_report_drift_section_names_its_profile(project_root_tmp, monkeypatch):
    """The Schema drift half of the report is a registry loop with two
    profile-dependent parts — the heading and the tree drift is computed
    from — and the reviewer's probe 2C replaced both (caller's cfg plus a
    hardcoded "### adrd") while `tests/test_reconciliation.py` stayed at 16
    passed (task-13-review-quality.md §2C — F4).

    Each profile's tree here holds its own bronze run and its own baseline:
    one baseline matches the run (status ok), the other carries a path the
    run no longer shows (status drift_detected, with the sentinel in the
    removed list). A section computed from the wrong tree or under the wrong
    heading cannot reproduce that per-section pairing."""
    import json

    from src.profiles import get_registry
    from src.quality.data_quality_report import build_report
    from src.quality.schema_drift import BASELINE_FILENAME, collect_field_paths

    monkeypatch.setattr("src.quality.data_quality_report.run_reconciliation", lambda: [])

    profiles = get_registry().refreshable()
    assert len(profiles) >= 2, "a one-profile loop cannot show per-profile drift"
    sentinel = "protocolSection.baselineOnlyModule.goneField"
    study = make_study("NCT00000001")
    observed = sorted(collect_field_paths(study))
    runs: dict[str, str] = {}
    for index, indication_profile in enumerate(profiles):
        profile_cfg = indication_profile.config
        run_id = f"20260904T120000Z_d000000{index}"
        runs[indication_profile.profile_id] = run_id
        write_bronze_page(profile_cfg.paths.bronze_api_responses / f"run_id={run_id}", 1, [study])
        profile_cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
        write_manifest(profile_cfg.paths.bronze_manifests, make_manifest(run_id, record_count=1))
        baseline_paths = sorted([*observed, sentinel]) if index == 1 else observed
        (profile_cfg.paths.bronze_api_responses.parent / BASELINE_FILENAME).write_text(
            json.dumps(
                {
                    "created_at_utc": "2026-09-04T12:00:00Z",
                    "source_run_id": "old",
                    "paths": baseline_paths,
                }
            ),
            encoding="utf-8",
        )

    report = build_report(make_config(project_root_tmp), output_path=project_root_tmp / "drift.md")
    text = report.path.read_text(encoding="utf-8")

    sections: dict[str, str] = {}
    for chunk in text.split("### ")[1:]:
        heading, _, body = chunk.partition("\n")
        sections[heading.strip()] = body
    assert {"adrd", "oncology_nsclc"} <= set(sections), (
        f"every refreshable profile gets its own drift heading; headings rendered: "
        f"{sorted(sections)}"
    )
    for pid, expected_status in (("adrd", "**ok**"), ("oncology_nsclc", "**drift_detected**")):
        body = sections[pid]
        assert f"Run checked: `{runs[pid]}`" in body, (
            f"the {pid} section must report {pid}'s own run; a section computed "
            "from another tree cannot name it"
        )
        assert f"Status: {expected_status}" in body, (
            f"the {pid} section must report {pid}'s own baseline delta"
        )
    assert sentinel in sections["oncology_nsclc"]
    assert sentinel not in sections["adrd"], "removed paths must not bleed across sections"
