"""Change Radar: event grain, run-trust gate, and persistence across pruning."""

from pathlib import Path

import pytest

from src.utils.retention import RetentionConfig, prune_profile
from tests.conftest import CHANGE_RADAR_RUN_IDS as RUNS
from tests.fixture_warehouse import build_dbt_fixture
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
    shared = load_shared_paths(change_radar_root / "config/shared_paths.yml")
    profile = load_profile(change_radar_root / "config/profiles/adrd.yml", shared=shared)
    pruned = prune_profile(profile, RetentionConfig(bronze_runs_to_keep=1, snapshot_runs_to_keep=1))
    assert RUNS["r1"] in {p.run_id for p in pruned}
    build_dbt_fixture(change_radar_root)
    assert _events(change_radar_root) == before


@pytest.mark.parametrize("model", ["int_trial_outcome_sets", "int_trial_outcome_changes"])
def test_outcome_models_exist(change_radar_root: Path, model: str) -> None:
    assert _rows(change_radar_root, f"select count(*) from main_intermediate.{model}")[0][0] > 0
