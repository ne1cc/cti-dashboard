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
    "dashboard/pages/9_Trial_Transparency.py",
    "dashboard/pages/10_Access_Rates.py",
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
    "dashboard/pages/9_Trial_Transparency.py": "indication_profile_id",
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


def test_geography_trends_view_mode_toggle(fixture_warehouse):
    """Both listing modes render counts, rate provenance, and interpretation limits."""
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    at = AppTest.from_file(
        str(ROOT / "dashboard/pages/3_Geography_Trends.py"), default_timeout=60
    ).run()
    assert not at.exception, at.exception

    # Radio toggle exists with "Per 100k residents" and "Raw count"
    radio = next(w for w in at.radio if w.label == "Listing metric")
    assert radio.options == ["Per 100k residents", "Raw count"]
    assert radio.value == "Per 100k residents"

    # Default view shows per 100k subheader
    assert any("per 100k" in s.value.lower() for s in at.subheader)

    # Top states dataframe has both columns
    df = at.dataframe[0].value
    assert "recruiting_trials_per_100k" in df.columns
    assert "recruiting_trial_count" in df.columns
    assert df["recruiting_trials_per_100k"].dropna().is_monotonic_decreasing

    # Vintage is visible in caption
    assert any("Vintage 2024" in c.value for c in at.caption)
    assert any("measure patient access or clinical need" in c.value for c in at.caption)
    assert any(
        "population_normalization" in str(item.value)
        and "U.S. Census" in str(item.value)
        and "Vintage 2024" in str(item.value)
        for item in at.json
    )

    # Toggle to "Raw count" works
    radio.set_value("Raw count").run()
    assert not at.exception, at.exception
    assert radio.value == "Raw count"
    assert any("recruiting listings by state" in s.value.lower() for s in at.subheader)
    raw = at.dataframe[0].value
    assert "recruiting_trials_per_100k" in raw.columns
    assert raw["recruiting_trial_count"].is_monotonic_decreasing
    assert any("measure patient access or clinical need" in c.value for c in at.caption)


@pytest.mark.parametrize("rerun_raw", [False, True])
def test_geography_legacy_warehouse_keeps_raw_counts(
    fixture_warehouse, monkeypatch: pytest.MonkeyPatch, rerun_raw: bool
):
    """A retained pre-population mart stays usable until its next dbt rebuild."""
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    from components import data

    original = data.condition_geography_trends

    def legacy_trends(profile_id: str):
        """Return the legacy mart's columns without changing the shared warehouse."""
        return original(profile_id).drop(
            columns=[
                "population",
                "population_source",
                "population_vintage",
                "recruiting_trials_per_100k",
            ]
        )

    monkeypatch.setattr(data, "condition_geography_trends", legacy_trends)
    at = AppTest.from_file(
        str(ROOT / "dashboard/pages/3_Geography_Trends.py"), default_timeout=60
    ).run()
    assert not at.exception, at.exception
    radio = next(widget for widget in at.radio if widget.label == "Listing metric")
    assert radio.options == ["Raw count"]
    assert radio.value == "Raw count"
    if rerun_raw:
        radio.set_value("Raw count").run()
        assert not at.exception, at.exception
    table = at.dataframe[0].value
    assert table["recruiting_trials_per_100k"].isna().all()
    assert table["recruiting_trial_count"].is_monotonic_decreasing
    assert table["recruiting_trial_count"].sum() > 0
    assert any("rebuild" in warning.value.lower() for warning in at.warning)


def test_geography_missing_rates_caption_describes_top_twenty(
    fixture_warehouse, monkeypatch: pytest.MonkeyPatch
):
    """A null-rate territory outside the top twenty must not be promised visibility."""
    import pandas as pd
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    from components import data

    template = data.condition_geography_trends("adrd").iloc[[0]].copy()
    states = "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA GU".split()
    rows = []
    for index, state in enumerate(states):
        row = template.copy()
        row["state_normalized"] = state
        row["recruiting_trial_count"] = len(states) - index
        row["recruiting_trials_per_100k"] = float("nan") if state == "GU" else 22 - index
        rows.append(row)
    trends = pd.concat(rows, ignore_index=True)
    monkeypatch.setattr(data, "condition_geography_trends", lambda _: trends.copy())
    at = AppTest.from_file(
        str(ROOT / "dashboard/pages/3_Geography_Trends.py"), default_timeout=60
    ).run()
    assert not at.exception, at.exception
    assert len(at.dataframe[0].value) == 20
    assert "GU" not in at.dataframe[0].value["state_normalized"].tolist()
    assert any("table shows only the top 20" in caption.value.lower() for caption in at.caption)
    assert not any("remain listed" in caption.value.lower() for caption in at.caption)


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


def test_segment_drilldown_uses_selected_profile_label(
    fixture_warehouse, monkeypatch: pytest.MonkeyPatch
):
    """Both selected-segment headings use the active profile's display label."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    from components import guidance

    original_formatter = guidance.format_condition_group
    original_dataframe = st.dataframe

    def profile_label(group, profile_id=None):
        """Model a configured label that differs from the group-key fallback."""
        if profile_id == "oncology_nsclc":
            return "NSCLC Fixture Label"
        return original_formatter(group, profile_id)

    def select_first_row(*args, **kwargs):
        """Render normally and model selecting the first row of the selectable table."""
        rendered = original_dataframe(*args, **kwargs)
        if kwargs.get("on_select") == "rerun":
            return {"selection": {"rows": [0]}}
        return rendered

    monkeypatch.setattr(guidance, "format_condition_group", profile_label)
    monkeypatch.setattr(st, "dataframe", select_first_row)
    at = AppTest.from_file(
        str(ROOT / "dashboard/pages/1_Priority_Queue.py"), default_timeout=60
    ).run()
    selector = next(w for w in at.sidebar.selectbox if w.key == "indication_profile_selector")
    selector.set_value("oncology_nsclc").run()
    assert not at.exception, at.exception
    headings = [heading.value for heading in at.subheader]
    assert any("Segment details — NSCLC Fixture Label" in text for text in headings)
    assert any("Constituent Clinical Trials — NSCLC Fixture Label" in text for text in headings)


@pytest.mark.parametrize(
    "script",
    [
        "dashboard/overview.py",
        "dashboard/pages/1_Priority_Queue.py",
        "dashboard/pages/2_Competition_Landscape.py",
    ],
)
def test_hhi_suppression_keeps_numeric_table_and_explains_missing_values(
    script: str, fixture_warehouse: Path
) -> None:
    """Each HHI table retains numeric sorting and shows the row's suppression reason."""
    import pandas as pd
    from streamlit.testing.v1 import AppTest

    if str(ROOT / "dashboard") not in sys.path:
        sys.path.insert(0, str(ROOT / "dashboard"))
    at = AppTest.from_file(str(ROOT / script), default_timeout=60).run()
    assert not at.exception, at.exception
    table = at.dataframe[0].value
    assert pd.api.types.is_numeric_dtype(table["sponsor_hhi"])
    suppressed = table[table["sponsor_hhi"].isna()]
    assert not suppressed.empty
    assert suppressed["hhi_status"].str.startswith("insufficient data (").all()
    if script.endswith("2_Competition_Landscape.py"):
        assert any(
            f"Excluded {len(suppressed)} segments" in caption.value for caption in at.caption
        )
