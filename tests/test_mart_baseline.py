"""Profile-retirement step 1: additive registry keys leave every existing mart output unchanged.

tests/fixtures/mart_baseline/ holds the marts as they were before the keys were added
(see its README). The comparison projects the live warehouse onto the baseline's columns,
so new columns never trip it but a changed row, count or fan-out does.
"""

import json
from pathlib import Path

import duckdb
import pytest

from tests.mart_baseline import cells_match, dump_marts, load_baseline

TRIAL_KEYED = [
    "dim_trial",
    "fct_trial_snapshot",
    "fct_trial_site",
    "bridge_trial_sponsor",
    "bridge_trial_condition",
    "mart_study_snapshot_audit",
    "mart_location_snapshot_audit",
    "mart_trial_change_events",
]
CONDITION_KEYED = [
    "dim_condition",
    "bridge_trial_condition",
    "mart_trial_activity",
    "mart_recruiting_competition",
    "mart_condition_geography_trends",
    "mart_feasibility_priority_queue",
    "mart_baseline_demographics_coverage",
]


def test_existing_mart_outputs_match_baseline(fixture_project_root: Path) -> None:
    baseline = load_baseline()
    assert baseline, "baseline fixtures missing"
    live = dump_marts(fixture_project_root)
    assert set(baseline) <= set(live)
    for name, expected in baseline.items():
        cols = expected["columns"]
        idx = [live[name]["columns"].index(c) for c in cols]  # ValueError if a column vanished
        actual_rows = sorted(
            ([row[i] for i in idx] for row in live[name]["rows"]),
            key=lambda r: json.dumps(r, sort_keys=True),
        )
        assert len(actual_rows) == len(expected["rows"]), f"{name}: row count changed"
        for want, got in zip(expected["rows"], actual_rows, strict=True):
            assert all(cells_match(w, g) for w, g in zip(want, got, strict=True)), (
                f"{name}: {want} != {got}"
            )


def test_cells_match_tolerance() -> None:
    assert cells_match(0.5, 0.5 * (1 + 5e-7))
    assert not cells_match(0.5, 0.5 * (1 + 2e-6))
    assert not cells_match(1, 1.0)  # a count turning into a float is a type change
    assert cells_match(None, None) and not cells_match(None, 0)


def _scalar(root: Path, sql: str) -> int:
    con = duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True)
    try:
        return int(con.execute(sql).fetchone()[0])  # type: ignore[index]
    finally:
        con.close()


@pytest.mark.parametrize("table", TRIAL_KEYED)
def test_registry_trial_key_is_populated_and_hashes_nct_id(
    fixture_project_root: Path, table: str
) -> None:
    # The one-snapshot fixture has no change events, so that mart is checked vacuously.
    if table != "mart_trial_change_events":
        sql = f"select count(*) from main_marts.{table}"  # noqa: S608
        assert _scalar(fixture_project_root, sql) > 0
    bad = _scalar(
        fixture_project_root,
        f"select count(*) from main_marts.{table} "  # noqa: S608
        "where registry_trial_key is null or registry_trial_key != md5(nct_id)",
    )
    assert bad == 0


@pytest.mark.parametrize("table", CONDITION_KEYED)
def test_registry_condition_key_is_populated_and_matches_dim_condition(
    fixture_project_root: Path, table: str
) -> None:
    root = fixture_project_root
    assert _scalar(root, f"select count(*) from main_marts.{table}") > 0  # noqa: S608
    assert (
        _scalar(
            root,
            f"select count(*) from main_marts.{table} "  # noqa: S608
            "where registry_condition_key is null",
        )
        == 0
    )
    if table.startswith("mart_"):
        # Every group-level mart row must address a real dim_condition group node.
        orphans = _scalar(
            root,
            f"select count(*) from main_marts.{table} m "  # noqa: S608
            "where not exists (select 1 from main_marts.dim_condition d "
            "where d.registry_condition_key = m.registry_condition_key "
            "and d.indication_profile_id = m.indication_profile_id)",
        )
        assert orphans == 0
