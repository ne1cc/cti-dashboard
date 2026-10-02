"""Read-only DuckDB access for the dashboard (never writes to the warehouse).

Every reader takes an indication profile id and binds it as a `?` parameter.
After the composite-grain migration an unscoped query does not fail — it blends
two indications into one plausible number, which is what this file exists to
make impossible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import streamlit as st

from src.config import get_config


def warehouse_path() -> Path:
    """Return the filesystem path to the DuckDB analytics warehouse.

    Returns:
        Path: Filesystem path to the DuckDB database file configured for the pipeline.
    """
    return get_config().paths.duckdb


@st.cache_resource
def _connection() -> duckdb.DuckDBPyConnection:
    """Open and cache a read-only DuckDB database connection.

    Returns:
        duckdb.DuckDBPyConnection: Read-only DuckDB connection handle.
    """
    return duckdb.connect(str(warehouse_path()), read_only=True)


def require_warehouse(profile_id: str | None = None) -> None:
    """Verify the warehouse exists and warn when the scope has no trials yet.

    Displays a Streamlit error with build instructions and halts page rendering
    via ``st.stop()`` if the database file is missing. When ``profile_id`` is
    given and that profile has no trials, a warning is shown instead of stopping:
    a profile between "config added" and "first refresh" is a real state, and the
    pages already tolerate zero-row frames.

    Args:
        profile_id: Active indication profile id, or ``None`` to skip the
            per-profile emptiness check.
    """
    if not warehouse_path().exists():
        st.error("Warehouse not found. Build it first:\n\n```\nmake pipeline\n```")
        st.stop()
    if (
        profile_id is not None
        and _has_column("dim_trial", "indication_profile_id")
        and profile_trial_count(profile_id) == 0
    ):
        st.warning(
            f"No trials recorded for `{profile_id}` yet. The refresh has not "
            "produced a successful run for this profile, so every figure below "
            "is empty. That is a missing run, not a zero count."
        )


@st.cache_data(ttl=600)
def profile_trial_count(profile_id: str) -> int:
    """Count the trials recorded for one indication profile.

    Args:
        profile_id: Indication profile id to scope the count to.

    Returns:
        int: Number of ``dim_trial`` rows carrying that profile id.
    """
    if not _has_column("dim_trial", "indication_profile_id"):
        return 0
    row = (
        _connection()
        .execute(
            "select count(*) from main_marts.dim_trial where indication_profile_id = ?",
            [profile_id],
        )
        .fetchone()
    )
    return int(row[0]) if row else 0


def _materialize(df: pd.DataFrame) -> pd.DataFrame:
    """Convert PyArrow-backed data types in a DataFrame to standard pandas types.

    DuckDB returns PyArrow-backed string and nullable integer columns which can
    cause SIGSEGV crashes in Streamlit's dataframe renderer. Converts string
    columns to ``object`` and nullable integer columns (`Int32`, `Int64`) to ``float64``.

    Args:
        df: DataFrame returned directly from DuckDB query execution.

    Returns:
        pd.DataFrame: Sanitized DataFrame safe for Streamlit visualization.
    """
    # DuckDB returns PyArrow-backed string and nullable integer columns which
    # can cause SIGSEGV in Streamlit's dataframe renderer. Convert to standard
    # pandas object/float64 dtypes.
    for col in df.select_dtypes(include=["string"]).columns:
        df[col] = df[col].astype("object")
    for col in df.select_dtypes(include=["Int32", "Int64"]).columns:
        df[col] = df[col].astype("float64")
    return df


@st.cache_data(ttl=600)
def query(sql: str, params: list[object] | None = None) -> pd.DataFrame:
    """Execute a read-only SQL query against DuckDB and return materialized results.

    Results are cached for 10 minutes (TTL 600 seconds) in Streamlit cache, keyed
    on both the SQL text and the bound parameters, so two profiles never share a
    cached frame. Values only ever reach DuckDB as bind parameters — never
    interpolated into the SQL text.

    Args:
        sql: SQL query string with ``?`` placeholders.
        params: Bind parameters for the placeholders.

    Returns:
        pd.DataFrame: Query result DataFrame with sanitized data types.
    """
    return _materialize(_connection().execute(sql, params or []).df())


def priority_queue(profile_id: str) -> pd.DataFrame:
    """Fetch the feasibility priority queue mart for one profile.

    Args:
        profile_id: Indication profile id to scope the mart to.

    Returns:
        pd.DataFrame: Feasibility queue records containing trial keys, priority
            scores, and ranking factors, ordered by priority rank.
    """
    return query(
        "select * from main_marts.mart_feasibility_priority_queue"
        " where indication_profile_id = ? order by priority_rank",
        [profile_id],
    )


@st.cache_data(ttl=600)
def _table_exists(table_name: str, schema: str = "main_marts") -> bool:
    """Check whether a table or view exists in the warehouse schema.

    Args:
        table_name: Name of the relation to check.
        schema: Database schema containing the table (default: ``main_marts``).

    Returns:
        bool: True if the table exists, False otherwise.
    """
    try:
        df = (
            _connection()
            .execute(
                "select 1 from information_schema.tables where table_schema = ? and table_name = ?",
                [schema, table_name],
            )
            .df()
        )
        return not df.empty
    except Exception:
        return False


@st.cache_data(ttl=600)
def _has_column(table_name: str, column_name: str, schema: str = "main_marts") -> bool:
    """Check whether a column exists on a table or view in the warehouse schema.

    Args:
        table_name: Name of the relation to inspect.
        column_name: Name of the column to check for.
        schema: Database schema containing the table (default: ``main_marts``).

    Returns:
        bool: True if the column exists on the relation, False otherwise.
    """
    try:
        df = (
            _connection()
            .execute(
                "select 1 from information_schema.columns "
                "where table_schema = ? and table_name = ? and column_name = ?",
                [schema, table_name, column_name],
            )
            .df()
        )
        return not df.empty
    except Exception:
        return False


@st.cache_data(ttl=600)
def trial_similarity(profile_id: str, nct_id: str | None = None) -> pd.DataFrame:
    """Fetch pairwise trial similarity scores for one profile and index trial.

    Queries ``main_marts.mart_trial_similarity`` for candidate trials compared
    against ``nct_id``, ordered by descending similarity score / ascending rank.

    Args:
        profile_id: Indication profile id to scope the scores to.
        nct_id: ClinicalTrials.gov NCT identifier of the index trial (e.g. "NCT01234567").

    Returns:
        pd.DataFrame: Similarity records with similarity rank, composite score,
            and component factor scores. Empty DataFrame if the similarity mart
            has not been built yet.
    """
    if nct_id is None:
        nct_id = profile_id
        profile_id = "adrd"
    if not _table_exists("mart_trial_similarity"):
        return pd.DataFrame()
    return query(
        "select * from main_marts.mart_trial_similarity"
        " where indication_profile_id = ? and nct_id_a = ? order by similarity_rank",
        [profile_id, nct_id],
    )


def recruiting_competition(profile_id: str) -> pd.DataFrame:
    """Fetch the latest recruiting competition metrics for one profile.

    Args:
        profile_id: Indication profile id to scope the metrics to.

    Returns:
        pd.DataFrame: Active recruiting competition indicators by geographic and
            indication segment, at that profile's own latest snapshot date.
    """
    # "latest snapshot" is per profile: one profile may have refreshed and the
    # other not, and a shared max() would show the stale profile's newest date
    # as if it had data.
    return query(
        "select * from main_marts.mart_recruiting_competition"
        " where indication_profile_id = ?"
        "   and snapshot_date = ("
        "     select max(snapshot_date) from main_marts.mart_recruiting_competition"
        "     where indication_profile_id = ?)",
        [profile_id, profile_id],
    )


def condition_geography_trends(profile_id: str) -> pd.DataFrame:
    """Fetch longitudinal condition and geography trend metrics ordered by month.

    Args:
        profile_id: Indication profile id to scope the trends to.

    Returns:
        pd.DataFrame: Historical trend records from
            ``main_marts.mart_condition_geography_trends`` ordered by ``activity_month``.
    """
    return query(
        "select * from main_marts.mart_condition_geography_trends"
        " where indication_profile_id = ? order by activity_month",
        [profile_id],
    )


def site_overlap(profile_id: str) -> pd.DataFrame:
    """Fetch trial site overlap metrics for one profile's latest snapshot date.

    Queries ``main_marts.mart_site_overlap`` for facility co-location and trial
    congestion, ordered by recruiting trial count and listed trial count descending.

    Args:
        profile_id: Indication profile id to scope the overlap to.

    Returns:
        pd.DataFrame: Facility site overlap records for that profile's latest snapshot.
    """
    return query(
        "select * from main_marts.mart_site_overlap"
        " where indication_profile_id = ?"
        "   and snapshot_date = ("
        "     select max(snapshot_date) from main_marts.mart_site_overlap"
        "     where indication_profile_id = ?)"
        " order by recruiting_trial_count desc, listed_trial_count desc",
        [profile_id, profile_id],
    )


def sponsor_landscape(profile_id: str) -> pd.DataFrame:
    """Aggregate active recruiting trial counts and phase mix by lead sponsor.

    Queries ``main_marts.dim_trial`` and ``main_marts.bridge_trial_sponsor`` for
    recruiting trials, returning sponsor organization classification and phase breakdown.

    Args:
        profile_id: Indication profile id to scope the aggregation to.

    Returns:
        pd.DataFrame: Summary DataFrame with columns ``indication_profile_id``,
            ``lead_sponsor``, ``sponsor_class``, ``recruiting_trial_count``, and
            ``phase_mix``.
    """
    # No profile predicate on the join: bridge_trial_sponsor.trial_key is
    # md5(nct_id, indication_profile_id) (Task 9), so it cannot match across
    # profiles. The column is selected so the frame says which scope it is.
    return query(
        """
        select
            d.indication_profile_id,
            d.current_lead_sponsor as lead_sponsor,
            any_value(s.sponsor_class) as sponsor_class,
            count(distinct d.nct_id) as recruiting_trial_count,
            string_agg(distinct d.current_phase, ' | ' order by d.current_phase) as phase_mix
        from main_marts.dim_trial d
        left join main_marts.bridge_trial_sponsor s
            on d.trial_key = s.trial_key and s.lead_sponsor_flag
        where d.current_overall_status = 'RECRUITING'
            and d.indication_profile_id = ?
        group by 1, 2
        order by recruiting_trial_count desc, lead_sponsor
        """,
        [profile_id],
    )


def trial_explorer(profile_id: str = "adrd") -> pd.DataFrame:
    """Fetch denormalized trial registry records and active site states for browsing.

    Combines ``dim_trial`` with active US site states from ``fct_trial_site``
    for that profile's latest snapshot date.

    Args:
        profile_id: Indication profile id to scope the browser to.

    Returns:
        pd.DataFrame: Trial records containing NCT ID, indication profile ID (if available),
            brief title, overall status, phase, lead sponsor, post date, enrollment,
            and comma-delimited US state locations.
    """
    # Joins on trial_key, not nct_id: an nct_id-only join would attach the
    # other profile's sites to a shared trial and show them as its own.
    if not _has_column("dim_trial", "indication_profile_id"):
        return query(
            """
            select
                d.nct_id,
                d.registry_url,
                d.current_brief_title as brief_title,
                d.current_overall_status as overall_status,
                d.current_phase as phase,
                d.current_lead_sponsor as lead_sponsor,
                d.study_first_post_date,
                d.enrollment_count,
                cast('' as varchar) as us_states
            from main_marts.dim_trial d
            order by d.study_first_post_date desc nulls last, d.nct_id
            """
        )
    return query(
        """
        select
            d.nct_id,
            d.indication_profile_id,
            d.registry_url,
            d.current_brief_title as brief_title,
            d.current_overall_status as overall_status,
            d.current_phase as phase,
            d.current_lead_sponsor as lead_sponsor,
            d.study_first_post_date,
            d.enrollment_count,
            string_agg(distinct s.state_normalized, ', ' order by s.state_normalized)
                as us_states
        from main_marts.dim_trial d
        left join main_marts.fct_trial_site s
            on d.trial_key = s.trial_key
            and s.snapshot_date = (
                select max(snapshot_date) from main_marts.fct_trial_site
                where indication_profile_id = ?)
            and regexp_matches(s.state_normalized, '^[A-Z]{2}$')
        where d.indication_profile_id = ?
        group by all
        order by d.study_first_post_date desc nulls last, d.nct_id
        """,
        [profile_id, profile_id],
    )


@st.cache_data(ttl=600)
def get_indication_profiles(profile_id: str | None = None) -> list[dict[str, str]]:
    """Return available indication profiles present in dim_trial.

    Discovers distinct indication profile IDs populated in ``main_marts.dim_trial``
    and resolves their human-readable display names from the profile registry.

    Args:
        profile_id: Optional indication profile id filter.

    Returns:
        list[dict[str, str]]: List of dictionaries with keys ``id`` (e.g. "adrd")
            and ``display_name`` (e.g. "Alzheimer's Disease & Related Dementias"). Empty list
            if indication_profile_id is not present in dim_trial.
    """
    if not _has_column("dim_trial", "indication_profile_id"):
        return []
    df = query(
        "select distinct indication_profile_id from main_marts.dim_trial "
        "where indication_profile_id is not null order by 1"
    )
    from src.profiles import get_registry

    try:
        reg = get_registry()
    except Exception:
        reg = None

    results = []
    for pid in df["indication_profile_id"].dropna():
        pid_str = str(pid)
        display = pid_str
        if reg:
            try:
                display = reg.get(pid_str).display_name
            except KeyError:
                pass
        results.append({"id": pid_str, "display_name": display})
    return results


def data_reliability(profile_id: str) -> pd.DataFrame:
    """Fetch ingestion and data pipeline reliability metrics for one profile.

    Args:
        profile_id: Indication profile id to scope the runs to.

    Returns:
        pd.DataFrame: Reliability metrics from ``main_marts.mart_data_reliability``
            ordered by snapshot date and ingestion run ID descending.
    """
    return query(
        "select * from main_marts.mart_data_reliability"
        " where indication_profile_id = ?"
        " order by snapshot_date desc, ingestion_run_id desc",
        [profile_id],
    )


def overview_metrics(profile_id: str) -> dict[str, Any]:
    """Compute high-level summary KPIs for one indication profile.

    Args:
        profile_id: Indication profile id every subquery is scoped to.

    Returns:
        dict: Mapping of metric keys to counts/dates:
            - ``total_trials``: Total number of trials in this profile.
            - ``recruiting_trials``: Number of trials currently in 'RECRUITING' status.
            - ``states_with_sites``: Geographic rows in ``dim_geography`` for this profile.
            - ``listed_facilities``: Facility locations tracked in ``mart_site_overlap``.
            - ``latest_snapshot``: Date of this profile's most recent snapshot.
            - ``snapshot_count``: Number of unique snapshot runs for this profile.
    """
    row = query(
        """
        select
            (select count(*) from main_marts.dim_trial
             where indication_profile_id = ?) as total_trials,
            (select count(*) from main_marts.dim_trial
             where indication_profile_id = ?
               and current_overall_status = 'RECRUITING') as recruiting_trials,
            (select count(*) from main_marts.dim_geography
             where indication_profile_id = ?) as states_with_sites,
            (select count(*) from main_marts.mart_site_overlap
             where indication_profile_id = ?) as listed_facilities,
            (select max(snapshot_date) from main_marts.fct_trial_snapshot
             where indication_profile_id = ?) as latest_snapshot,
            (select count(distinct snapshot_date)
             from main_marts.fct_trial_snapshot
             where indication_profile_id = ?) as snapshot_count
        """,
        [profile_id] * 6,
    ).iloc[0]
    res = {str(k): v for k, v in row.to_dict().items()}
    res["warehouse_runs"] = res.get("snapshot_count")
    return res


def _audit_unavailable() -> pd.DataFrame:
    frame = pd.DataFrame()
    frame.attrs["audit_unavailable"] = (
        "Audit marts unavailable; rebuild the warehouse with the current pipeline and dbt models."
    )
    return frame


def audit_studies(profile_id: str, snapshot_ids: list[str]) -> pd.DataFrame:
    """Read captured studies for explicit run IDs; an empty selection means no rows."""
    if not _table_exists("mart_study_snapshot_audit"):
        return _audit_unavailable()
    return query(
        "select * from main_marts.mart_study_snapshot_audit "
        "where indication_profile_id = ? and snapshot_id in (select unnest(?)) "
        "order by snapshot_date, snapshot_id, nct_id",
        [profile_id, snapshot_ids],
    )


def audit_locations(profile_id: str, snapshot_ids: list[str]) -> pd.DataFrame:
    """Read original recorded-location rows, retaining their ordinal and provenance."""
    if not _table_exists("mart_location_snapshot_audit"):
        return _audit_unavailable()
    return query(
        "select * from main_marts.mart_location_snapshot_audit "
        "where indication_profile_id = ? and snapshot_id in (select unnest(?)) "
        "order by snapshot_id, nct_id, location_ordinal",
        [profile_id, snapshot_ids],
    )


def audit_condition_mapping(profile_id: str) -> pd.DataFrame:
    """Load mappings before geographic exclusions, keyed by captured run and study."""
    if not _table_exists("int_trial_condition_mapping", "main_intermediate"):
        return _audit_unavailable()
    return query(
        "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, condition_group "
        "from main_intermediate.int_trial_condition_mapping where indication_profile_id = ?",
        [profile_id],
    )


def metric_audit_observations(profile_id: str, metric: str = "competition") -> pd.DataFrame:
    """Return actual per-study observations before status/geographic exclusions.

    Competition/facility use their mart's latest date, history keeps daily
    observations, and latest_study follows the current per-study status dimension.
    Callers may further select dates/months, without inventing a newest run.
    """
    if not _table_exists("mart_study_snapshot_audit"):
        return _audit_unavailable()
    if metric == "latest_study":
        return query(
            "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, snapshot_date "
            "from main_intermediate.int_current_trial_status where indication_profile_id = ?",
            [profile_id],
        )
    if metric == "history":
        return query(
            "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, snapshot_date "
            "from main_intermediate.int_trial_status_history where indication_profile_id = ?",
            [profile_id],
        )
    if metric == "competition":
        return query(
            "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, snapshot_date "
            "from main_intermediate.int_trial_status_history where indication_profile_id = ? "
            "and snapshot_date = (select max(snapshot_date) from main_marts."
            "mart_recruiting_competition where indication_profile_id = ?)",
            [profile_id, profile_id],
        )
    if metric == "facility":
        return query(
            "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, snapshot_date "
            "from main_intermediate.int_trial_status_history where indication_profile_id = ? "
            "and snapshot_date = (select max(snapshot_date) from main_marts."
            "mart_site_overlap where indication_profile_id = ?)",
            [profile_id, profile_id],
        )
    raise ValueError(f"Unknown audit metric: {metric}")
