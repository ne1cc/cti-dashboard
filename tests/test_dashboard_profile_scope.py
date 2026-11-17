"""Dashboard readers stay at registry grain and select rows through YAML presets."""

from __future__ import annotations

import ast
import inspect
import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.preset_helpers import preset_for

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
PAGES = [
    "dashboard/overview.py",
    "dashboard/pages/1_Priority_Queue.py",
    "dashboard/pages/2_Competition_Landscape.py",
    "dashboard/pages/3_Geography_Trends.py",
    "dashboard/pages/4_Site_Overlap.py",
    "dashboard/pages/5_Sponsor_Landscape.py",
    "dashboard/pages/6_Data_Reliability.py",
    "dashboard/pages/7_Trial_Explorer.py",
    "dashboard/pages/8_Trial_Similarity.py",
    "dashboard/pages/9_Trial_Transparency.py",
    "dashboard/pages/10_Access_Rates.py",
    "dashboard/pages/11_Participation_Prevalence.py",
]
DATA_SOURCE = DASHBOARD / "components" / "data.py"


def _load_data_module():
    if str(DASHBOARD) not in sys.path:
        sys.path.insert(0, str(DASHBOARD))
    from components import data

    return data


def _sql_literals() -> list[str]:
    tree = ast.parse(DATA_SOURCE.read_text(encoding="utf-8"))
    strings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = (
            node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        )
        if function in {"query", "execute"} and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, str):
                strings.append(node.args[0].value)
    return strings


def test_every_warehouse_reader_is_preset_first() -> None:
    data = _load_data_module()
    checked = 0
    for name, obj in vars(data).items():
        if name.startswith("_") or name in {"warehouse_path", "query"}:
            continue
        if not callable(obj) or getattr(obj, "__module__", None) != data.__name__:
            continue
        params = list(inspect.signature(obj).parameters)
        assert params and params[0] == "preset", f"{name}() is not scoped: {params}"
        checked += 1
    assert checked >= 10


def test_every_page_binds_the_selector_return_value() -> None:
    binding = re.compile(r"^preset = render_preset_selector\(\)$", re.M)
    for script in PAGES:
        source = (ROOT / script).read_text(encoding="utf-8")
        assert "render_preset_selector(" in source, f"{script} has no preset selector"
        assert binding.search(source), f"{script} does not bind the selected preset"


def test_readers_do_not_filter_on_legacy_profile_grain() -> None:
    predicate = re.compile(r"indication_profile_id\s*(?:=|!=|<>|<=|>=|<|>|\b(?:in|like)\b)", re.I)
    offenders = [sql for sql in _sql_literals() if predicate.search(sql)]
    assert not offenders, "registry readers still filter on indication_profile_id"
    assert any("bridge_preset_trial" in sql for sql in _sql_literals())


def test_registry_readers_keep_preset_rows_separate(
    registry_preset_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import streamlit as st

    from src.config import get_config

    adrd = preset_for("adrd")
    presets = {"adrd": adrd, "shared_preset": replace(adrd, preset_id="shared_preset")}
    monkeypatch.setenv("CTI_PROJECT_ROOT", str(registry_preset_project_root))
    monkeypatch.chdir(registry_preset_project_root)
    get_config.cache_clear()
    st.cache_data.clear()
    st.cache_resource.clear()
    data = _load_data_module()
    readers = (
        data.priority_queue,
        data.recruiting_competition,
        data.condition_geography_trends,
        data.site_overlap,
        data.sponsor_landscape,
        data.trial_explorer,
        data.data_reliability,
    )
    for preset_id, preset in presets.items():
        for reader in readers:
            frame = reader(preset)
            if frame.empty:
                continue
            assert "indication_profile_id" in frame.columns
            assert set(frame.indication_profile_id) == {preset_id}
        trials = data.trial_explorer(preset)
        assert not trials.empty
        assert trials.nct_id.is_unique
        assert len(trials) == data.preset_trial_count(preset)
        assert int(data.overview_metrics(preset)["total_trials"]) == len(trials)

    # The retained profile-only pull must not replace the authoritative registry record.
    adrd = data.trial_explorer(presets["adrd"]).set_index("nct_id")
    assert "NCT00000095" not in adrd.index


def _snapshot_dates(frame) -> set[str]:
    """The distinct ``snapshot_date`` values in ``frame``, as ISO date strings."""
    return {str(value)[:10] for value in frame["snapshot_date"]}


@pytest.fixture
def facility_snapshots(monkeypatch: pytest.MonkeyPatch):
    """An isolated registry-grain facility fixture; session warehouses stay read-only."""
    import duckdb
    import streamlit as st

    data = _load_data_module()
    st.cache_data.clear()
    st.cache_resource.clear()
    data._connection.clear()
    data.query.clear()
    data._table_exists.clear()
    with duckdb.connect() as connection:
        connection.execute("""
            create schema main_marts;
            create table main_marts.dim_registry_trial (
                nct_id varchar, current_overall_status varchar
            );
            create table main_marts.fct_registry_trial_site (
                nct_id varchar, snapshot_id varchar, state_normalized varchar
            );
            create table main_marts.mart_registry_site_overlap as
            select *, 'city' as city_normalized, 'NC' as state_normalized,
                   1 as recruiting_trial_count, 1 as listed_trial_count
            from (values
                ('adrd', date '2026-09-01', 'one'),
                ('adrd', date '2026-09-01', 'two'),
                ('adrd', date '2026-09-08', 'one'),
                ('adrd', date '2026-09-08', 'two'),
                ('oncology_nsclc', date '2026-09-15', 'three'),
                ('oncology_nsclc', date '2026-09-15', 'four'),
                ('oncology_nsclc', date '2026-09-15', 'five')
            ) as sites(preset_id, snapshot_date, facility_normalized);
            create table main_marts.fct_registry_trial_snapshot (
                snapshot_id varchar, nct_id varchar, snapshot_date date
            );
            insert into main_marts.fct_registry_trial_snapshot values
                ('snap-adrd-1', 'NCT00000001', date '2026-09-01'),
                ('snap-adrd-2', 'NCT00000001', date '2026-09-08'),
                ('snap-nsclc-1', 'NCT00000002', date '2026-09-15');
            create table main_marts.bridge_preset_trial (
                preset_id varchar, nct_id varchar, snapshot_date date,
                snapshot_id varchar, current_record_flag boolean
            );
            insert into main_marts.bridge_preset_trial values
                ('adrd', 'NCT00000001', date '2026-09-01', 'snap-adrd-1', false),
                ('adrd', 'NCT00000001', date '2026-09-08', 'snap-adrd-2', true),
                ('oncology_nsclc', 'NCT00000002', date '2026-09-15', 'snap-nsclc-1', true);
            create table main_marts.mart_registry_study_snapshot_audit as
            select snapshot_id, nct_id, snapshot_date
            from main_marts.fct_registry_trial_snapshot;
        """)
        monkeypatch.setattr(data, "_connection", lambda: connection)
        yield connection
    st.cache_data.clear()
    st.cache_resource.clear()


@pytest.mark.parametrize("profile, expected", [("adrd", 2), ("oncology_nsclc", 3)])
def test_overview_facilities_count_only_the_profiles_latest_capture(
    facility_snapshots, profile: str, expected: int
) -> None:
    data = _load_data_module()
    assert int(data.overview_metrics(preset_for(profile))["listed_facilities"]) == expected
    overlap = data.site_overlap(preset_for(profile))
    assert len(overlap) == expected
    observations = data.metric_audit_observations(preset_for(profile), "facility")
    assert _snapshot_dates(observations) == _snapshot_dates(overlap)
    assert set(observations.preset_id) == {profile}


@pytest.mark.parametrize(
    "reader", ["overview_metrics", "site_overlap", "metric_audit_observations"]
)
def test_facility_readers_do_not_fall_back_when_latest_capture_has_no_facilities(
    facility_snapshots, reader: str
) -> None:
    facility_snapshots.execute("""
        update main_marts.bridge_preset_trial
        set current_record_flag = false
        where preset_id = 'adrd';
        insert into main_marts.fct_registry_trial_snapshot
        values ('snap-adrd-3', 'NCT00000001', date '2026-09-22');
        insert into main_marts.bridge_preset_trial
        values ('adrd', 'NCT00000001', date '2026-09-22', 'snap-adrd-3', true);
    """)
    data = _load_data_module()
    if reader == "overview_metrics":
        assert int(data.overview_metrics(preset_for("adrd"))["listed_facilities"]) == 0
    elif reader == "site_overlap":
        assert data.site_overlap(preset_for("adrd")).empty
    else:
        observations = data.metric_audit_observations(preset_for("adrd"), "facility")
        assert _snapshot_dates(observations) == {"2026-09-22"}
        assert set(observations.preset_id) == {"adrd"}


def test_facility_readers_do_not_fall_back_when_current_capture_has_no_preset_members(
    facility_snapshots,
) -> None:
    facility_snapshots.execute("""
        update main_marts.bridge_preset_trial
        set current_record_flag = false
        where preset_id = 'adrd';
        insert into main_marts.fct_registry_trial_snapshot
        values ('snap-unassigned-1', 'NCT00000001', date '2026-09-22');
        insert into main_marts.bridge_preset_trial
        values ('unassigned', 'NCT00000001', date '2026-09-22', 'snap-unassigned-1', true);
    """)
    data = _load_data_module()
    assert int(data.overview_metrics(preset_for("adrd"))["listed_facilities"]) == 0
    assert data.site_overlap(preset_for("adrd")).empty
    assert data.metric_audit_observations(preset_for("adrd"), "facility").empty


def test_overview_facilities_are_zero_for_an_empty_profile(facility_snapshots) -> None:
    data = _load_data_module()
    assert int(data.overview_metrics(preset_for("full_catalog"))["listed_facilities"]) == 0
    assert data.site_overlap(preset_for("full_catalog")).empty
    assert data.metric_audit_observations(preset_for("full_catalog"), "facility").empty
