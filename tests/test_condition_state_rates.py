"""End-to-end check of `mart_condition_state_rates` and `dim_population` (Issue #71).

Intent: build a fixture warehouse whose trials carry registry MeSH nodes for three
conditions with three different burden sources, then assert the mart's per-100k
and per-estimated-case rates against numbers recomputed here from the committed
seeds. No network: every input is a committed seed or fixture.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from tests.fixture_warehouse import AddedStudy, base_adrd_run, build_fixture_warehouse

SEEDS = Path(__file__).resolve().parents[1] / "dbt_clinical_trials" / "seeds"
NSCLC, T2D, ALZ = "D002289", "D003924", "D000544"

# (nct_id, status, state, MeSH id): MA and TX only, so every other state is a dense zero.
TRIALS = [
    ("NCT00990001", "RECRUITING", "Massachusetts", ALZ),
    ("NCT00990002", "RECRUITING", "Massachusetts", NSCLC),
    ("NCT00990003", "RECRUITING", "Massachusetts", NSCLC),
    ("NCT00990004", "RECRUITING", "Massachusetts", T2D),
    ("NCT00990005", "RECRUITING", "Texas", NSCLC),
    ("NCT00990006", "RECRUITING", "Texas", T2D),
    ("NCT00990007", "COMPLETED", "Massachusetts", NSCLC),  # not recruiting: must not count
]


def _csv(name: str) -> list[dict[str, str]]:
    with (SEEDS / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _mesh(mesh_id: str) -> dict[str, object]:
    return {
        "conditionBrowseModule": {"meshes": [{"id": mesh_id, "term": mesh_id}], "ancestors": []}
    }


@pytest.fixture(scope="module")
def con(tmp_path_factory: pytest.TempPathFactory) -> Iterator[duckdb.DuckDBPyConnection]:
    """Read-only connection to a fixture warehouse built from TRIALS."""
    added = tuple(
        AddedStudy(
            nct_id=nct,
            clone_of="NCT00000001",
            overall_status=status,
            location=(f"Rates fixture {nct}", "Fixture City", state),
        )
        for nct, status, state, _ in TRIALS
    )
    overrides = {nct: {"derivedSection": _mesh(mesh)} for nct, _, _, mesh in TRIALS}
    run = replace(base_adrd_run(), added=added, study_overrides=overrides)
    root = build_fixture_warehouse(tmp_path_factory.mktemp("state_rates"), runs=[run])
    with duckdb.connect(str(root / "data/warehouse/clinical_trials.duckdb"), read_only=True) as c:
        yield c


def _rate(con: duckdb.DuckDBPyConnection, node: str, state: str) -> dict[str, object]:
    cur = con.execute(
        "select * from main_marts.mart_condition_state_rates where node_id = ? and state_code = ?",
        [node, state],
    )
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    assert len(rows) == 1, f"{node}/{state}: expected one row, got {len(rows)}"
    return dict(zip(cols, rows[0], strict=True))


def _acs_total(state: str) -> int:
    fips = next(r["fips_code"] for r in _csv("state_population.csv") if r["state_code"] == state)
    return next(
        int(r["population"])
        for r in _csv("acs_population_extract.csv")
        if r["geo_level"] == "state" and r["geo_id"] == fips and r["dimension"] == "total"
    )


def test_nsclc_state_rates_use_seer_cases(con: duckdb.DuckDBPyConnection) -> None:
    """NSCLC/MA: 2 recruiting trials; per-100k from ACS, per-1k-cases from SEER's annual count."""
    row = _rate(con, NSCLC, "MA")
    seer = next(r for r in _csv("burden_seer_lung_state.csv") if r["state_name"] == "Massachusetts")
    cases = float(seer["average_annual_count"])
    assert row["recruiting_trial_count"] == 2  # the COMPLETED trial is excluded
    assert row["population"] == _acs_total("MA")
    assert row["recruiting_trials_per_100k"] == round(100000 * 2 / _acs_total("MA"), 4)
    assert row["denominator_available"] is True
    assert row["burden_estimated_cases"] == cases
    assert row["recruiting_trials_per_1k_est_cases"] == round(1000 * 2 / cases, 4)
    assert row["burden_source_id"] == "seer_state_cancer_profiles"
    assert "2019-2023" in str(row["denominator_label"])
    assert "ACS 2020-2024 5-year" in str(row["population_source"])


def test_diabetes_state_rates_use_places_cases(con: duckdb.DuckDBPyConnection) -> None:
    """T2D/MA: estimated cases are the county-level PLACES prevalence x adults, summed."""
    row = _rate(con, T2D, "MA")
    places = [
        r
        for r in _csv("burden_places_county.csv")
        if r["state_code"] == "MA" and r["measure_id"] == "DIABETES" and r["prevalence_pct"]
    ]
    cases = sum(float(r["prevalence_pct"]) / 100 * int(r["population_18plus"]) for r in places)
    assert row["denominator_available"] is True
    assert row["burden_estimated_cases"] == pytest.approx(cases)
    assert row["recruiting_trials_per_1k_est_cases"] == pytest.approx(1000 * 1 / cases, abs=1e-4)
    assert row["burden_source_id"] == "cdc_places_county"
    assert row["burden_county_estimate_count"] == len(places)


def test_adrd_has_no_burden_denominator(con: duckdb.DuckDBPyConnection) -> None:
    """ADRD maps only to a self-reported proxy: per-100k stays, per-case rate is withheld."""
    row = _rate(con, ALZ, "MA")
    assert row["recruiting_trial_count"] == 1
    assert row["recruiting_trials_per_100k"] == round(100000 / _acs_total("MA"), 4)
    assert row["denominator_available"] is False
    assert row["burden_estimated_cases"] is None
    assert row["recruiting_trials_per_1k_est_cases"] is None
    assert row["denominator_label"] == "no burden denominator available"
    assert "not diagnosed dementia" in str(row["denominator_note"])


def test_state_without_places_coverage_says_no_denominator(con: duckdb.DuckDBPyConnection) -> None:
    """TX is absent from the PLACES extract: no rate per case, and no silent raw-count fallback."""
    row = _rate(con, T2D, "TX")
    assert row["recruiting_trial_count"] == 1
    assert row["recruiting_trials_per_100k"] is not None
    assert row["denominator_available"] is False
    assert row["recruiting_trials_per_1k_est_cases"] is None
    assert row["denominator_label"] == "no burden denominator available"
    assert "no estimate for this state" in str(row["denominator_note"])
    # SEER covers TX, so NSCLC there still has a denominator.
    assert _rate(con, NSCLC, "TX")["denominator_available"] is True


def test_grid_is_dense_and_every_row_names_its_denominator(con: duckdb.DuckDBPyConnection) -> None:
    """Zero-trial states are present, and no row lacks a denominator statement."""
    states, zero_rows, unlabeled = con.execute(
        """
        select count(distinct state_code),
               count(*) filter (
                   where recruiting_trial_count = 0 and recruiting_trials_per_100k = 0
               ),
               count(*) filter (where denominator_label is null or population_source is null)
        from main_marts.mart_condition_state_rates
        where node_id = 'D002289'
        """
    ).fetchone() or (0, 0, 1)
    assert states == 52
    assert zero_rows == 50  # every state except MA and TX
    assert unlabeled == 0


def test_dim_population_reconciles_and_cites_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Each dimension sums to the geography total; rows carry table id and vintage."""
    bad = con.execute(
        """
        with t as (
            select geo_level, geo_id, dimension, sum(population) as s
            from main_marts.dim_population group by all
        )
        select count(*) from t a join t b
            on a.geo_level = b.geo_level and a.geo_id = b.geo_id and b.dimension = 'total'
        where a.dimension <> 'total' and a.s <> b.s
        """
    ).fetchone()
    assert bad == (0,)
    tables, vintages = con.execute(
        "select list(distinct table_id order by table_id), list(distinct vintage) "
        "from main_marts.dim_population"
    ).fetchone() or ([], [])
    assert tables == ["B01001", "B03002"]
    assert vintages == ["ACS 2020-2024 5-year"]
    levels = {
        r[0] for r in con.execute("select geo_level from main_marts.dim_population").fetchall()
    }
    assert levels == {"state", "county", "zcta"}
