"""The dashboard may only read one indication profile at a time.

Static guards here; the behavioural cross-profile-leak test is Step 5.
"""

import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
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


def _load_data_module():
    if str(DASHBOARD) not in sys.path:
        sys.path.insert(0, str(DASHBOARD))
    from components import data

    return data


def test_every_warehouse_reader_is_profile_first() -> None:
    """A reader without `profile_id` is a cross-indication blend, and it will
    not raise — it will just look like a bigger ADRD.

    `inspect.getmembers(data, inspect.isfunction)` cannot be used: st.cache_data
    returns a `CachedFunc`, which is not a function object (verified
    2026-09-05), so the cached readers would be silently skipped. `vars()` sees
    them, and `inspect.signature` still resolves the wrapped parameters.
    """
    data = _load_data_module()
    skipped = {"warehouse_path", "query"}
    checked = 0
    for name, obj in vars(data).items():
        if name.startswith("_") or name in skipped:
            continue
        if not callable(obj) or getattr(obj, "__module__", None) != data.__name__:
            continue
        params = list(inspect.signature(obj).parameters)
        assert params and params[0] == "profile_id", f"{name}() is not scoped: {params}"
        checked += 1
    assert checked >= 10, f"only {checked} readers found; the module shape changed"


def test_no_page_calls_a_reader_without_arguments() -> None:
    """Catches the `data.priority_queue()` shape that existed before scoping.

    `require_warehouse` is in the pattern on purpose: it gained profile_id too,
    so a bare call there is also the old shape.
    """
    pattern = re.compile(r"data\.[a-z_]+\(\s*\)")
    for script in PAGES:
        source = (ROOT / script).read_text(encoding="utf-8")
        assert not pattern.findall(source), f"{script}: {pattern.findall(source)}"


def test_every_page_renders_the_profile_selector() -> None:
    for script in PAGES:
        source = (ROOT / script).read_text(encoding="utf-8")
        assert "render_profile_selector(" in source, f"{script} has no profile selector"


PROFILES = ("adrd", "oncology_nsclc")

# Measured on this fixture, read-only: `dim_geography` holds 9 rows for `adrd` and
# 18 across both profiles, so the unscoped KPI showed every profile's states as
# one number. Recorded here because Task 11 left it to a KPI card to notice.
ADRD_STATES_WITH_SITES = 9


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    """Streamlit's caches are process-global, and these tests and
    test_dashboard_smoke.py point at *different* warehouses (the session
    fixture's, and the real repo one). Without this, whichever module runs
    second serves the first one's connection and frames.
    """
    import streamlit as st

    st.cache_data.clear()
    st.cache_resource.clear()
    yield
    st.cache_data.clear()
    st.cache_resource.clear()


@pytest.fixture(autouse=True)
def _readers_point_at_the_fixture_warehouse(
    fixture_project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``data`` resolves its database through ``get_config()``, which reads
    ``CTI_PROJECT_ROOT``; a fresh checkout has no warehouse under the repo's own
    ``data/`` at all, so without this the readers below would open a file that is
    not there and every behavioural test would pass vacuously or error.
    """
    from src.config import get_config

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(fixture_project_root))
    get_config.cache_clear()


def test_no_reader_returns_another_profiles_rows(fixture_project_root) -> None:
    """The adversarial case: Task 6's two profiles share all ten NCT IDs and one
    snapshot date, so `set(frame["indication_profile_id"]) == {profile}` is the
    only assertion that distinguishes correct scoping from a lucky count.
    """
    data = _load_data_module()
    readers = [
        data.priority_queue,
        data.recruiting_competition,
        data.condition_geography_trends,
        data.site_overlap,
        data.sponsor_landscape,
        data.trial_explorer,
        data.data_reliability,
    ]
    for profile in PROFILES:
        for reader in readers:
            frame = reader(profile)
            assert not frame.empty, f"{reader.__name__}({profile}) returned no rows"
            assert set(frame["indication_profile_id"]) == {profile}, reader.__name__

    both = {p: len(data.trial_explorer(p)) for p in PROFILES}
    assert min(both.values()) > 0
    unscoped = data.query("select nct_id, indication_profile_id from main_marts.dim_trial")
    assert len(unscoped) == sum(both.values()), "readers dropped or duplicated rows"
    assert unscoped["nct_id"].nunique() == 10, "fixture guarantees full NCT overlap"


def test_overview_metrics_counts_only_its_profile(fixture_project_root) -> None:
    data = _load_data_module()
    for profile in PROFILES:
        metrics = data.overview_metrics(profile)
        assert int(metrics["total_trials"]) == len(data.trial_explorer(profile))

    # The KPI Task 11 recorded as blended: `states_with_sites` is a count of
    # `dim_geography`, whose grain became (profile, state) in Task 11, so an
    # unscoped count adds the other profile's states onto this one's.
    adrd = data.overview_metrics("adrd")
    nsclc = data.overview_metrics("oncology_nsclc")
    assert int(adrd["states_with_sites"]) == ADRD_STATES_WITH_SITES
    blended = data.query("select count(*) as n from main_marts.dim_geography").iloc[0]["n"]
    assert int(adrd["states_with_sites"]) < int(blended), (
        "scoped geography KPI equals the unscoped count: the profile predicate is not binding"
    )
    assert int(adrd["states_with_sites"]) + int(nsclc["states_with_sites"]) == int(blended), (
        "the two profiles' scoped KPIs should account for every geography row"
    )


def test_trial_similarity_stays_inside_its_profile(fixture_project_root) -> None:
    data = _load_data_module()
    for profile in PROFILES:
        index_trial = data.trial_explorer(profile).iloc[0]["nct_id"]
        matches = data.trial_similarity(profile, index_trial)
        assert not matches.empty, f"{profile} has no similarity rows"
        assert set(matches["indication_profile_id"]) == {profile}


def test_query_binds_parameters_and_never_interpolates(fixture_project_root) -> None:
    """A profile id arriving as SQL text would make this return every row; as a
    bound parameter it returns none.
    """
    data = _load_data_module()
    hostile = "adrd' or '1'='1"
    frame = data.query(
        "select nct_id from main_marts.dim_trial where indication_profile_id = ?",
        [hostile],
    )
    assert frame.empty
