"""Read-only DuckDB access for the dashboard (never writes to the warehouse)."""

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


def require_warehouse() -> None:
    """Verify the DuckDB warehouse file exists, stopping execution if missing.

    Displays a Streamlit error message with instructions to build the warehouse
    and halts page rendering via ``st.stop()`` if the database is not found.
    """
    if not warehouse_path().exists():
        st.error(
            "Warehouse not found. Build it first:\n\n"
            "```\nmake ingest && make transform && make dbt-run\n```"
        )
        st.stop()


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
def query(sql: str) -> pd.DataFrame:
    """Execute a read-only SQL query against DuckDB and return materialized results.

    Results are cached for 10 minutes (TTL 600 seconds) in Streamlit cache.

    Args:
        sql: SQL query string to execute.

    Returns:
        pd.DataFrame: Query result DataFrame with sanitized data types.
    """
    return _materialize(_connection().execute(sql).df())


def priority_queue() -> pd.DataFrame:
    """Fetch the feasibility priority queue mart ordered by priority rank.

    Returns:
        pd.DataFrame: Feasibility queue records containing trial keys, priority
            scores, and ranking factors.
    """
    return query("select * from main_marts.mart_feasibility_priority_queue order by priority_rank")


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
def trial_similarity(nct_id: str) -> pd.DataFrame:
    """Fetch pairwise trial similarity scores for a given index trial NCT ID.

    Queries ``main_marts.mart_trial_similarity`` for candidate trials compared
    against ``nct_id``, ordered by descending similarity score / ascending rank.

    Args:
        nct_id: ClinicalTrials.gov NCT identifier of the index trial (e.g. "NCT01234567").

    Returns:
        pd.DataFrame: Similarity records with similarity rank, composite score,
            and component factor scores. Empty DataFrame if the similarity mart
            has not been built yet.
    """
    if not _table_exists("mart_trial_similarity"):
        return pd.DataFrame()
    return _materialize(
        _connection()
        .execute(
            "select * from main_marts.mart_trial_similarity"
            " where nct_id_a = ? order by similarity_rank",
            [nct_id],
        )
        .df()
    )


def recruiting_competition() -> pd.DataFrame:
    """Fetch the latest recruiting competition metrics across indication segments.

    Filters for records matching the latest available snapshot date in
    ``main_marts.mart_recruiting_competition``.

    Returns:
        pd.DataFrame: Active recruiting competition indicators by geographic and
            indication segment.
    """
    return query(
        "select * from main_marts.mart_recruiting_competition "
        "where snapshot_date = (select max(snapshot_date) "
        "from main_marts.mart_recruiting_competition)"
    )


def condition_geography_trends() -> pd.DataFrame:
    """Fetch longitudinal condition and geography trend metrics ordered by month.

    Returns:
        pd.DataFrame: Historical trend records from
            ``main_marts.mart_condition_geography_trends`` ordered by ``activity_month``.
    """
    return query("select * from main_marts.mart_condition_geography_trends order by activity_month")


def site_overlap() -> pd.DataFrame:
    """Fetch trial site overlap metrics for the latest snapshot date.

    Queries ``main_marts.mart_site_overlap`` for facility co-location and trial
    congestion, ordered by recruiting trial count and listed trial count descending.

    Returns:
        pd.DataFrame: Facility site overlap records for the latest snapshot.
    """
    return query(
        "select * from main_marts.mart_site_overlap "
        "where snapshot_date = (select max(snapshot_date) from main_marts.mart_site_overlap) "
        "order by recruiting_trial_count desc, listed_trial_count desc"
    )


def sponsor_landscape() -> pd.DataFrame:
    """Aggregate active recruiting trial counts and phase mix by lead sponsor.

    Queries ``main_marts.dim_trial`` and ``main_marts.bridge_trial_sponsor`` for
    recruiting trials, returning sponsor organization classification and phase breakdown.

    Returns:
        pd.DataFrame: Summary DataFrame with columns ``lead_sponsor``,
            ``sponsor_class``, ``recruiting_trial_count``, and ``phase_mix``.
    """
    return query(
        """
        select
            d.current_lead_sponsor as lead_sponsor,
            any_value(s.sponsor_class) as sponsor_class,
            count(distinct d.nct_id) as recruiting_trial_count,
            string_agg(distinct d.current_phase, ' | ' order by d.current_phase) as phase_mix
        from main_marts.dim_trial d
        left join main_marts.bridge_trial_sponsor s
            on d.trial_key = s.trial_key and s.lead_sponsor_flag
        where d.current_overall_status = 'RECRUITING'
        group by 1
        order by recruiting_trial_count desc, lead_sponsor
        """
    )


def trial_explorer() -> pd.DataFrame:
    """Fetch denormalized trial registry records and active site states for browsing.

    Combines ``dim_trial`` with active US site states from ``fct_trial_site``
    for the latest snapshot date.

    Returns:
        pd.DataFrame: Trial records containing NCT ID, indication profile ID (if available),
            brief title, overall status, phase, lead sponsor, post date, enrollment,
            and comma-delimited US state locations.
    """
    has_profile = _has_column("dim_trial", "indication_profile_id")
    profile_col = "d.indication_profile_id,\n" if has_profile else ""
    return query(
        f"""
        select
            d.nct_id,
            {profile_col}            d.registry_url,
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
            on d.nct_id = s.nct_id
            and s.snapshot_date = (select max(snapshot_date) from main_marts.fct_trial_site)
            and regexp_matches(s.state_normalized, '^[A-Z]{2}$')
        group by all
        order by d.study_first_post_date desc nulls last, d.nct_id
        """
    )


@st.cache_data(ttl=600)
def get_indication_profiles() -> list[dict[str, str]]:
    """Return available indication profiles present in dim_trial.

    Discovers distinct indication profile IDs populated in ``main_marts.dim_trial``
    and resolves their human-readable display names from the profile registry.

    Returns:
        list[dict[str, str]]: List of dictionaries with keys ``id`` (e.g. "adrd")
            and ``display_name`` (e.g. "Alzheimer's Disease & ADRD"). Empty list
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


def data_reliability() -> pd.DataFrame:
    """Fetch ingestion and data pipeline reliability metrics.

    Returns:
        pd.DataFrame: Reliability metrics from ``main_marts.mart_data_reliability``
            ordered by snapshot date and ingestion run ID descending.
    """
    return query(
        "select * from main_marts.mart_data_reliability "
        "order by snapshot_date desc, ingestion_run_id desc"
    )


def overview_metrics() -> dict[str, Any]:
    """Compute high-level summary KPIs across trials, sites, and snapshot runs.

    Returns:
        dict: Mapping of metric keys to counts/dates:
            - ``total_trials``: Total number of trials across indications.
            - ``recruiting_trials``: Number of trials currently in 'RECRUITING' status.
            - ``states_with_sites``: Distinct geographic states hosting trial sites.
            - ``listed_facilities``: Distinct facility locations tracked.
            - ``latest_snapshot``: Date of the most recent snapshot.
            - ``snapshot_count``: Total number of unique snapshot runs.
    """
    row = query(
        """
        select
            (select count(*) from main_marts.dim_trial) as total_trials,
            (select count(*) from main_marts.dim_trial
             where current_overall_status = 'RECRUITING') as recruiting_trials,
            (select count(*) from main_marts.dim_geography) as states_with_sites,
            (select count(*) from main_marts.mart_site_overlap) as listed_facilities,
            (select max(snapshot_date) from main_marts.fct_trial_snapshot) as latest_snapshot,
            (select count(distinct snapshot_date)
             from main_marts.fct_trial_snapshot) as snapshot_count
        """
    ).iloc[0]
    return {str(k): v for k, v in row.to_dict().items()}
