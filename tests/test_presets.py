"""Dashboard presets (#148): scope by registry condition node, add one by YAML alone.

Reads the session two-profile fixture warehouse. Readers are checked against the
#146 mart baseline (exact ids and counts, 1e-6 relative on rates), so a preset that
drops or adds a row relative to the profile-scoped marts fails here.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.presets import Preset, load_presets, preset_from_profile, registry_condition_key
from src.profiles import ProfileRegistry
from tests.mart_baseline import cells_match, load_baseline
from tests.preset_helpers import preset_for

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "dashboard") not in sys.path:
    sys.path.insert(0, str(ROOT / "dashboard"))

PRESET_IDS = ("adrd", "oncology_nsclc")
# reader name -> baseline mart it must reproduce (all `select *` of that mart).
BASELINE_READERS = {
    "priority_queue": "mart_feasibility_priority_queue",
    "recruiting_competition": "mart_recruiting_competition",
    "condition_geography_trends": "mart_condition_geography_trends",
    "site_overlap": "mart_site_overlap",
    "data_reliability": "mart_data_reliability",
}


@pytest.fixture(autouse=True)
def _readers_on_fixture_warehouse(fixture_project_root: Path, monkeypatch: pytest.MonkeyPatch):
    import streamlit as st

    from src.config import get_config

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(fixture_project_root))
    get_config.cache_clear()
    st.cache_data.clear()
    st.cache_resource.clear()
    yield
    st.cache_data.clear()
    st.cache_resource.clear()


def _data():
    from components import data

    return data


def _cell(value, sample):
    """A reader cell in the baseline's JSON form.

    `sample` is a non-null baseline value of the same column. pandas turns an int
    column holding a NULL into floats and dates into Timestamps; undo only that.
    """
    if value is None or (not isinstance(value, str | list) and pd.isna(value)):
        return None
    if hasattr(value, "item") and not isinstance(value, pd.Timestamp):
        value = value.item()
    if isinstance(value, pd.Timestamp):
        text = str(value)
        return text[:10] if isinstance(sample, str) and len(sample) == 10 else text
    if isinstance(value, float) and isinstance(sample, int) and value.is_integer():
        return int(value)
    return value if isinstance(value, bool | int | float | str) else str(value)


def test_registry_condition_key_matches_dim_condition(fixture_project_root: Path) -> None:
    """The python hash is the dbt hash: every `dim_condition` key is a preset node's key."""
    data = _data()
    dim = data.query(
        "select indication_profile_id, node_id, registry_condition_key "
        "from main_marts.dim_condition"
    )
    assert not dim.empty
    for row in dim.itertuples():
        assert row.registry_condition_key == registry_condition_key(row.node_id)
        assert row.node_id in preset_for(row.indication_profile_id).node_ids
        assert (
            row.registry_condition_key
            in preset_for(row.indication_profile_id).registry_condition_keys
        )


@pytest.mark.parametrize("preset_id", PRESET_IDS)
@pytest.mark.parametrize("reader", sorted(BASELINE_READERS))
def test_preset_results_equal_the_146_baseline(preset_id: str, reader: str) -> None:
    baseline = load_baseline()[BASELINE_READERS[reader]]
    columns = baseline["columns"]
    expected = sorted(
        (r for r in baseline["rows"] if r[columns.index("indication_profile_id")] == preset_id),
        key=lambda r: json.dumps(r, sort_keys=True),
    )
    assert expected, f"{reader}: the baseline holds no {preset_id} rows to compare"
    frame = getattr(_data(), reader)(preset_for(preset_id))
    samples = [
        next((r[i] for r in expected if r[i] is not None), None) for i in range(len(columns))
    ]
    got = sorted(
        (
            [_cell(row[c], samples[i]) for i, c in enumerate(columns)]
            for row in frame.astype(object).to_dict("records")
        ),
        key=lambda r: json.dumps(r, sort_keys=True),
    )
    assert len(got) == len(expected), f"{reader}({preset_id}): {len(got)} rows != {len(expected)}"
    for want, have in zip(expected, got, strict=True):
        assert all(cells_match(w, h) for w, h in zip(want, have, strict=True)), (
            f"{reader}({preset_id}): {want} != {have}"
        )


def test_trial_explorer_equals_baseline_trials() -> None:
    """Trial grain: the bridge join returns exactly the profile's baseline `dim_trial` rows."""
    baseline = load_baseline()["dim_trial"]
    cols = baseline["columns"]
    for preset_id in PRESET_IDS:
        want = {
            r[cols.index("nct_id")]
            for r in baseline["rows"]
            if r[cols.index("indication_profile_id")] == preset_id
        }
        got = set(_data().trial_explorer(preset_for(preset_id))["nct_id"])
        assert got == want
        assert _data().preset_trial_count(preset_for(preset_id)) == len(want)


# --- adding a preset is one YAML file ---------------------------------------------


def _write_profile(directory: Path, profile_id: str, taxonomy: str) -> None:
    """A new preset = a copy of a shipped profile YAML with its own id, name, taxonomy."""
    raw = yaml.safe_load((ROOT / "config/profiles/adrd.yml").read_text())
    raw["profile"].update(
        {"id": profile_id, "display_name": "Throwaway Preset", "taxonomy": taxonomy}
    )
    (directory / f"{profile_id}.yml").write_text(yaml.safe_dump(raw))


def _registry(directory: Path) -> ProfileRegistry:
    return ProfileRegistry(
        profiles_dir=directory, shared_paths_file=ROOT / "config/shared_paths.yml"
    )


def test_a_throwaway_preset_needs_only_a_yaml_file(tmp_path: Path) -> None:
    """A profile YAML nobody wired into code or dbt is selectable and reads real rows.

    The preset reuses the shipped ADRD taxonomy, so its nodes are group ids the fixture
    warehouse already carries; nothing in `dashboard/`, `src/` or `dbt_clinical_trials/`
    knows its id.
    """
    _write_profile(tmp_path, "throwaway_adrd", "config/condition_taxonomy.yml")
    [throwaway] = load_presets(_registry(tmp_path))
    assert (throwaway.preset_id, throwaway.name) == ("throwaway_adrd", "Throwaway Preset")
    assert throwaway.registry_condition_keys == preset_for("adrd").registry_condition_keys

    data = _data()
    assert len(data.trial_explorer(throwaway)) == len(data.trial_explorer(preset_for("adrd"))) > 0
    assert len(data.priority_queue(throwaway)) == len(data.priority_queue(preset_for("adrd"))) > 0
    # The profile-grain marts resolve through dim_condition by key, not by the new id.
    assert len(data.data_reliability(throwaway)) == len(data.data_reliability(preset_for("adrd")))
    assert (
        data.overview_metrics(throwaway)["total_trials"]
        == (data.overview_metrics(preset_for("adrd"))["total_trials"])
    )


def test_a_narrower_preset_reads_a_subset_of_the_nodes(tmp_path: Path) -> None:
    """Keeping one group of the ADRD taxonomy narrows trial- and condition-grain readers."""
    taxonomy = yaml.safe_load((ROOT / "config/condition_taxonomy.yml").read_text())
    wanted = "alzheimers_disease"
    taxonomy["groups"] = [g for g in taxonomy["groups"] if g["condition_group"] == wanted]
    taxonomy_path = tmp_path / "taxonomy_only_alzheimers.yml"
    taxonomy_path.write_text(yaml.safe_dump(taxonomy))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    _write_profile(profiles, "throwaway_alz", str(taxonomy_path))
    [narrow] = load_presets(_registry(profiles))
    assert narrow.node_ids == (f"group:{wanted}", f"group:{taxonomy['default']['condition_group']}")

    kept = {wanted, taxonomy["default"]["condition_group"]}
    data = _data()
    full_queue = data.priority_queue(preset_for("adrd"))
    queue = data.priority_queue(narrow)
    assert set(queue["condition_group"]) <= kept
    assert len(queue) == int(full_queue["condition_group"].isin(kept).sum()) > 0
    assert len(queue) < len(full_queue)
    assert data.preset_trial_count(narrow) < data.preset_trial_count(preset_for("adrd"))


def test_every_shipped_profile_yields_a_preset() -> None:
    presets = load_presets()
    assert {p.preset_id for p in presets} >= set(PRESET_IDS)
    for p in presets:
        assert isinstance(p, Preset) and p.node_ids and len(p.node_ids) == len(set(p.node_ids))
        assert p.registry_condition_keys == tuple(registry_condition_key(n) for n in p.node_ids)
    adrd = preset_for("adrd")
    assert "group:alzheimers_disease" in adrd.node_ids
    assert adrd.study_type == "INTERVENTIONAL" and adrd.statuses
    assert preset_from_profile(ProfileRegistry().get("adrd")) == adrd


def test_the_double_count_caption_is_gone() -> None:
    from streamlit.testing.v1 import AppTest

    def page():
        from components.profile import render_preset_selector

        render_preset_selector()

    at = AppTest.from_function(page).run()
    assert not at.exception, at.exception
    selector = next(w for w in at.sidebar.selectbox if w.key == "indication_profile_selector")
    assert selector.label == "Indication preset"
    captions = " ".join(str(c.value) for c in at.sidebar.caption).lower()
    assert "additive" not in captions and "counted once" not in captions
    assert "default filters" in captions
