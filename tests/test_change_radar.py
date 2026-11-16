"""Change Radar: event grain, run-trust gate, and persistence across pruning."""

from pathlib import Path

import pytest

from src.utils.retention import RetentionConfig, prune_profile
from tests.conftest import CHANGE_RADAR_RUN_IDS as RUNS
from tests.fixture_warehouse import build_dbt_fixture, build_fixture_warehouse
from tests.test_dbt_fixture_build import _rows

EVENTS = "main_marts.mart_trial_change_events"
COLS = (
    "nct_id, event_type, field_name, before_value, after_value, before_value_type, after_value_type"
)


def _events(root: Path) -> list[tuple]:
    return _rows(root, f"select {COLS} from {EVENTS} order by nct_id, event_type, field_name")


def test_events_cover_each_type_and_exclude_untrusted_runs(change_radar_root: Path) -> None:
    assert _events(change_radar_root) == [
        (
            "NCT00000001",
            "enrollment_change",
            "enrollment_count",
            "500",
            "650",
            "Anticipated",
            "ACTUAL",
        ),
        (
            "NCT00000001",
            "enrollment_change",
            "enrollment_type",
            "Anticipated",
            "ACTUAL",
            None,
            None,
        ),
        (
            "NCT00000002",
            "status_change",
            "overall_status",
            "ACTIVE_NOT_RECRUITING",
            "UNKNOWN",
            None,
            None,
        ),
        (
            "NCT00000003",
            "completion_date_change",
            "primary_completion_date",
            "2025-08-01",
            "2026-02-15",
            None,
            "ACTUAL",
        ),
        (
            "NCT00000004",
            "outcome_set_change",
            "outcome_set",
            "primary: Pathologic complete response rate [6 months]",
            "primary: Overall response rate [6 months]",
            None,
            None,
        ),
    ]
    # stale r3 and failed r4 are never either side of a pair
    assert _rows(
        change_radar_root,
        f"select count(*) from {EVENTS} where '{RUNS['r3']}' in (before_ingestion_run_id, "
        f"after_ingestion_run_id) or '{RUNS['r4']}' in (before_ingestion_run_id, "
        f"after_ingestion_run_id)",
    ) == [(0,)]


def test_event_carries_runs_timestamps_and_deterministic_id(change_radar_root: Path) -> None:
    sql = (
        f"select before_ingestion_run_id, after_ingestion_run_id, before_observed_at_utc, "
        f"after_observed_at_utc, event_id from {EVENTS} where nct_id = 'NCT00000002'"
    )
    (before, after, t0, t1, event_id) = _rows(change_radar_root, sql)[0]
    assert (before, after) == (RUNS["r1"], RUNS["r2"])
    assert (t0.day, t1.day) == (1, 8)
    assert (
        event_id
        == _rows(
            change_radar_root,
            "select md5(concat_ws('||', 'adrd', 'NCT00000002', "
            f"'{RUNS['r1']}', '{RUNS['r2']}', 'status_change', 'overall_status'))",
        )[0][0]
    )


def test_events_survive_pruning_and_rebuild(change_radar_root: Path, monkeypatch) -> None:
    from src.profiles import load_profile, load_shared_paths

    before = _events(change_radar_root)
    monkeypatch.setenv("CTI_PROJECT_ROOT", str(change_radar_root))
    shared = load_shared_paths()
    profile = load_profile(change_radar_root / "config/profiles/adrd.yml", shared=shared)
    pruned = prune_profile(profile, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=1))
    assert RUNS["r1"] in {p.run_id for p in pruned}
    build_dbt_fixture(change_radar_root)
    assert _events(change_radar_root) == before


@pytest.mark.parametrize("model", ["int_trial_outcome_sets", "int_trial_outcome_changes"])
def test_outcome_models_exist(change_radar_root: Path, model: str) -> None:
    assert _rows(change_radar_root, f"select count(*) from main_intermediate.{model}")[0][0] > 0


REG_EVENTS = "main_marts.mart_registry_trial_change_events"


def test_registry_change_radar_events_untrusted_exclusion_and_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Canonical Change Radar captures trusted transitions, excludes untrusted, persists."""
    import json
    from dataclasses import replace
    from datetime import UTC, datetime

    from src.profiles import load_profile, load_shared_paths
    from tests.conftest import change_radar_runs
    from tests.mart_baseline import load_baseline

    mesh = {"conditionBrowseModule": {"meshes": [{"id": "D000544", "term": "Alzheimer Disease"}]}}
    # Tag NCT00000001..0009 as ADRD; leave NCT00000010 unassigned and mutate it in r2
    base_overrides = {f"NCT{i:08d}": {"derivedSection": mesh} for i in range(1, 10)}
    runs = []
    for run in change_radar_runs():
        merged = {**base_overrides, **run.study_overrides}
        for nct, patch in run.study_overrides.items():
            if nct in base_overrides:
                merged[nct] = {**base_overrides[nct], **patch}
        status_overrides = dict(run.status_overrides)
        if run.run_id == RUNS["r2"]:
            status_overrides["NCT00000010"] = "COMPLETED"
        runs.append(
            replace(
                run,
                profile="full_catalog",
                condition="",
                study_overrides=merged,
                status_overrides=status_overrides,
            )
        )
    # Add a newer profile-only run modifying NCT00000007; must be excluded from canonical events
    runs.append(
        replace(
            change_radar_runs()[0],
            profile="adrd",
            run_id="20260929T100000Z_profile_only",
            started_at=datetime(2026, 9, 29, 10, tzinfo=UTC),
            retrieved_at="2026-09-29T10:01:00+00:00",
            status_overrides={"NCT00000007": "RECRUITING"},
        )
    )

    root = tmp_path / "registry_change_radar"
    build_fixture_warehouse(root, runs=runs)

    # All 6 expected canonical events (5 ADRD + 1 unassigned NCT00000010)
    got_all = _rows(
        root, f"select {COLS} from {REG_EVENTS} order by nct_id, event_type, field_name"
    )
    assert got_all == [
        (
            "NCT00000001",
            "enrollment_change",
            "enrollment_count",
            "500",
            "650",
            "Anticipated",
            "ACTUAL",
        ),
        (
            "NCT00000001",
            "enrollment_change",
            "enrollment_type",
            "Anticipated",
            "ACTUAL",
            None,
            None,
        ),
        (
            "NCT00000002",
            "status_change",
            "overall_status",
            "ACTIVE_NOT_RECRUITING",
            "UNKNOWN",
            None,
            None,
        ),
        (
            "NCT00000003",
            "completion_date_change",
            "primary_completion_date",
            "2025-08-01",
            "2026-02-15",
            None,
            "ACTUAL",
        ),
        (
            "NCT00000004",
            "outcome_set_change",
            "outcome_set",
            "primary: Pathologic complete response rate [6 months]",
            "primary: Overall response rate [6 months]",
            None,
            None,
        ),
        (
            "NCT00000010",
            "status_change",
            "overall_status",
            "RECRUITING",
            "COMPLETED",
            None,
            None,
        ),
    ]

    # Untrusted runs (r3 stale, r4 failed) and profile-only run never appear
    untrusted_runs = f"('{RUNS['r3']}', '{RUNS['r4']}', '20260929T100000Z_profile_only')"
    assert _rows(
        root,
        f"select count(*) from {REG_EVENTS} where "
        f"before_ingestion_run_id in {untrusted_runs} "
        f"or after_ingestion_run_id in {untrusted_runs}",
    ) == [(0,)]

    # Scoped by bridge_preset_trial for adrd excludes unassigned NCT00000010
    adrd_scoped = _rows(
        root,
        f"select {COLS} from {REG_EVENTS} e "
        "where exists (select 1 from main_marts.bridge_preset_trial b "
        "where b.nct_id = e.nct_id and b.snapshot_id = e.after_ingestion_run_id "
        "and b.preset_id = 'adrd') order by nct_id, event_type, field_name",
    )
    assert [row[0] for row in adrd_scoped] == [
        "NCT00000001",
        "NCT00000001",
        "NCT00000002",
        "NCT00000003",
        "NCT00000004",
    ]

    # Deterministic canonical event_id omits indication_profile_id
    (before, after, t0, t1, event_id, reg_key) = _rows(
        root,
        f"select before_ingestion_run_id, after_ingestion_run_id, before_observed_at_utc, "
        f"after_observed_at_utc, event_id, registry_trial_key from {REG_EVENTS} "
        f"where nct_id = 'NCT00000002'",
    )[0]
    assert (before, after) == (RUNS["r1"], RUNS["r2"])
    assert (t0.day, t1.day) == (1, 8)
    assert _rows(
        root,
        f"select md5(concat_ws('||', 'NCT00000002', '{RUNS['r1']}', '{RUNS['r2']}', "
        "'status_change', 'overall_status')), md5('NCT00000002')",
    ) == [(event_id, reg_key)]

    # Outcome intermediate models are populated
    for model in ("int_registry_trial_outcome_sets", "int_registry_trial_outcome_changes"):
        assert _rows(root, f"select count(*) from main_intermediate.{model}")[0][0] > 0

    # Incremental persistence across full_catalog retention pruning
    before_prune = got_all
    monkeypatch.setenv("CTI_PROJECT_ROOT", str(root))
    shared = load_shared_paths()
    catalog = load_profile(root / "config/profiles/full_catalog.yml", shared=shared)
    pruned = prune_profile(catalog, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=1))
    assert RUNS["r1"] in {p.run_id for p in pruned}
    build_dbt_fixture(root)
    assert (
        _rows(root, f"select {COLS} from {REG_EVENTS} order by nct_id, event_type, field_name")
        == before_prune
    )

    # Single-snapshot #146 baseline has 0 events; verify empty single-run full_catalog matches
    single_root = tmp_path / "single_run_radar"
    build_fixture_warehouse(single_root, runs=[runs[0]])
    baseline_events = load_baseline()["mart_trial_change_events"]["rows"]
    assert json.loads(json.dumps(baseline_events)) == []
    assert _rows(single_root, f"select count(*) from {REG_EVENTS}") == [(0,)]


def test_registry_change_radar_matches_legacy_multi_run_events(
    tmp_path: Path, change_radar_root: Path
) -> None:
    """Multi-run parity: preset-scoped canonical Change Radar equals legacy mart rows."""
    from dataclasses import replace

    from tests.conftest import change_radar_runs

    mesh = {"conditionBrowseModule": {"meshes": [{"id": "D000544", "term": "Alzheimer Disease"}]}}
    base_overrides = {f"NCT{i:08d}": {"derivedSection": mesh} for i in range(1, 10)}
    reg_runs = []
    for run in change_radar_runs():
        merged = {**base_overrides, **run.study_overrides}
        for nct, patch in run.study_overrides.items():
            if nct in base_overrides:
                merged[nct] = {**base_overrides[nct], **patch}
        reg_runs.append(
            replace(
                run,
                profile="full_catalog",
                condition="",
                study_overrides=merged,
            )
        )

    reg_root = tmp_path / "registry_radar_parity"
    build_fixture_warehouse(reg_root, runs=reg_runs)

    parity_cols = (
        f"{COLS}, before_ingestion_run_id, after_ingestion_run_id, "
        "before_observed_at_utc, after_observed_at_utc"
    )
    legacy_rows = _rows(
        change_radar_root,
        f"select {parity_cols} from {EVENTS} order by nct_id, event_type, field_name",
    )
    registry_rows = _rows(
        reg_root,
        f"select {parity_cols} from {REG_EVENTS} e "
        "where exists (select 1 from main_marts.bridge_preset_trial b "
        "where b.nct_id = e.nct_id and b.snapshot_id = e.after_ingestion_run_id "
        "and b.preset_id = 'adrd') order by nct_id, event_type, field_name",
    )
    assert len(legacy_rows) == 5
    assert registry_rows == legacy_rows
