"""The dashboard may only read one indication profile at a time.

Two kinds of check live here. Static guards read `dashboard/` source and enforce
the rules a green page run cannot see (a page may not discard the selector's
return value; a reader may not put a profile id into SQL text; trial joins go on
`trial_key`, not `nct_id`). Behavioural checks read the two committed fixture
warehouses: `fixture_project_root` (both profiles, one snapshot date) and
`divergent_fixture_root` (both profiles, ADRD a week ahead).
"""

import ast
import inspect
import re
import sys
from pathlib import Path
from unittest import mock

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
DATA_SOURCE = DASHBOARD / "components" / "data.py"


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


def test_every_page_binds_the_selector_return_value() -> None:
    """The selector *returns* the picked profile id, and the page must use it.

    `test_every_page_renders_the_profile_selector` only greps for the call, so the
    shape `render_profile_selector(); profile_id = "adrd"` passes it: the widget is
    still rendered, still keyed, still holds whatever the user picked, and the page
    still runs -- it just shows one fixed indication no matter what is selected. The
    smoke suite's `landed.value == profile_id` proves the widget kept the value, not
    that the page read it. This pins the assignment every page actually uses.
    """
    binding = re.compile(r"^profile_id = render_profile_selector\(\)$", re.M)
    for script in PAGES:
        source = (ROOT / script).read_text(encoding="utf-8")
        bound = binding.search(source) is not None
        assert bound, f"{script} does not bind profile_id from render_profile_selector()"


# The three registry ids. `full_catalog` is one of them: a reader could hard-wire
# any of the three and stay behaviourally correct for the two it renders.
PROFILE_IDS = ("adrd", "oncology_nsclc", "full_catalog")

# Any comparison operator applied to the profile column, and the one shape that is
# allowed: `= ?`. Select-list mentions (`d.indication_profile_id,`) match neither,
# which is why the pair rather than a single regex does the work.
_PROFILE_COMPARISON = re.compile(
    r"indication_profile_id\s*(?:=|!=|<>|<=|>=|<|>|\b(?:in|like)\b)", re.I
)
_PROFILE_COMPARISON_BOUND = re.compile(r"indication_profile_id\s*=\s*\?", re.I)
_PROFILE_ID_IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(PROFILE_IDS) + r")(?![A-Za-z0-9_])"
)
# Trial identity is `trial_key` (Task 9: md5(nct_id, indication_profile_id));
# `nct_id` alone is shared across profiles.
_NCT_ID_TO_NCT_ID = re.compile(r"\b(?:\w+\.)?nct_id\b\s*=\s*(?:\w+\.)?nct_id\b", re.I)

# Measured 2026-09-06: nine `query(...)` calls plus the one `_connection().execute`
# in `profile_trial_count`. A reader whose SQL stops being a literal at a call site
# drops the count, which is what stops the guards below going vacuous.
_SQL_LITERAL_FLOOR = 10


def _sql_arguments(tree: ast.AST) -> list[tuple[int, ast.expr]]:
    """`(call line, SQL argument)` for every call that hands text to DuckDB.

    Only the two shapes that reach the database are collected — `query(...)` and
    `conn.execute(...)` — so prose that names tables (several docstrings do) and
    the `st.warning` message are never mistaken for SQL text.
    """
    found: list[tuple[int, ast.expr]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in {"query", "execute"}:
            found.append((node.lineno, node.args[0]))
    return found


def _data_sql() -> list[tuple[int, str]]:
    """`(call line, SQL text)` for every literal SQL string in `data.py`."""
    tree = ast.parse(DATA_SOURCE.read_text(encoding="utf-8"))
    return [
        (line, arg.value)
        for line, arg in _sql_arguments(tree)
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]


def test_profile_scope_reaches_duckdb_only_as_a_bind_parameter() -> None:
    """The `?` rule, enforced over the whole reader module instead of assumed.

    Every part of the dashboard's contract rests on the profile id reaching
    DuckDB as a *parameter*, and the mutation that breaks it is invisible to
    behaviour: `priority_queue` rewritten to
    `f"... where indication_profile_id = '{profile_id}'"` with empty binds still
    returns exactly the right rows, still carries the profile column, and passes
    all nine page runs. `test_query_binds_parameters_and_never_interpolates`
    covers the executor; this covers the readers, which is where the rule lives.
    """
    tree = ast.parse(DATA_SOURCE.read_text(encoding="utf-8"))
    offenders: list[str] = []
    literals = 0
    for line, arg in _sql_arguments(tree):
        where = f"dashboard/components/data.py:{line}"
        if isinstance(arg, ast.Name):
            continue  # `query()`'s own body forwards its parameter; nothing else may
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            offenders.append(f"{where}: SQL is built at runtime ({type(arg).__name__})")
            continue
        literals += 1
        text = arg.value
        for match in _PROFILE_COMPARISON.finditer(text):
            if _PROFILE_COMPARISON_BOUND.match(text, match.start()) is None:
                clause = text[match.start() :].splitlines()[0]
                offenders.append(
                    f"{where}: profile column compared without a bind parameter: {clause!r}"
                )
        for match in _PROFILE_ID_IN_TEXT.finditer(text):
            offenders.append(f"{where}: profile id {match.group(0)!r} written into SQL text")
    report = "\n".join(offenders)
    assert not offenders, f"profile scope must reach DuckDB as a ? parameter:\n{report}"
    assert literals >= _SQL_LITERAL_FLOOR, (
        f"only {literals} literal SQL strings reach DuckDB in data.py; "
        "the binding guard no longer sees the readers"
    )


def test_no_reader_joins_trials_on_nct_id() -> None:
    """`trial_key` is the per-profile trial identity; `nct_id` is not.

    This class has no discriminating fixture. The two profiles hold the same ten
    `nct_id`s with the same site states, so `d.trial_key = s.trial_key` and
    `d.nct_id = s.nct_id` return byte-identical results on both committed
    warehouses — re-measured 2026-09-06, zero differing rows on either profile on
    either fixture. The comment at `data.py` ("an nct_id-only join would attach
    the other profile's sites") is therefore guarded in source, and the residual
    is disclosed in the task report rather than claimed as covered.
    """
    offenders = [
        f"dashboard/components/data.py:{line}: join compares an nct_id to an nct_id"
        for line, text in _data_sql()
        if _NCT_ID_TO_NCT_ID.search(text)
    ]
    assert not offenders, "trial identity joins must use trial_key:\n" + "\n".join(offenders)


PROFILES = ("adrd", "oncology_nsclc")

# Measured on this fixture, read-only: `dim_geography` holds 9 rows for `adrd` and
# 18 across both profiles, so the unscoped KPI showed every profile's states as
# one number. Recorded here because Task 11 left it to a KPI card to notice.
ADRD_STATES_WITH_SITES = 9

# The same blend problem on the four `overview_metrics` tiles nothing pinned. A
# dict of scalars carries no profile column for the set-difference check to
# inspect, so a pinned number is the only thing that can see a wrong one.
# Measured 2026-09-06, read-only on this fixture: both profiles hold 3 recruiting
# trials and 14 site-overlap rows, and with the profile predicate removed those
# become 6 and 28 — the blend each pin below excludes.
PINNED_OVERVIEW_TILES = {
    "recruiting_trials": 3,
    "listed_facilities": 14,
}
# The unscoped twin of each tile, for the assertion that the pin still
# discriminates: if the fixture ever makes scoped == blended, the pin would go
# on passing while seeing nothing.
BLENDED_OVERVIEW_SQL = {
    "recruiting_trials": (
        "select count(*) as n from main_marts.dim_trial where current_overall_status = 'RECRUITING'"
    ),
    "listed_facilities": "select count(*) as n from main_marts.mart_site_overlap",
}

# `dim_trial` rows per profile here. `profile_trial_count` decides the one branch
# of `require_warehouse` that no page run can reach, so its input is pinned too.
BASE_PROFILE_TRIALS = 10


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


@pytest.fixture
def divergent_warehouse(
    divergent_fixture_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Re-point ``data`` at the second warehouse: ADRD refreshed to 2026-09-08,
    NSCLC a week behind.

    Non-autouse, so pytest instantiates it after the fixture above and its
    ``CTI_PROJECT_ROOT`` is the one the readers resolve. Each test that uses it
    re-asserts ``DIVERGENT_ADRD_TRIALS`` first: if the ordering ever reversed, the
    readers would silently measure the base warehouse and every assertion here
    would pass on the wrong data.
    """
    from src.config import get_config

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(divergent_fixture_root))
    get_config.cache_clear()
    return divergent_fixture_root


# Measured 2026-09-06, read-only against `divergent_fixture_root`. ADRD owns two
# snapshots (09-01, 09-08) and 27 trials; NSCLC owns one (09-01) and 10. So the
# *global* max is 09-08, ADRD's, which makes NSCLC the stale profile: a shared
# max() leaves it with zero current rows, and asserting the ADRD side of that
# would pass under the mutation.
DIVERGENT_ADRD_TRIALS = 27
DIVERGENT_GLOBAL_LATEST_SNAPSHOT = "2026-09-08"
NSCLC_OWN_LATEST_SNAPSHOT = "2026-09-01"
NSCLC_OWN_SNAPSHOT_COUNT = 1
NSCLC_RECRUITING_COMPETITION_ROWS = 4
NSCLC_SITE_OVERLAP_ROWS = 14
NSCLC_TRIAL_EXPLORER_ROWS = 10


def _snapshot_dates(frame) -> set[str]:
    """The distinct ``snapshot_date`` values in `frame`, as ISO date strings."""
    return {str(value)[:10] for value in frame["snapshot_date"]}


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
    # One report per run, naming reader *and* profile: seven readers across two
    # profiles, and the first `assert` used to hide every regression behind it
    # while naming nothing but the reader.
    failures: list[str] = []
    for profile in PROFILES:
        for reader in readers:
            label = f"{reader.__name__}({profile})"
            frame = reader(profile)
            if frame.empty:
                failures.append(f"{label} returned no rows")
                continue
            if "indication_profile_id" not in frame.columns:
                failures.append(f"{label} dropped the profile column that names its own scope")
                continue
            leaked = set(frame["indication_profile_id"]) - {profile}
            if leaked:
                failures.append(f"{label} returned rows belonging to {sorted(leaked)}")
    assert not failures, "readers crossed the profile boundary:\n" + "\n".join(failures)

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

    # The tiles `states_with_sites` was the pattern for. Two of the six are left
    # out on purpose: `latest_snapshot` and `snapshot_count` are identical scoped
    # and unscoped on *this* warehouse (one shared snapshot date, so 2026-09-01
    # and 1 either way — measured 2026-09-06), which means no pin here could
    # fail. They are pinned against `divergent_fixture_root` instead, below.
    for tile, pinned in PINNED_OVERVIEW_TILES.items():
        scoped = {p: int(data.overview_metrics(p)[tile]) for p in PROFILES}
        assert scoped == dict.fromkeys(PROFILES, pinned), (
            f"{tile} moved off its per-profile value of {pinned}: {scoped}"
        )
        blend = int(data.query(BLENDED_OVERVIEW_SQL[tile]).iloc[0]["n"])
        assert blend > pinned, (
            f"{tile}'s unscoped count is no larger than one profile's: this fixture "
            "cannot show a blend any more"
        )
        assert sum(scoped.values()) == blend, (
            f"{tile}: the two profiles' scoped counts do not account for every row"
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

    Scope to be honest about: this exercises the *executor* — it fails if
    `query()` ever substitutes values into the SQL text itself. It cannot see a
    reader that interpolates before calling `query()` with nothing to bind, which
    is what `test_profile_scope_reaches_duckdb_only_as_a_bind_parameter` guards.
    """
    data = _load_data_module()
    hostile = "adrd' or '1'='1"
    frame = data.query(
        "select nct_id from main_marts.dim_trial where indication_profile_id = ?",
        [hostile],
    )
    assert frame.empty


def test_overview_snapshot_tiles_track_their_own_profile(divergent_warehouse) -> None:
    """The two tiles the base fixture cannot pin, pinned where they discriminate.

    `overview_metrics` resolves `latest_snapshot` and `snapshot_count` from
    `fct_trial_snapshot` scoped to the profile. Here ADRD has refreshed twice and
    NSCLC once, so NSCLC's own newest date (2026-09-01) is strictly below the
    global one (2026-09-08, ADRD's) — a shared max() would date NSCLC's overview a
    week ahead of data it does not have. ADRD's own two tiles equal the global
    values on this fixture too (measured 2026-09-06), so only the NSCLC side is
    asserted.
    """
    data = _load_data_module()
    # The anchor: 27 rows exist only on this warehouse, so a fixture that stopped
    # taking effect fails here rather than quietly re-measuring the base one.
    assert data.profile_trial_count("adrd") == DIVERGENT_ADRD_TRIALS

    nsclc = data.overview_metrics("oncology_nsclc")
    assert str(nsclc["latest_snapshot"])[:10] == NSCLC_OWN_LATEST_SNAPSHOT, (
        "NSCLC's overview carries a snapshot date it has no rows at: max() is global, "
        f"not per profile ({DIVERGENT_GLOBAL_LATEST_SNAPSHOT} belongs to adrd)"
    )
    assert int(nsclc["snapshot_count"]) == NSCLC_OWN_SNAPSHOT_COUNT, (
        "NSCLC's overview counts ADRD's second snapshot as its own"
    )
    assert int(data.overview_metrics("adrd")["total_trials"]) == DIVERGENT_ADRD_TRIALS, (
        f"adrd's trial tile left {DIVERGENT_ADRD_TRIALS}; both profiles together are 37"
    )


def test_the_stale_profile_sees_only_its_own_latest_snapshot(divergent_warehouse) -> None:
    """The semantics `data.py` documents in two comments, on the fixture that has
    the date skew to show them.

    `recruiting_competition`, `site_overlap` and `trial_explorer` each resolve
    "latest" with a `max(snapshot_date)` subquery scoped to the profile, because
    one profile may have refreshed while the other has not. On the base warehouse
    both peak at 2026-09-01, so an unscoped subquery changes no row there; on this
    one the global max is ADRD's 2026-09-08 and NSCLC — the stale profile — is the
    side that goes empty. Asserting ADRD would pass under the mutation.
    """
    data = _load_data_module()
    assert data.profile_trial_count("adrd") == DIVERGENT_ADRD_TRIALS  # fixture anchor

    competition = data.recruiting_competition("oncology_nsclc")
    assert len(competition) == NSCLC_RECRUITING_COMPETITION_ROWS, (
        "NSCLC's competition mart was filtered to a snapshot date it does not own"
    )
    assert _snapshot_dates(competition) == {NSCLC_OWN_LATEST_SNAPSHOT}

    overlap = data.site_overlap("oncology_nsclc")
    assert len(overlap) == NSCLC_SITE_OVERLAP_ROWS, (
        "NSCLC's site overlap mart was filtered to a snapshot date it does not own"
    )
    assert _snapshot_dates(overlap) == {NSCLC_OWN_LATEST_SNAPSHOT}

    explorer = data.trial_explorer("oncology_nsclc")
    assert len(explorer) == NSCLC_TRIAL_EXPLORER_ROWS
    states = explorer["us_states"]
    with_states = int((states.fillna("").astype(str).str.strip().str.len() > 0).sum())
    assert with_states == NSCLC_TRIAL_EXPLORER_ROWS, (
        f"{NSCLC_TRIAL_EXPLORER_ROWS - with_states} of NSCLC's trials lost their sites to "
        f"the site subquery resolving ADRD's {DIVERGENT_GLOBAL_LATEST_SNAPSHOT}"
    )


def test_require_warehouse_warns_rather_than_stops_on_an_empty_scope(
    fixture_project_root,
) -> None:
    """Direct call of the guard — not a page run — for the one state no page run
    reaches.

    `require_warehouse` stops when the warehouse is missing but only *warns* when
    the profile has no trials, because a scope between "config added" and "first
    refresh" is a real state and the pages tolerate zero-row frames. The fixture
    warehouse populates both viewable profiles, so no committed page run enters
    that branch; `full_catalog` is `ingest_only` and holds no `dim_trial` rows
    here (measured 2026-09-06), which makes it a reachable scope rather than a
    contrivance.
    """
    data = _load_data_module()
    # The branch is decided by these two counts, so they are pinned: delete the
    # predicate inside `profile_trial_count` and every profile reports the same
    # wrong total, which turns the warning below into a coin flip.
    assert data.profile_trial_count("adrd") == BASE_PROFILE_TRIALS
    assert data.profile_trial_count("full_catalog") == 0, "full_catalog is no longer an empty scope"

    with (
        mock.patch("streamlit.error") as error,
        mock.patch("streamlit.stop") as stop,
        mock.patch("streamlit.warning") as warning,
    ):
        data.require_warehouse("full_catalog")

    assert error.call_count == 0, "a warehouse that exists was reported missing"
    assert stop.call_count == 0, (
        "require_warehouse halted the page for a scope that is merely empty; it is meant "
        "to warn and let the zero-row frames render"
    )
    assert warning.call_count == 1, "the empty-profile warning did not fire"
    message = str(warning.call_args[0][0])
    assert "full_catalog" in message, (
        f"the warning does not name the scope it is about: {message!r}"
    )
