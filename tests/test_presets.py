"""Dashboard presets (#148): scope by registry condition node, add one by YAML alone.

Reads the session two-profile fixture warehouse. Readers are checked against the
#146 mart baseline (exact ids and counts, 1e-6 relative on rates), so a preset that
drops or adds a row relative to the profile-scoped marts fails here.
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import duckdb
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
    return ProfileRegistry(profiles_dir=directory)


@pytest.fixture
def warehouse_copy(
    fixture_project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """A private copy of the fixture warehouse the test may write to; readers point at it."""
    import streamlit as st

    from src.config import get_config

    root = tmp_path / "root"
    shutil.copytree(fixture_project_root, root)
    monkeypatch.setenv("CTI_PROJECT_ROOT", str(root))
    get_config.cache_clear()
    st.cache_data.clear()
    st.cache_resource.clear()
    return root / "data/warehouse/clinical_trials.duckdb"


@pytest.fixture
def warehouse_with_second_adrd(warehouse_copy: Path) -> Path:
    """`throwaway_adrd` is a refreshed profile reusing the ADRD taxonomy.

    Every base table with a profile column gets the ADRD rows again under the new id
    (trial keys suffixed, so they stay per-profile as md5(nct_id, profile) is). This is
    what the warehouse holds once a YAML-only preset has been refreshed. This fixture
    tests reader isolation only; shared_trial_project_root tests actual key generation.
    """
    with duckdb.connect(str(warehouse_copy)) as con:
        tables = con.execute(
            "select c.table_schema, c.table_name from information_schema.columns c "
            "join information_schema.tables t using (table_schema, table_name) "
            "where c.column_name = 'indication_profile_id' and t.table_type = 'BASE TABLE' "
            "and c.table_schema in ('main_marts', 'main_intermediate')"
        ).fetchall()
        for schema, table in tables:
            cols = {r[0] for r in con.execute(f"describe {schema}.{table}").fetchall()}
            swap = "'throwaway_adrd' as indication_profile_id"
            if "trial_key" in cols:
                swap += ", trial_key || '_t' as trial_key"
            con.execute(
                f"insert into {schema}.{table} select * replace ({swap}) "
                f"from {schema}.{table} where indication_profile_id = 'adrd'"
            )
    return warehouse_copy


PROFILE_FRAME_READERS = (
    "priority_queue",
    "recruiting_competition",
    "condition_geography_trends",
    "access_rates",
    "participation_prevalence",
    "site_overlap",
    "sponsor_landscape",
    "trial_explorer",
    "data_reliability",
    "early_stop_transparency",
    "results_posting_transparency",
)


def _adrd_baseline_trials() -> int:
    baseline = load_baseline()["dim_trial"]
    pid = baseline["columns"].index("indication_profile_id")
    return sum(1 for r in baseline["rows"] if r[pid] == "adrd")


def _assert_reads_only(preset: Preset) -> None:
    """Every reader returns this preset's profile alone, and no trial twice."""
    data = _data()
    for reader in PROFILE_FRAME_READERS:
        frame = getattr(data, reader)(preset)
        if "indication_profile_id" in frame.columns:
            assert set(frame["indication_profile_id"]) <= {preset.profile_id}, reader
    explorer = data.trial_explorer(preset)
    assert explorer["nct_id"].is_unique
    assert len(explorer) == data.preset_trial_count(preset) == _adrd_baseline_trials()
    assert int(data.overview_metrics(preset)["total_trials"]) == len(explorer)


def test_a_throwaway_preset_needs_only_a_yaml_file(
    tmp_path: Path, warehouse_with_second_adrd: Path
) -> None:
    """A YAML nobody wired into code or dbt, once refreshed, reads its own rows.

    The preset reuses the shipped ADRD taxonomy, so `dim_condition` now holds the same
    node keys under `adrd` and `throwaway_adrd`. Each preset must read only its own
    profile: the ADRD figures may not grow, and the new preset must not read ADRD's.
    """
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    _write_profile(profiles, "throwaway_adrd", "config/condition_taxonomy.yml")
    [throwaway] = load_presets(_registry(profiles))
    assert (throwaway.preset_id, throwaway.name) == ("throwaway_adrd", "Throwaway Preset")
    assert throwaway.registry_condition_keys == preset_for("adrd").registry_condition_keys

    data = _data()
    # Both profiles are really in the warehouse, or the checks below prove nothing.
    profiles_present = set(
        data.query("select distinct indication_profile_id from main_marts.dim_trial")[
            "indication_profile_id"
        ]
    )
    assert {"adrd", "throwaway_adrd"} <= profiles_present
    _assert_reads_only(preset_for("adrd"))
    _assert_reads_only(throwaway)
    assert set(data.data_reliability(throwaway)["indication_profile_id"]) == {"throwaway_adrd"}
    assert len(data.priority_queue(throwaway)) == len(data.priority_queue(preset_for("adrd"))) > 0


def test_shared_trials_keep_one_registry_identity_across_presets(
    shared_trial_project_root: Path,
) -> None:
    """Independent transforms must produce the NCT hash for both non-null keys.

    - Fails on missing profile rows, NULL keys or a profile-dependent dbt hash.
    - Synthetic snapshots test identity only, not live-registry selection parity.
    """
    # 1. Read actual dbt output rather than the copied-row reader-isolation fixture.
    warehouse = shared_trial_project_root / "data/warehouse/clinical_trials.duckdb"
    with duckdb.connect(str(warehouse), read_only=True) as con:
        rows = con.execute(
            "select nct_id, indication_profile_id, registry_trial_key, trial_key "
            "from main_marts.dim_trial order by nct_id, indication_profile_id"
        ).fetchall()
    assert {nct_id for nct_id, *_ in rows} == {f"NCT{i:08d}" for i in range(1, 11)}
    # 2. Check each row explicitly: count(distinct) alone silently ignores NULL.
    for nct_id in sorted({row[0] for row in rows}):
        shared = [row for row in rows if row[0] == nct_id]
        assert len(shared) == 2
        assert {row[1] for row in shared} == {"adrd", "oncology_nsclc"}
        expected = hashlib.md5(nct_id.encode()).hexdigest()
        assert [row[2] for row in shared] == [expected, expected]
        assert len({row[3] for row in shared}) == 2


def test_bridge_preset_trial_membership_and_scopes(
    shared_trial_project_root: Path,
) -> None:
    """Preset membership counts each trial once per preset and once in the registry union.

    Tests shared-node, no-MeSH, and distinct-snapshot cases.
    """
    warehouse = shared_trial_project_root / "data/warehouse/clinical_trials.duckdb"
    with duckdb.connect(str(warehouse), read_only=True) as con:
        # 1. Snapshot currency case:
        # In shared_trial_runs(), all 10 trials run through 2026-09-15.
        # Verify current_record_flag selects the latest snapshot_date per trial
        # and marks earlier runs false.
        nct1_current_dates = set(
            con.execute(
                "select distinct strftime(snapshot_date, '%Y-%m-%d') "
                "from main_marts.bridge_preset_trial "
                "where nct_id = 'NCT00000001' and current_record_flag = true"
            ).fetchall()
        )
        assert nct1_current_dates == {("2026-09-15",)}

        nct1_stale_dates = set(
            con.execute(
                "select distinct strftime(snapshot_date, '%Y-%m-%d') "
                "from main_marts.bridge_preset_trial "
                "where nct_id = 'NCT00000001' and current_record_flag = false"
            ).fetchall()
        )
        assert nct1_stale_dates == {("2026-09-01",), ("2026-09-08",)}

        # 2. Shared-node case: one MeSH node ('Alzheimer Disease', D000544) shared
        # by two trials (NCT00000001, NCT00000002) and by two presets (adrd, shared_preset).
        shared_trials = con.execute(
            "select preset_id, nct_id, is_unassigned, membership_scope "
            "from main_marts.bridge_preset_trial "
            "where nct_id in ('NCT00000001', 'NCT00000002') and current_record_flag = true "
            "order by nct_id, preset_id"
        ).fetchall()
        assert [(r[0], r[1], r[2], r[3]) for r in shared_trials] == [
            ("adrd", "NCT00000001", False, "preset"),
            ("shared_preset", "NCT00000001", False, "preset"),
            ("adrd", "NCT00000002", False, "preset"),
            ("shared_preset", "NCT00000002", False, "preset"),
        ]

        # 3. No-MeSH / unmapped case: NCT00000010
        # NCT00000010 deliberately has no condition/MeSH.
        # It must NOT be in adrd or oncology_nsclc, but MUST be in unassigned.
        unmapped_preset_rows = con.execute(
            "select preset_id, nct_id, is_unassigned, membership_scope "
            "from main_marts.bridge_preset_trial "
            "where nct_id = 'NCT00000010' and current_record_flag = true"
        ).fetchall()
        assert len(unmapped_preset_rows) == 1
        assert unmapped_preset_rows[0] == ("unassigned", "NCT00000010", True, "unassigned")

        # 4. Each trial counted once per preset:
        # Check uniqueness of (preset_id, nct_id) among current records
        dup_check = con.execute(
            "select preset_id, nct_id, count(*) as c "
            "from main_marts.bridge_preset_trial "
            "where current_record_flag = true "
            "group by preset_id, nct_id having count(*) > 1"
        ).fetchall()
        assert not dup_check

        # 5. Registry union: each trial counted once in registry union
        # dim_registry_trial has exactly one row per NCT ID (all 10 trials NCT00000001..NCT00000010)
        reg_trials = con.execute(
            "select nct_id, registry_trial_key from main_marts.dim_registry_trial"
        ).fetchall()
        assert len(reg_trials) == 10
        assert {r[0] for r in reg_trials} == {f"NCT{i:08d}" for i in range(1, 11)}

        # And union of distinct NCT IDs across bridge_preset_trial equals dim_registry_trial
        bridge_union_trials = set(
            con.execute(
                "select distinct nct_id from main_marts.bridge_preset_trial "
                "where current_record_flag = true"
            ).fetchall()
        )
        assert bridge_union_trials == {(r[0],) for r in reg_trials}

        # 6. Check bridge_registry_trial_condition and dim_registry_condition:
        # NCT00000001 links to condition nodes (D000544 and ancestor D001523)
        nct1_conditions = con.execute(
            "select b.node_id, d.mapping_source, d.condition_display_name "
            "from main_marts.bridge_registry_trial_condition b "
            "join main_marts.dim_registry_condition d "
            "  on b.registry_condition_key = d.registry_condition_key "
            "where b.nct_id = 'NCT00000001'"
        ).fetchall()
        assert any("D000544" in r[0] for r in nct1_conditions)
        # NCT00000010 has no conditions in bridge
        assert (
            con.execute(
                "select count(*) from main_marts.bridge_registry_trial_condition "
                "where nct_id = 'NCT00000010'"
            ).fetchone()[0]
            == 0
        )


def test_bridge_preset_trial_distinct_snapshots(tmp_path: Path) -> None:
    """Distinct-snapshot case: each trial's currency tracks its own latest snapshot."""
    from dataclasses import replace
    from datetime import UTC, datetime

    from tests.fixture_warehouse import (
        AddedStudy,
        base_adrd_run,
        base_nsclc_run,
        build_fixture_warehouse,
    )

    run1 = replace(
        base_adrd_run(),
        started_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
        run_id="20260901T100000Z_run1",
        added=(
            AddedStudy(
                nct_id="NCT00000088",
                clone_of="NCT00000001",
                overall_status="RECRUITING",
                condition="Alzheimer Disease",
            ),
        ),
    )
    run2 = replace(
        base_nsclc_run(),
        started_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
        run_id="20260908T100000Z_run2",
    )
    root = tmp_path / "distinct_snapshots_root"
    build_fixture_warehouse(root, runs=[run1, run2])

    warehouse = root / "data/warehouse/clinical_trials.duckdb"
    with duckdb.connect(str(warehouse), read_only=True) as con:
        # NCT00000088 only exists on 2026-09-01: its current record is on 2026-09-01
        nct88_current = con.execute(
            "select distinct strftime(snapshot_date, '%Y-%m-%d') "
            "from main_marts.bridge_preset_trial "
            "where nct_id = 'NCT00000088' and current_record_flag = true"
        ).fetchall()
        assert nct88_current == [("2026-09-01",)]

        # NCT00000001 exists on both dates: its current record is on 2026-09-08
        nct1_current = con.execute(
            "select distinct strftime(snapshot_date, '%Y-%m-%d') "
            "from main_marts.bridge_preset_trial "
            "where nct_id = 'NCT00000001' and current_record_flag = true"
        ).fetchall()
        assert nct1_current == [("2026-09-08",)]

        # And NCT00000001 on 2026-09-01 is marked historical
        nct1_stale = con.execute(
            "select distinct strftime(snapshot_date, '%Y-%m-%d') "
            "from main_marts.bridge_preset_trial "
            "where nct_id = 'NCT00000001' and current_record_flag = false"
        ).fetchall()
        assert nct1_stale == [("2026-09-01",)]


def test_a_narrower_preset_reads_a_subset_of_the_nodes(
    tmp_path: Path, warehouse_with_second_adrd: Path
) -> None:
    """Keeping one group of the ADRD taxonomy narrows trial- and condition-grain readers."""
    taxonomy = yaml.safe_load((ROOT / "config/condition_taxonomy.yml").read_text())
    wanted = "alzheimers_disease"
    taxonomy["groups"] = [g for g in taxonomy["groups"] if g["condition_group"] == wanted]
    taxonomy_path = tmp_path / "taxonomy_only_alzheimers.yml"
    taxonomy_path.write_text(yaml.safe_dump(taxonomy))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    _write_profile(profiles, "throwaway_adrd", str(taxonomy_path))
    [narrow] = load_presets(_registry(profiles))
    assert narrow.node_ids == (f"group:{wanted}", f"group:{taxonomy['default']['condition_group']}")

    kept = {wanted, taxonomy["default"]["condition_group"]}
    data = _data()
    full_queue = data.priority_queue(preset_for("adrd"))
    queue = data.priority_queue(narrow)
    assert set(queue["condition_group"]) <= kept
    assert len(queue) == int(full_queue["condition_group"].isin(kept).sum()) > 0
    assert len(queue) < len(full_queue)
    assert set(queue["indication_profile_id"]) == {"throwaway_adrd"}
    assert data.preset_trial_count(narrow) < data.preset_trial_count(preset_for("adrd"))


def test_a_preset_cannot_blend_profiles() -> None:
    """The review repro: one ADRD group plus one NSCLC group in a single preset.

    Nodes alone do not say which profile owns a row, so the readers bind the owning
    profile too. The NSCLC node adds nothing to an ADRD preset: no NSCLC rows, no
    trial counted once per profile.
    """
    mixed = Preset(
        "adrd",
        "Mixed",
        ("group:alzheimers_disease", "group:other_thoracic_oncology"),
        tuple(
            registry_condition_key(n)
            for n in ("group:alzheimers_disease", "group:other_thoracic_oncology")
        ),
        (),
        "",
        (),
        (),
    )
    data = _data()
    for reader in PROFILE_FRAME_READERS:
        frame = getattr(data, reader)(mixed)
        if "indication_profile_id" in frame.columns:
            assert set(frame["indication_profile_id"]) <= {"adrd"}, reader
    explorer = data.trial_explorer(mixed)
    assert explorer["nct_id"].is_unique
    assert set(explorer["indication_profile_id"]) == {"adrd"}
    assert int(data.overview_metrics(mixed)["total_trials"]) == len(explorer)
    assert len(explorer) <= _adrd_baseline_trials()


def test_trials_without_a_condition_stay_in_their_profile(warehouse_copy: Path) -> None:
    """A trial with no mapped condition has no bridge row; the #146 baseline counted it."""
    with duckdb.connect(str(warehouse_copy)) as con:
        [(trial_key, nct_id)] = con.execute(
            "select trial_key, nct_id from main_marts.dim_trial "
            "where indication_profile_id = 'adrd' order by nct_id limit 1"
        ).fetchall()
        con.execute(
            "delete from main_marts.bridge_trial_condition where trial_key = ?", [trial_key]
        )
    data = _data()
    for preset_id in PRESET_IDS:
        preset = preset_for(preset_id)
        want = (
            _adrd_baseline_trials()
            if preset_id == "adrd"
            else int(
                data.query(
                    "select count(*) as n from main_marts.dim_trial "
                    "where indication_profile_id = 'oncology_nsclc'"
                ).iloc[0]["n"]
            )
        )
        explorer = data.trial_explorer(preset)
        assert len(explorer) == data.preset_trial_count(preset) == want
        assert int(data.overview_metrics(preset)["total_trials"]) == want
    assert nct_id in set(data.trial_explorer(preset_for("adrd"))["nct_id"])


def test_same_timestamp_conflicting_payloads_fails_build(tmp_path: Path) -> None:
    """Ties on started_at_utc with conflicting payloads must fail the build.

    Identical payloads coalesce without error.
    """
    from dataclasses import replace

    from tests.fixture_warehouse import (
        base_adrd_run,
        build_dbt_fixture,
        build_fixture_warehouse,
        prepare_fixture_root,
    )

    base = base_adrd_run()
    # 1. Identical payloads at same started_at_utc coalesce without failure
    run1 = replace(base, run_id="20260901T100000Z_run1")
    run2 = replace(base, run_id="20260901T100000Z_run2")
    coalesce_root = tmp_path / "coalesce_root"
    build_fixture_warehouse(coalesce_root, runs=[run1, run2])

    # 2. Conflicting payloads at same started_at_utc fail with NCT ID, run IDs and hashes
    conflict_run2 = replace(
        base,
        run_id="20260901T100000Z_run2",
        status_overrides={"NCT00000001": "COMPLETED"},
    )
    conflict_root = tmp_path / "conflict_root"
    from src.transform.build_silver_entities import CanonicalConflictError

    try:
        prepare_fixture_root(conflict_root, runs=[run1, conflict_run2])
        with pytest.raises(RuntimeError) as exc_info:
            build_dbt_fixture(conflict_root)
        err_msg = str(exc_info.value)
        assert "assert_no_same_timestamp_conflicts" in err_msg
        assert "NCT00000001" in err_msg
    except CanonicalConflictError as exc:
        err_msg = str(exc)
        assert "NCT00000001" in err_msg
        assert "20260901T100000Z_run2" in err_msg


def test_a_preset_with_unknown_nodes_is_told_apart_from_an_unrefreshed_one() -> None:
    """A refreshed profile whose nodes are all absent is a taxonomy problem, not a missing run."""
    data = _data()
    node = "group:no_such_group"
    typo = Preset("adrd", "Typo", (node,), (registry_condition_key(node),), (), "", (), ())
    assert data._nodes_missing(typo) is True
    assert data._nodes_missing(preset_for("adrd")) is False
    # No dim_condition rows for the profile at all: the "not yet refreshed" state.
    assert data._nodes_missing(preset_for("full_catalog")) is False


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
