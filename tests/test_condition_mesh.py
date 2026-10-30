"""Issue #52: registry MeSH condition identity, YAML overlays optional.

Unit tests cover the flattener (direct/ancestor extraction, deduplication,
missing/empty derived sections, branch semantics, taxonomy-less fallback).
The end-to-end test builds one dbt warehouse from tests/fixtures/
condition_mesh.json and proves the acceptance criteria: an unseen MeSH
condition is browsable in dim_condition with no config file, ancestor closure
lands in the bridge, overlay group rows keep their pre-MeSH semantics, and
similarity inputs exclude ancestor edges.
"""

import os
from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.transform.export_parquet import export_entity
from src.transform.flatten_studies import (
    UNMAPPED_CONDITION_GROUP,
    extract_condition_mesh,
    flatten_study,
)
from src.transform.normalize_conditions import get_taxonomy
from src.transform.normalize_locations import get_geography_rules
from tests.conftest import FIXTURE_SNAPSHOT_DAY
from tests.fixture_warehouse import FixtureRun, build_fixture_warehouse

TAXONOMY = get_taxonomy()
GEOGRAPHY = get_geography_rules()

MESH_RUN_ID = "20260901T130000Z_fixturemesh"


def make_mesh_study(nct_id: str, derived: dict | None) -> dict:
    """Minimal bronze-shaped study; `derived` becomes derivedSection verbatim."""
    study: dict = {
        "protocolSection": {
            "identificationModule": {"nctId": nct_id},
            "conditionsModule": {"conditions": ["Alzheimer Disease", "Friedreich Ataxia"]},
        },
        "hasResults": False,
    }
    if derived is not None:
        study["derivedSection"] = derived
    return study


CONDITION_MODULE = {
    "meshes": [
        {"id": "D000544", "term": "Alzheimer Disease"},
        {"id": "D003072", "term": "Cognition Disorders"},
    ],
    "ancestors": [
        {"id": "D001523", "term": "Mental Disorders"},
        # D000544 is also an ancestor here: direct must win in the flatten.
        {"id": "D000544", "term": "Alzheimer Disease"},
        {"id": "D003072", "term": "Cognition Disorders"},
    ],
}


# --- flattener ---------------------------------------------------------------


def test_extract_dedupes_and_direct_wins_over_ancestor():
    study = make_mesh_study("NCT1", {"conditionBrowseModule": CONDITION_MODULE})
    rows = extract_condition_mesh(study)
    assert [(r["id"], r["is_ancestor"]) for r in rows] == [
        ("D000544", False),
        ("D003072", False),
        ("D001523", True),
    ]


def test_extract_handles_missing_empty_and_malformed_modules():
    # No derivedSection at all (new/unprocessed records).
    assert extract_condition_mesh(make_mesh_study("NCT2", None)) == []
    # Module present but empty.
    assert extract_condition_mesh(make_mesh_study("NCT3", {"conditionBrowseModule": {}})) == []
    # Rows without an id or a term are not browsable identities; drop them.
    partial = {"conditionBrowseModule": {"meshes": [{"term": "Orphan"}, {"id": "D000000"}]}}
    assert extract_condition_mesh(make_mesh_study("NCT4", partial)) == []


def test_flatten_emits_mesh_rows_with_null_branch():
    rows = flatten_study(
        make_mesh_study("NCT5", {"conditionBrowseModule": CONDITION_MODULE}),
        "run1",
        "2026-10-30T00:00:00+00:00",
        TAXONOMY,
        GEOGRAPHY,
    )
    mesh = rows["silver_trial_condition_mesh"]
    assert {r["branch"] for r in mesh} == {None}
    assert {r["mesh_id"] for r in mesh} == {"D000544", "D003072", "D001523"}
    direct = {r["mesh_id"] for r in mesh if not r["is_ancestor"]}
    assert direct == {"D000544", "D003072"}


def test_flatten_condition_rows_carry_mapping_source():
    rows = flatten_study(
        make_mesh_study("NCT6", {"conditionBrowseModule": CONDITION_MODULE}),
        "run1",
        "2026-10-30T00:00:00+00:00",
        TAXONOMY,
        GEOGRAPHY,
    )
    by_raw = {r["condition_raw"]: r for r in rows["silver_trial_conditions"]}
    # "Alzheimer Disease" matches the overlay...
    assert by_raw["Alzheimer Disease"]["mapping_source"] == "yaml_overlay"
    assert by_raw["Alzheimer Disease"]["condition_group"] == "alzheimers_disease"
    # ...while "Friedreich Ataxia" matches no taxonomy and falls back.
    assert by_raw["Friedreich Ataxia"]["mapping_source"] == "text_fallback"
    assert by_raw["Friedreich Ataxia"]["condition_group"] == "non_dementia_other"


def test_flatten_without_taxonomy_still_normalizes_text_and_falls_back():
    rows = flatten_study(
        make_mesh_study("NCT7", {"conditionBrowseModule": CONDITION_MODULE}),
        "run1",
        "2026-10-30T00:00:00+00:00",
        None,
        GEOGRAPHY,
    )
    assert len(rows["silver_trial_conditions"]) == 2
    for row in rows["silver_trial_conditions"]:
        assert row["mapping_source"] == "text_fallback"
        assert row["condition_group"] == UNMAPPED_CONDITION_GROUP
        assert row["condition_normalized"] is not None
    assert len(rows["silver_trial_condition_mesh"]) == 3


def test_mesh_rows_round_trip_through_parquet_as_boolean(tmp_path):
    rows = flatten_study(
        make_mesh_study("NCT8", {"conditionBrowseModule": CONDITION_MODULE}),
        "run1",
        "2026-10-30T00:00:00+00:00",
        TAXONOMY,
        GEOGRAPHY,
    )
    path, count = export_entity(
        rows["silver_trial_condition_mesh"], "silver_trial_condition_mesh", "run1", tmp_path
    )
    assert count == 3
    frame = pd.read_parquet(path)
    assert frame["is_ancestor"].dtype == bool
    assert frame["mesh_term"].tolist().count("Alzheimer Disease") == 1


# --- end-to-end dbt warehouse ------------------------------------------------


def mesh_run() -> FixtureRun:
    return FixtureRun(
        profile="adrd",
        run_id=MESH_RUN_ID,
        started_at=FIXTURE_SNAPSHOT_DAY + timedelta(hours=13),
        condition="Alzheimer Disease",
        fixture_dir="condition_mesh",
    )


@pytest.fixture(scope="module")
def mesh_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One warehouse: a single ADRD run over the mesh fixture page."""
    return build_fixture_warehouse(tmp_path_factory.mktemp("mesh_warehouse"), runs=[mesh_run()])


def query(root: Path, sql: str) -> list[tuple]:
    # Staging models are views over relative globs resolved against the
    # process cwd (same mechanism as test_dbt_fixture_build._rows).
    con = duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True)
    start = Path(os.getcwd())
    try:
        os.chdir(root)
        return con.execute(sql).fetchall()
    finally:
        os.chdir(start)
        con.close()


def test_unseen_mesh_condition_browsable_without_config(mesh_warehouse: Path):
    # Friedreich Ataxia (D005662) appears in no YAML taxonomy; its MeSH node
    # is still in dim_condition with a trial count — no new config file.
    rows = query(
        mesh_warehouse,
        """
        select node_id, condition_normalized, trial_count, mapping_source, is_ancestor
        from main_marts.dim_condition
        where node_id = 'D005662'
        """,
    )
    assert rows == [("D005662", "Friedreich Ataxia", 1, "mesh", False)]


def test_ancestor_closure_lands_in_dim_and_bridge(mesh_warehouse: Path):
    # D009422 Nervous System Diseases is an ancestor of both mesh-fixture
    # trials with a derived section; every edge is a closure edge.
    rows = query(
        mesh_warehouse,
        """
        select node_id, trial_count, is_ancestor from main_marts.dim_condition
        where node_id = 'D009422'
        """,
    )
    assert rows == [("D009422", 2, True)]
    # D010300 Parkinson Disease is applied directly to both trials, so the
    # node is not ancestor-only even though it is also an ancestor elsewhere.
    rows = query(
        mesh_warehouse,
        """
        select node_id, trial_count, is_ancestor from main_marts.dim_condition
        where node_id = 'D010300'
        """,
    )
    assert rows == [("D010300", 2, False)]
    # The bridge carries the per-trial closure edges.
    rows = query(
        mesh_warehouse,
        """
        select count(*), count(*) filter (where is_ancestor)
        from main_marts.bridge_trial_condition
        where nct_id = 'NCT99000001'
        """,
    )
    # 1 group row + 4 direct + 14 ancestor mesh rows.
    assert rows == [(19, 14)]


def test_overlay_group_nodes_keep_taxonomy_semantics(mesh_warehouse: Path):
    rows = query(
        mesh_warehouse,
        """
        select node_id, condition_group, mapping_source, trial_count
        from main_marts.dim_condition
        where mapping_source != 'mesh'
        order by node_id
        """,
    )
    assert rows == [
        ("group:alzheimers_disease", "alzheimers_disease", "yaml_overlay", 1),
        ("group:frontotemporal_dementia", "frontotemporal_dementia", "yaml_overlay", 1),
        ("group:non_dementia_other", "non_dementia_other", "text_fallback", 1),
    ]
    # The trial without a derived section is flagged only by its fallback row.
    rows = query(
        mesh_warehouse,
        """
        select count(*) from main_marts.bridge_trial_condition
        where nct_id = 'NCT99000003'
        """,
    )
    assert rows == [(1,)]


def test_trial_without_derived_section_has_no_mesh_rows(mesh_warehouse: Path):
    rows = query(
        mesh_warehouse,
        "select count(*) from main_staging.stg_trial_condition_mesh where nct_id = 'NCT99000003'",
    )
    assert rows == [(0,)]


def test_similarity_inputs_exclude_ancestor_edges(mesh_warehouse: Path):
    # Direct terms are in the comparability identity (by label); ancestors
    # are not: NCT99000001 carries "Alzheimer Disease" directly while
    # "Cognition Disorders" is reachable only as an ancestor.
    rows = query(
        mesh_warehouse,
        """
        select
            list_contains(condition_groups, 'Alzheimer Disease'),
            list_contains(condition_groups, 'Cognition Disorders')
        from main_intermediate.int_trial_comparability_features
        where nct_id = 'NCT99000001'
        """,
    )
    assert rows == [(True, False)]
    # The two mesh trials share D010300 directly but sit in different phases,
    # so no (condition node, phase) token is shared: no candidate pairs.
    rows = query(
        mesh_warehouse,
        "select count(*) from main_marts.mart_trial_similarity",
    )
    assert rows == [(0,)]


def test_group_counts_unchanged_by_mesh_rows(mesh_warehouse: Path):
    # fct_trial_snapshot reads the group grain (int_trial_condition_mapping),
    # which mesh rows never touch: one condition_group per group-mapped trial.
    rows = query(
        mesh_warehouse,
        """
        select nct_id, condition_group_count from main_marts.fct_trial_snapshot
        order by nct_id
        """,
    )
    assert rows == [
        ("NCT99000001", 1),
        ("NCT99000002", 1),
        ("NCT99000003", 1),
    ]
