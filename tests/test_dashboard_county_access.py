"""Issue #73: the county access map on Geography Trends.

Logic tests run on a hand-built frame with the mart's columns (the fixture warehouse has no
open sites with coordinates, so its county mart is empty). Legend and caption wording are
pinned as text, not pixels: they are what a reader is told, and what a rewrite must not
quietly change.
"""

import csv
import io
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "dashboard") not in sys.path:
    sys.path.insert(0, str(ROOT / "dashboard"))

from components import county_access as ca  # noqa: E402
from components import data  # noqa: E402

COLUMNS = [
    "indication_profile_id", "mesh_id", "condition_label", "county_fips", "county_name",
    "state_code", "population", "population_vintage", "svi_quartile", "ruca_group",
    "nearest_site_miles", "within_25mi", "within_50mi", "within_100mi", "sites_total",
    "sites_exact", "sites_zip_approx", "sites_city_approx", "sites_excluded",
    "coverage_confidence",
]  # fmt: skip


def _row(fips, name, pop, ruca, near, label="Condition A", conf="high", svi=2.0, state="MD"):
    return {
        "indication_profile_id": "adrd", "mesh_id": "D1", "condition_label": label,
        "county_fips": fips, "county_name": name, "state_code": state, "population": pop,
        "population_vintage": "ACS 2020-2024", "svi_quartile": svi, "ruca_group": ruca,
        "nearest_site_miles": near, "within_25mi": near is not None and near <= 25,
        "within_50mi": near is not None and near <= 50,
        "within_100mi": near is not None, "sites_total": 10, "sites_exact": 9,
        "sites_zip_approx": 1, "sites_city_approx": 0, "sites_excluded": 0,
        "coverage_confidence": conf,
    }  # fmt: skip


def _frame(label="Condition A", conf="high", far="rural"):
    rows = [
        _row("24001", "Allegany County", 60_000, far, None, label, conf),
        _row("24005", "Baltimore County", 800_000, "metropolitan", 10.0, label, conf),
        _row("24025", "Harford County", 260_000, "micropolitan", 40.0, label, conf),
        _row("24999", "Tiny County", 900, "rural", None, label, conf),
        _row("24998", "Unranked County", 30_000, "small_town", 90.0, label, conf, svi=None),
    ]
    return pd.DataFrame(rows, columns=COLUMNS)


def test_small_population_is_insufficient_not_zero():
    out = ca.classify(_frame(), 100).set_index("county_name")
    assert out.loc["Tiny County", "reason"] == "population under 5,000"
    assert not out.loc["Tiny County", "in_range"]
    assert out.loc["Allegany County", "reason"] == ""  # a real "no site", still a number


def test_node_without_usable_coordinates_is_insufficient_everywhere():
    out = ca.classify(_frame(conf="none"), 100)
    assert set(out["reason"]) == {"no open site has usable coordinates", "population under 5,000"}
    summary = ca.summarize(out)
    assert (summary.counties, summary.population, summary.covered_population) == (0, 0, 0)
    assert summary.share is None and summary.median_miles is None


def test_summary_denominator_excludes_insufficient_counties():
    summary = ca.summarize(ca.classify(_frame(), 50))
    assert summary.insufficient == 1
    assert summary.population == 60_000 + 800_000 + 260_000 + 30_000
    assert summary.covered_population == 800_000 + 260_000  # 90 mi is beyond 50
    assert summary.share == pytest.approx(1_060_000 / 1_150_000)
    assert summary.median_miles == 10.0  # Baltimore County holds half of all residents


def test_median_is_none_when_the_median_resident_is_beyond_100_miles():
    frame = _frame().assign(nearest_site_miles=[None, None, None, None, 90.0])
    assert ca.summarize(ca.classify(frame, 100)).median_miles is None


def test_filters_use_ruca_and_unranked_svi():
    out = ca.apply_filters(_frame(), ["small_town"], ["unranked"])
    assert out["county_name"].tolist() == ["Unranked County"]
    assert ca.apply_filters(_frame(), ["small_town"], ["Q2"]).empty


def test_rural_gaps_compare_two_conditions():
    a = _frame("Condition A")
    b = _frame("Condition B").assign(nearest_site_miles=[20.0, 5.0, 40.0, None, 90.0])
    b["within_100mi"] = b["nearest_site_miles"].notna()
    gaps = ca.rural_gaps({"Condition A": a, "Condition B": b}).set_index("county_name")
    assert gaps.loc["Allegany County", "Condition A"] == "no site within 100 mi"
    assert gaps.loc["Allegany County", "Condition B"] == "site within 100 mi"
    assert "Tiny County" not in gaps.index  # insufficient data is never reported as a gap
    assert "Baltimore County" not in gaps.index  # metropolitan


def test_figure_legend_and_scale_regression():
    classified = ca.classify(_frame(), 100)
    pop = ca.build_figure(classified, 100, ca.METRIC_POPULATION)
    shown, no_site, grey = pop.data
    assert shown.colorscale[0][1] == "#440154" and shown.colorscale[-1][1] == "#fde725"  # Viridis
    assert shown.colorbar.title.text == "Residents within 100 mi"
    assert no_site.name == "No open site within 100 mi"
    assert no_site.showlegend and not no_site.showscale
    assert list(no_site.locations) == ["24001"]
    assert grey.name == "Insufficient data (not zero)"
    assert grey.showlegend and not grey.showscale
    assert list(grey.locations) == ["24999"]
    assert "Insufficient data: population under 5,000" in grey.text[0]

    dist = ca.build_figure(classified, 100, ca.METRIC_DISTANCE)
    scale, beyond, _ = dist.data
    assert scale.colorbar.title.text == "Miles to nearest open site"
    assert (scale.zmin, scale.zmax) == (0, 100)
    assert max(scale.z) == 90.0  # nothing is drawn at 100 unless it is 100 miles away
    assert beyond.name == "100+ mi (no open site within 100 mi)" and beyond.showlegend
    assert list(beyond.locations) == ["24001"]
    assert "no site within 100 mi" in beyond.text[0]
    names = {t.name for t in ca.build_figure(classified, 25, ca.METRIC_POPULATION).data}
    assert names == {"Open site within 25 mi", "No open site within 25 mi", grey.name}


def test_small_in_range_county_is_not_drawn_like_a_county_with_no_site():
    # The seeded Vermont case: a 6,000-resident county in range beside a ~800,000 metro one.
    frame = _frame().assign(population=[60_000, 800_000, 260_000, 900, 6_000])
    classified = ca.classify(frame, 100)
    scale, no_site, _ = ca.build_figure(classified, 100, ca.METRIC_POPULATION).data
    assert "24998" in scale.locations and "24998" not in no_site.locations
    assert list(no_site.locations) == ["24001"]  # out of range: its own legend entry
    assert no_site.colorscale[0][1] == ca.NO_SITE_COLOR
    viridis = {c for _, c in scale.colorscale}
    assert ca.NO_SITE_COLOR not in viridis and ca.INSUFFICIENT_COLOR not in viridis
    assert ca.NO_SITE_COLOR != ca.INSUFFICIENT_COLOR


def test_access_table_carries_the_hover_facts_as_text():
    table = ca.access_table(ca.classify(_frame(), 50), 50).set_index("county")
    assert list(table.columns) == [
        "state", "rural-urban group (RUCA)", "SVI quartile", "residents", "nearest open site",
        "within 50 mi", "insufficient data",
    ]  # fmt: skip
    allegany = table.loc["Allegany County"]
    assert allegany["nearest open site"] == "no site within 100 mi"  # text, not an empty cell
    assert (allegany["within 50 mi"], allegany["rural-urban group (RUCA)"]) == ("no", "rural")
    assert (
        allegany["SVI quartile"] == "Q2"
        and table.loc["Unranked County", "SVI quartile"] == "unranked"
    )
    assert table.loc["Harford County", "nearest open site"] == "40.0 mi"
    assert table.loc["Harford County", "within 50 mi"] == "yes"
    tiny = table.loc["Tiny County"]
    assert (
        tiny["within 50 mi"] == "insufficient data"
        and tiny["nearest open site"] == "insufficient data"
    )
    assert table.loc["Unranked County", "within 50 mi"] == "no"  # 90 mi is beyond 50


def test_caption_content_regression():
    classified = ca.classify(_frame(), 50)
    text = ca.summary_text(ca.summarize(classified), 50, ["MD"], "ACS 2020-2024")
    assert text == (
        "Denominator: 1,150,000 residents in 4 assessed counties (MD; ACS 2020-2024). "
        "Within 50 mi: 1,060,000 (92%). Population-weighted median distance to nearest open "
        "site: 10 mi. Excluded as insufficient data: 1 counties."
    )
    assert ca.coverage_text(_frame()) == (
        "Site coverage: high confidence · 10 open sites, 9 on registry coordinates, "
        "1 placed by ZIP, 0 by city, 0 excluded (no coordinates)."
    )
    assert "What it does not mean" in ca.MEANING
    assert "Not travel time" in ca.METHODOLOGY
    assert "not zero" in ca.MEANING


def test_scope_text_names_states_and_county_count_from_the_data():
    seeded = pd.concat([_frame(), _frame(label="Condition B")])  # same counties, two conditions
    assert ca.scope_text(seeded) == (
        "Coverage: only the 5 seeded counties in MD are mapped; "
        "every other county and state has no data (not zero)."
    )
    kent = pd.DataFrame([_row("10001", "Kent County", 180_000, "metropolitan", 5.0, state="DE")])
    two = pd.concat([_frame(), kent])
    assert "6 seeded counties in DE and MD" in ca.scope_text(two)
    assert "only the seeded counties" in ca.scope_text(pd.DataFrame())


def test_csv_carries_methodology_header_and_parses():
    classified = ca.classify(_frame(), 100)
    header = ca.methodology_header(
        "Condition A", 100, ca.METRIC_DISTANCE, "Sep 1, 2026", "RUCA all; SVI all",
        ca.summarize(classified), ca.scope_text(_frame()),
    )  # fmt: skip
    text = ca.to_csv(classified, header)
    lines = text.splitlines()
    assert all(line.startswith("# ") for line in lines[: len(header)])
    assert any("Denominator: 1,150,000" in line for line in lines)
    assert any("data date: Sep 1, 2026" in line for line in lines)
    assert any("Method: Straight-line" in line for line in lines)
    assert any("only the 5 seeded counties in MD" in line for line in lines)
    body = [line for line in lines if not line.startswith("#")]
    rows = list(csv.DictReader(io.StringIO("\n".join(body))))
    assert len(rows) == 5
    tiny = next(r for r in rows if r["county_name"] == "Tiny County")
    assert tiny["insufficient_data_reason"] == "population under 5,000"


def test_geojson_covers_every_seeded_county():
    seed = ROOT / "dbt_clinical_trials" / "seeds" / "county_access_context.csv"
    with seed.open(encoding="utf-8", newline="") as f:
        fips = {r["county_fips"] for r in csv.DictReader(f)}
    ids = {f["id"] for f in json.loads(ca.GEOJSON.read_text(encoding="utf-8"))["features"]}
    assert fips <= ids
    assert len(ca.county_geojson(sorted(fips))["features"]) == len(fips)


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    import streamlit as st

    st.cache_data.clear()
    st.cache_resource.clear()
    yield
    st.cache_data.clear()
    st.cache_resource.clear()


@pytest.fixture
def fixture_warehouse(fixture_project_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Serve the session two-profile warehouse to the dashboard pages (as the smoke tests do)."""
    from src.config import get_config

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(fixture_project_root))
    get_config.cache_clear()
    monkeypatch.chdir(fixture_project_root)
    return fixture_project_root


# --- page -----------------------------------------------------------------------------


def _page(monkeypatch, frame):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(data, "county_access", lambda preset: frame)
    return AppTest.from_file(
        str(ROOT / "dashboard/pages/3_Geography_Trends.py"), default_timeout=60
    ).run()


def test_page_shows_context_and_compares_two_conditions(fixture_warehouse, monkeypatch):
    frame = pd.concat([_frame("Condition A"), _frame("Condition B", far="small_town")])
    at = _page(monkeypatch, frame)
    assert not at.exception, at.exception
    scope = "only the 5 seeded counties in MD are mapped"
    assert any(i.value.startswith(ca.MEANING) and scope in i.value for i in at.info)
    captions = [c.value for c in at.caption]
    assert any(c.startswith("Data date:") and "Denominator:" in c and scope in c for c in captions)
    assert any(c.startswith(ca.METHODOLOGY) and scope in c for c in captions)
    assert any(c.startswith("Site coverage:") for c in captions)

    next(s for s in at.selectbox if s.key == "county_compare").set_value("Condition B").run()
    assert not at.exception, at.exception
    gaps = next(d.value for d in at.dataframe if "Condition B" in d.value.columns)
    assert {"Condition A", "Condition B"} <= set(gaps.columns)
    allegany = gaps.set_index("county_name").loc["Allegany County"]
    assert allegany["Condition A"] == "no site within 100 mi"
    table = next(d.value for d in at.dataframe if "within 100 mi" in d.value.columns)
    assert "no site within 100 mi" in set(table["nearest open site"])
    csvs = [b for b in at.get("download_button") if b.key.startswith("county_csv_")]
    assert len(csvs) == 2  # one CSV per condition


def test_page_without_county_rows_still_explains_and_never_shows_zero(fixture_warehouse):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(
        str(ROOT / "dashboard/pages/3_Geography_Trends.py"), default_timeout=60
    ).run()
    assert not at.exception, at.exception
    assert any(i.value.startswith(ca.MEANING) and "seeded counties" in i.value for i in at.info)
    assert any("no county is shown as zero" in c.value for c in at.caption)


def test_reader_is_profile_scoped_and_empty_mart_is_an_empty_frame(fixture_warehouse):
    from tests.preset_helpers import preset_for

    frame = data.county_access(preset_for("adrd"))
    assert frame.empty and "mesh_id" in frame.columns
    assert data.county_access_data_date(preset_for("adrd")) is not None


def test_reader_sql_runs_against_the_warehouse_mart(fixture_warehouse, tmp_path, monkeypatch):
    """The reader's SQL and the component's columns, against the real mart table.

    The fixture mart is empty, so a copy of the warehouse gets one row built from the
    component's own column list. A mart column rename or a reader typo fails here, not at
    runtime on a user's page.
    """
    import duckdb

    from tests.preset_helpers import preset_for

    copy = tmp_path / "warehouse.duckdb"
    shutil.copyfile(data.warehouse_path(), copy)
    with duckdb.connect(str(copy)) as con:
        mart = "main_marts.mart_trial_access_by_geo"
        present = {r[0] for r in con.execute(f"select * from {mart} limit 0").description}
        assert set(COLUMNS) <= present, set(COLUMNS) - present
        if con.execute(f"select count(*) from {mart}").fetchone()[0] == 0:
            row = pd.DataFrame([_row("24001", "Allegany County", 60_000, "rural", None)])
            row = row.assign(indication_profile_id=preset_for("adrd").profile_id)
            con.register("seed_row", row)
            columns = ", ".join(COLUMNS)
            con.execute(f"insert into {mart} ({columns}) select {columns} from seed_row")
    monkeypatch.setattr(data, "warehouse_path", lambda: copy)
    st.cache_resource.clear()
    st.cache_data.clear()

    frame = data.county_access(preset_for("adrd"))
    assert set(COLUMNS) <= set(frame.columns) and not frame.empty
    assert set(frame["indication_profile_id"]) == {preset_for("adrd").profile_id}
    classified = ca.classify(frame, 100)
    ca.build_figure(classified, 100, ca.METRIC_POPULATION)
    ca.access_table(classified, 100)
    ca.to_csv(
        classified,
        ca.methodology_header(
            "c", 100, "m", "d", "f", ca.summarize(classified), ca.scope_text(frame)
        ),
    )
