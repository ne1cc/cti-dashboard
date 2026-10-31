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
    "dashboard/overview.py",
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
# overlap 0, sponsor 2, reliability 1, explorer 10), so a count
# cannot discriminate them: the table below covers the ones that name their own
# scope in a column, and the rest are guarded in source by
# tests/test_dashboard_profile_scope.py.
PROFILE_SCOPED_FIRST_DATAFRAME_ROWS = {
    "dashboard/overview.py": {"adrd": 6, "oncology_nsclc": 4},
    "dashboard/pages/1_Priority_Queue.py": {"adrd": 6, "oncology_nsclc": 4},
    "dashboard/pages/2_Competition_Landscape.py": {"adrd": 6, "oncology_nsclc": 4},
    "dashboard/pages/3_Geography_Trends.py": {"adrd": 4, "oncology_nsclc": 1},
}

# Pages whose first dataframe carries the column that names the scope it was read
# for — the strongest proof available where the row counts coincide.
PROFILE_SCOPED_FIRST_DATAFRAME_COLUMN = {
    "dashboard/pages/5_Sponsor_Landscape.py": "indication_profile_id",
    "dashboard/pages/7_Trial_Explorer.py": "indication_profile_id",
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
    # Audit observation views resolve relative silver paths at query time.
    monkeypatch.chdir(fixture_project_root)
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


def test_router_lands_on_the_overview(fixture_warehouse):
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    at = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    assert at.title[0].value == "Recruitment Competition Intelligence"


def test_overview_reads_as_labels_not_codes(fixture_warehouse):
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    at = AppTest.from_file(str(ROOT / "dashboard/overview.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    preview = at.dataframe[0].value
    assert "listed_site_count" in preview.columns
    assert preview["registry_search_url"].str.startswith("https://clinicaltrials.gov/search?").all()
    assert not preview["phase_normalized"].str.fullmatch(r"[A-Z0-9_/]+").any()
    assert "active_recruiting_trials" in preview.columns
    assert "sponsor_hhi" in preview.columns
    assert (preview["active_recruiting_trials"] >= 0).all()
    assert not any("00:00:00" in c.value for c in at.caption)
    audit_labels = [e.label for e in at.expander if e.label.startswith("Audit:")]
    assert len(audit_labels) == len(set(audit_labels)) == 3
    section_titles = {m.value for m in at.markdown}
    assert {"**Compare**", "**Inspect**", "**Verify**"} <= section_titles


def test_phase_filter_offers_display_labels(fixture_warehouse):
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    at = AppTest.from_file(
        str(ROOT / "dashboard/pages/2_Competition_Landscape.py"), default_timeout=60
    ).run()
    assert not at.exception, at.exception
    phase = next(w for w in at.sidebar.multiselect if w.label == "Phase")
    assert phase.options, "fixture should expose at least one phase"
    assert not any(option.isupper() for option in phase.options)


def test_similarity_page_shows_only_the_selected_profile(
    similarity_equivalence_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Page 8's scope check, on the dense fixture: the base one's default index
    trials have no neighbours, so the page would render no table to inspect."""
    from streamlit.testing.v1 import AppTest

    from src.config import get_config

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(similarity_equivalence_root))
    get_config.cache_clear()
    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    for profile_id in ("adrd", "oncology_nsclc"):
        at = AppTest.from_file(
            str(ROOT / "dashboard/pages/8_Trial_Similarity.py"), default_timeout=60
        )
        at.run()
        next(w for w in at.sidebar.selectbox if w.key == "indication_profile_selector").set_value(
            profile_id
        )
        at.text_input[0].input("NCT00000001")
        at.run()
        assert not at.exception, f"as {profile_id}: {at.exception}"
        assert at.dataframe, f"rendered no dataframe as {profile_id}"
        frame = at.dataframe[0].value
        assert {str(v) for v in frame["indication_profile_id"]} == {profile_id}


def test_guidance_priority_queue_has_no_score_or_bands():
    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    from components.guidance import PAGE_PLAYBOOKS

    pq_guide = str(PAGE_PLAYBOOKS["priority_queue"]).lower()
    for forbidden in ("score", "priority_review", "0.70", "0.45", "weighted"):
        assert forbidden not in pq_guide, (
            f"Found forbidden term '{forbidden}' in priority_queue guide"
        )


def test_audit_panel_derived_rules_has_no_score_or_bands():
    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    from components.audit_panel import PRIORITY_DERIVED_RULES

    rules_lower = PRIORITY_DERIVED_RULES.lower()
    for forbidden in ("min-max", "weighted sum", "0.70", "0.45", "priority_review"):
        assert forbidden not in rules_lower, (
            f"Found forbidden term '{forbidden}' in PRIORITY_DERIVED_RULES"
        )


def test_pages_smoke_contain_no_priority_score_or_bands(fixture_warehouse):
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    for script in ("dashboard/overview.py", "dashboard/pages/1_Priority_Queue.py"):
        at = AppTest.from_file(str(ROOT / script), default_timeout=60).run()
        assert not at.exception, at.exception
        page_text = " ".join(
            [m.value for m in at.markdown]
            + [c.value for c in at.caption]
            + [t.value for t in at.title]
            + [h.value for h in at.header]
            + [s.value for s in at.subheader]
        )
        assert "Priority Review" not in page_text
        assert "Watch" not in page_text
        assert "priority score" not in page_text.lower()


def test_data_reliability_page_no_calculator(fixture_warehouse):
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    for profile_id in ("adrd", "oncology_nsclc"):
        at = AppTest.from_file(
            str(ROOT / "dashboard/pages/6_Data_Reliability.py"), default_timeout=60
        ).run()
        selector = next(w for w in at.sidebar.selectbox if w.key == "indication_profile_selector")
        selector.set_value(profile_id)
        at.run()
        assert not at.exception, at.exception
        # Confirm scenario explorer and currency inputs are gone
        assert not any("scenario explorer" in str(h.value).lower() for h in at.subheader)
        assert not any("feasibility review" in str(n.label).lower() for n in at.number_input)
        assert not any("site activation" in str(n.label).lower() for n in at.number_input)
        assert not any("reviews per cycle" in str(n.label).lower() for n in at.number_input)
        # Confirm ingestion diagnostics and reconciliation remain
        assert any("Ingestion runs" in str(h.value) for h in at.subheader)
