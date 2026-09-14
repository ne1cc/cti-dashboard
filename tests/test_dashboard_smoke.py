"""Smoke tests: every dashboard page must execute without exceptions.

Only ``test_page_runs_without_exception`` reads the repo's own ``data/warehouse``
and skips when it is absent (e.g. in CI without data). The profile tests below
point the pages at the session two-profile fixture instead, so they run — and can
fail — whether or not that skip fires.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT / "data" / "warehouse" / "clinical_trials.duckdb"

PAGES = [
    "dashboard/app.py",
    "dashboard/pages/1_Priority_Queue.py",
    "dashboard/pages/2_Competition_Landscape.py",
    "dashboard/pages/3_Geography_Trends.py",
    "dashboard/pages/4_Site_Overlap.py",
    "dashboard/pages/5_Sponsor_Landscape.py",
    "dashboard/pages/6_Data_Reliability.py",
    "dashboard/pages/7_Trial_Explorer.py",
    "dashboard/pages/8_Trial_Similarity.py",
]

# What the first dataframe each page renders must hold, per profile, measured
# 2026-09-06 against the session two-profile fixture. A page can render the
# selector and discard its return value (`render_profile_selector(); profile_id =
# "adrd"`) and keep every widget assertion here green — the widget still holds the
# pick, the page just shows one fixed indication. These are the numbers it shows
# instead. Pages left out render the *same* row count for both profiles (site
# overlap 0, sponsor 2, reliability 1, explorer 10, similarity 9), so a count
# cannot discriminate them: the table below covers the ones that name their own
# scope in a column, and the rest are guarded in source by
# tests/test_dashboard_profile_scope.py.
PROFILE_SCOPED_FIRST_DATAFRAME_ROWS = {
    "dashboard/app.py": {"adrd": 6, "oncology_nsclc": 4},
    "dashboard/pages/1_Priority_Queue.py": {"adrd": 6, "oncology_nsclc": 4},
    "dashboard/pages/2_Competition_Landscape.py": {"adrd": 6, "oncology_nsclc": 4},
    "dashboard/pages/3_Geography_Trends.py": {"adrd": 4, "oncology_nsclc": 1},
}

# Pages whose first dataframe carries the column that names the scope it was read
# for — the strongest proof available where the row counts coincide.
PROFILE_SCOPED_FIRST_DATAFRAME_COLUMN = {
    "dashboard/pages/5_Sponsor_Landscape.py": "indication_profile_id",
    "dashboard/pages/7_Trial_Explorer.py": "indication_profile_id",
    "dashboard/pages/8_Trial_Similarity.py": "indication_profile_id",
}


def warehouse_has_marts() -> bool:
    if not WAREHOUSE.exists():
        return False
    try:
        import duckdb

        con = duckdb.connect(str(WAREHOUSE), read_only=True)
        tables = con.execute("SHOW ALL TABLES").fetchall()
        schemas = {t[1] for t in tables}
        return "main_marts" in schemas
    except Exception:
        return False


@pytest.mark.skipif(
    not warehouse_has_marts(), reason="marts not built in warehouse (run make pipeline)"
)
@pytest.mark.parametrize("script", PAGES)
def test_page_runs_without_exception(script):
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    at = AppTest.from_file(str(ROOT / script), default_timeout=60)
    at.run()
    assert not at.exception, at.exception[0].value if at.exception else ""


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    """Streamlit's caches are process-global, and these tests and
    test_dashboard_profile_scope.py point at *different* warehouses (the session
    fixture's, and the real repo one). Without this, whichever module runs
    second serves the first one's connection and frames.
    """
    import streamlit as st

    st.cache_data.clear()
    st.cache_resource.clear()
    yield
    st.cache_data.clear()
    st.cache_resource.clear()


@pytest.fixture
def fixture_warehouse(fixture_project_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Serve the session two-profile warehouse to the dashboard pages.

    The repo's own ``data/`` holds no warehouse in a fresh checkout, which is why
    ``test_page_runs_without_exception`` skips here. These page runs must not skip
    with it: Task 6's fixture is a real ``dbt build`` over both profiles, and
    ``data`` resolves the file through ``get_config()``, which honours
    ``CTI_PROJECT_ROOT``.
    """
    from src.config import get_config

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(fixture_project_root))
    get_config.cache_clear()
    return fixture_project_root


@pytest.mark.parametrize("script", PAGES)
def test_every_page_offers_the_registry_profiles(script, fixture_warehouse):
    """The selector must exist on every page, be keyed, and offer exactly the
    refreshable registry profiles — not whatever the warehouse happens to hold.
    """
    from streamlit.testing.v1 import AppTest

    from src.profiles import get_registry

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    at = AppTest.from_file(str(ROOT / script), default_timeout=60)
    at.run()
    assert not at.exception, at.exception[0].value if at.exception else ""
    selectors = [w for w in at.sidebar.selectbox if w.key == "indication_profile_selector"]
    assert len(selectors) == 1, f"{script} renders {len(selectors)} profile selectors"
    registry = get_registry()
    refreshable = registry.refreshable()
    # `AppTest.selectbox.options` is built from the widget proto, which carries
    # the *formatted* labels (streamlit/testing/v1/app_test.py: `self.options =
    # list(proto.options)`), so the ids are asserted through `value` below.
    assert selectors[0].options == [p.display_name for p in refreshable]
    assert selectors[0].value == refreshable[0].profile_id, (
        "the widget value is the label, not the profile id"
    )
    assert registry.get("full_catalog").display_name not in selectors[0].options, (
        "an ingest_only profile is offered as a viewable scope"
    )


@pytest.mark.parametrize("script", PAGES)
def test_every_page_survives_the_other_profile(script, fixture_warehouse):
    """Switching scope is a full re-run of the page against a profile the
    deployment may not have data for yet. Nine pages x two profiles, in-process.
    """
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    for profile_id in ("adrd", "oncology_nsclc"):
        at = AppTest.from_file(str(ROOT / script), default_timeout=60)
        at.run()
        selector = next(w for w in at.sidebar.selectbox if w.key == "indication_profile_selector")
        selector.set_value(profile_id)
        at.run()
        assert not at.exception, f"{script} as {profile_id}: {at.exception}"
        # Re-read from the *re-run* tree: setting a value that the page ignored
        # would otherwise look identical to a page that survived the switch.
        landed = next(w for w in at.sidebar.selectbox if w.key == "indication_profile_selector")
        assert landed.value == profile_id, f"{script} did not re-run as {profile_id}"
        # ...and that only proves the *widget* kept the pick. What the page did
        # with it is the part a `profile_id = "adrd"` overwrite cannot hide.
        frames = at.dataframe
        expected_rows = PROFILE_SCOPED_FIRST_DATAFRAME_ROWS.get(script)
        if expected_rows is not None:
            assert frames, f"{script} rendered no dataframe as {profile_id}"
            shown_rows = len(frames[0].value)
            assert shown_rows == expected_rows[profile_id], (
                f"{script} rendered {shown_rows} rows as {profile_id}, expected "
                f"{expected_rows[profile_id]}: the page is not using the selector's return value"
            )
        scope_column = PROFILE_SCOPED_FIRST_DATAFRAME_COLUMN.get(script)
        if scope_column is not None:
            assert frames, f"{script} rendered no dataframe as {profile_id}"
            frame = frames[0].value
            assert scope_column in frame.columns, f"{script}'s first dataframe lost {scope_column}"
            shown_scopes = {str(v) for v in frame[scope_column]}
            assert shown_scopes == {profile_id}, (
                f"{script} renders rows of {sorted(shown_scopes)} while scoped to {profile_id}"
            )


def test_every_page_shows_disclaimer():
    sys.path.insert(0, str(ROOT / "dashboard"))
    from components.guardrails import DISCLAIMER

    assert "not" in DISCLAIMER.lower()
    for script in PAGES:
        source = (ROOT / script).read_text(encoding="utf-8")
        assert "page_setup(" in source, f"{script} must use page_setup (guardrail banner)"
