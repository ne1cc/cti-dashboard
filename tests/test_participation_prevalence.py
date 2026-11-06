"""Issue #75: participation-to-prevalence ratio mart, burden shares and the page helper.

The end-to-end warehouse is built from tests/fixtures/bronze_snapshot_ratio: seven
completed NSCLC trials with posted baseline results (five in 2020-2024, two in
2015-2019) and three Alzheimer trials. NCT99750001 also reports a "Total" group that
must not be counted twice; NCT99750005 reports sex only. Expected figures below are
summed by hand from that fixture.
"""

import csv
import os
import sys
from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.ingest import burden_extracts
from tests.conftest import FIXTURE_SNAPSHOT_DAY
from tests.fixture_warehouse import FixtureRun, build_fixture_warehouse

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "dbt_clinical_trials" / "seeds"

sys.path.insert(0, str(ROOT / "dashboard"))
from components.participation_prevalence import (  # noqa: E402
    COMPOSITION_ONLY,
    LOW_N,
    SUPPRESSED,
    ratio_view,
)


def _seed(name: str) -> list[dict[str, str]]:
    with (SEEDS / f"{name}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --- burden shares ------------------------------------------------------------------


def test_parse_scp_us_count_reads_the_aggregate_row() -> None:
    text = (ROOT / "tests/fixtures/burden/scp_lung_sample.csv").read_bytes().decode("latin-1")
    assert burden_extracts.parse_scp_us_count(text) == 225094


@pytest.mark.parametrize(
    "text", ["", '"US (SEER+NPCR)",00000,51.8,51.8,51.9,N/A,N/A,N/A,*,falling,-2.9,-3.4,-2.3']
)
def test_parse_scp_us_count_refuses_a_missing_or_suppressed_total(text: str) -> None:
    with pytest.raises(ValueError):
        burden_extracts.parse_scp_us_count(text)


def test_burden_shares_cover_each_dimension_once_and_match_the_us_total() -> None:
    rows = _seed("burden_demographic_share")
    by_dim: dict[str, int] = {}
    for r in rows:
        by_dim[r["dimension"]] = by_dim.get(r["dimension"], 0) + int(r["annual_cases"])
    assert {r["burden_source_id"] for r in rows} == {"scp_lung_state"}
    assert by_dim["sex"] == 225094  # the all-races, both-sexes US count
    # Hispanic origin leaves out cases of unknown origin, so it sums to slightly less.
    assert 0.99 * 225094 < by_dim["ethnicity"] <= 225094


# --- end to end ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def mart(tmp_path_factory: pytest.TempPathFactory) -> pd.DataFrame:
    run = FixtureRun(
        profile="adrd",
        run_id="20260901T150000Z_fixtureratio",
        started_at=FIXTURE_SNAPSHOT_DAY + timedelta(hours=15),
        condition="Alzheimer Disease",
        fixture_dir="bronze_snapshot_ratio",
    )
    root = build_fixture_warehouse(tmp_path_factory.mktemp("ratio_warehouse"), runs=[run])
    con = duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True)
    start = Path(os.getcwd())
    try:
        os.chdir(root)
        return con.execute("select * from main_marts.mart_participation_prevalence_ratio").df()
    finally:
        os.chdir(start)
        con.close()


def _row(mart: pd.DataFrame, mesh_id: str, window: str, dimension: str, group: str) -> pd.Series:
    hit = mart[
        (mart["mesh_id"] == mesh_id)
        & (mart["time_window"] == window)
        & (mart["dimension"] == dimension)
        & (mart["group_label"] == group)
    ]
    assert len(hit) == 1
    return hit.iloc[0]


def test_nsclc_sex_ratio_counts_arms_once_and_ignores_the_total_group(mart) -> None:
    female = _row(mart, "D002289", "2020-2024", "sex", "Female")
    male = _row(mart, "D002289", "2020-2024", "sex", "Male")
    # Arms only: 58+45+22+85+29 female, 82+65+34+115+42 male. NCT99750001's Total group
    # (58 / 82) would add 58 and 82 if it were counted.
    assert (female["group_participants"], male["group_participants"]) == (239, 338)
    assert female["dimension_participants"] == 577
    burden = {r["group_label"]: int(r["annual_cases"]) for r in _seed("burden_demographic_share")}
    expected_burden_share = burden["Female"] / (burden["Female"] + burden["Male"])
    assert female["burden_share"] == pytest.approx(expected_burden_share)
    assert female["participation_prevalence_ratio"] == pytest.approx(
        (239 / 577) / expected_burden_share
    )
    assert (
        female["ratio_ci_low"] < female["participation_prevalence_ratio"] < female["ratio_ci_high"]
    )
    assert female["comparison_basis"] == "participation_vs_burden"


def test_ratio_row_shows_trials_reporting_share_and_burden_source(mart) -> None:
    sex = _row(mart, "D002289", "2020-2024", "sex", "Female")
    eth = _row(mart, "D002289", "2020-2024", "ethnicity", "Hispanic or Latino")
    assert (sex["trials_in_window_count"], sex["trials_reporting_dimension_count"]) == (5, 5)
    assert eth["trials_reporting_dimension_count"] == 4  # NCT99750005 reports sex only
    assert eth["reporting_trials_share"] == pytest.approx(4 / 5)
    assert (eth["group_participants"], eth["dimension_participants"]) == (23, 492)
    assert "State Cancer Profiles" in eth["burden_source"]
    assert eth["burden_vintage"].startswith("2019-2023")
    assert eth["burden_group_annual_cases"] == 11780  # the burden_demographic_share row
    assert not sex["low_n_flag"] and eth["low_n_flag"]  # 5 vs 4 reporting trials


def test_every_ratio_traces_to_its_nct_ids(mart) -> None:
    eth = _row(mart, "D002289", "2020-2024", "ethnicity", "Hispanic or Latino")
    assert eth["contributing_nct_ids"] == "NCT99750001, NCT99750002, NCT99750003, NCT99750004"
    assert eth["group_trial_count"] == 4
    assert mart["contributing_nct_ids"].str.startswith("NCT").all()


def test_fewer_than_three_trials_are_suppressed(mart) -> None:
    row = _row(mart, "D002289", "2015-2019", "sex", "Female")
    assert row["suppressed_flag"] and row["low_n_flag"]
    assert row["group_trial_count"] == 2  # trial counts and NCT IDs stay visible
    for column in (
        "group_participants",
        "dimension_participants",
        "participation_share",
        "participation_prevalence_ratio",
        "ratio_ci_low",
        "ratio_ci_high",
    ):
        assert pd.isna(row[column]), column


def test_race_and_unmapped_conditions_show_composition_only(mart) -> None:
    race = _row(mart, "D002289", "2020-2024", "race", "White")
    adrd = _row(mart, "D000544", "2020-2024", "sex", "Female")
    for row in (race, adrd):
        assert row["comparison_basis"] == "participant_composition_only"
        assert not row["burden_denominator_available"]
        assert pd.isna(row["burden_share"]) and pd.isna(row["participation_prevalence_ratio"])
        assert pd.isna(row["burden_source"])
        assert "no comparable burden denominator available" in row["denominator_label"]
        assert not pd.isna(row["participation_share"])
    assert adrd["group_participants"] == 145 and adrd["low_n_flag"]  # three trials: low n only


def test_unknown_categories_are_not_groups_and_every_row_is_worded_safely(mart) -> None:
    assert "Unknown or Not Reported" not in set(mart["group_label"])
    assert mart["interpretation_note"].str.contains("Not evidence of intent, quotas").all()


# --- page helper ---------------------------------------------------------------------


def test_ratio_view_labels_composition_only_and_flags_small_cells(mart) -> None:
    table, caption = ratio_view(mart[(mart["mesh_id"] == "D000544")])
    assert caption == COMPOSITION_ONLY
    assert table["Ratio"].isna().all() and (table["Flag"] == LOW_N).all()

    table, caption = ratio_view(mart[(mart["mesh_id"] == "D002289") & (mart["dimension"] == "sex")])
    assert "State Cancer Profiles" in caption and "2019-2023" in caption
    assert SUPPRESSED in set(table["Flag"])
    assert table["NCT IDs"].str.startswith("NCT").all()
