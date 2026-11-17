"""Read-only DuckDB access for the dashboard (never writes to the warehouse).

Preset readers use registry-grain marts and bind `preset_id`. Trial-level data is
scoped through `bridge_preset_trial`; condition reports retain the configured
`condition_group` overlay so presets with conflicting YAML taxonomies stay distinct.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import streamlit as st

from src.config import get_config
from src.presets import Preset
from src.quality.freshness import status as freshness_status


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


def _groups(preset: Preset) -> list[str]:
    """Return taxonomy group names used by registry marts for one YAML preset."""
    return [node.removeprefix("group:") for node in preset.node_ids]


def require_warehouse(preset: Preset | None = None) -> None:
    """Verify the warehouse exists and warn when the scope has no trials yet.

    Displays a Streamlit error with build instructions and halts page rendering
    via ``st.stop()`` if the database file is missing. When ``preset`` is
    given and it has no trials, a warning is shown instead of stopping:
    a preset between "config added" and "first refresh" is a real state, and the
    pages already tolerate zero-row frames.

    Args:
        preset: Active preset, or ``None`` to skip the per-preset emptiness check.
    """
    if not warehouse_path().exists():
        st.error("Warehouse not found. Build it first:\n\n```\nmake pipeline\n```")
        st.stop()
    if preset is not None and preset_trial_count(preset) == 0:
        if _nodes_missing(preset):
            st.warning(
                f"`{preset.preset_id}` has data, but none of its condition nodes "
                "(the taxonomy's `group:` ids) are in the warehouse. Check the profile's "
                "taxonomy for a renamed or misspelled group, then rebuild."
            )
        else:
            st.warning(
                f"No trials recorded for `{preset.preset_id}` yet. The refresh has not "
                "produced a successful run for this preset, so every figure below "
                "is empty. That is a missing run, not a zero count."
            )
    elif preset is not None:
        _freshness_banner()


def _freshness_banner() -> None:
    freshness = freshness_status("registry")
    if not freshness["ok"]:
        st.warning("Data freshness warning: " + "; ".join(freshness["errors"]))
    metadata = freshness["metadata"]
    success = metadata.get("last_success") if metadata else None
    if success and success.get("registry"):
        captured = success["registry"]["captured_at_utc"]
        st.caption(f"Last verified registry capture (UTC): {captured}")


@st.cache_data(ttl=600)
def _nodes_missing(preset: Preset) -> bool:
    """True when a preset has canonical trials but none of its YAML groups map."""
    if not _table_exists("int_registry_preset_condition_mapping", "main_intermediate"):
        return False
    row = (
        _connection()
        .execute(
            "select count(distinct nct_id) filter (where condition_group in (select unnest(?))), "
            "count(distinct nct_id) from main_intermediate.int_registry_preset_condition_mapping "
            "where preset_id = ?",
            [_groups(preset), preset.preset_id],
        )
        .fetchone()
    )
    return bool(row and row[1] > 0 and row[0] == 0)


@st.cache_data(ttl=600)
def preset_trial_count(preset: Preset) -> int:
    """Count the trials recorded for one preset.

    Args:
        preset: Preset whose condition nodes scope the count.

    Returns:
        int: Number of ``dim_trial`` rows whose trial sits under one of the nodes.
    """
    if not _table_exists("bridge_preset_trial"):
        return 0
    row = (
        _connection()
        .execute(
            "select count(distinct nct_id) from main_marts.bridge_preset_trial "
            "where preset_id = ? and current_record_flag",
            [preset.preset_id],
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


def priority_queue(preset: Preset) -> pd.DataFrame:
    """Fetch the feasibility priority queue mart for one preset.

    Args:
        preset: Preset whose condition keys scope the mart.

    Returns:
        pd.DataFrame: Feasibility queue records containing trial keys and
            raw segment metrics, ordered by active recruiting trials,
            distinct sponsors, and priority queue key.
    """
    if not _has_column(
        "mart_registry_feasibility_priority_queue", "operational_completeness_share"
    ):
        st.error(
            "Priority Queue is unavailable because the warehouse has an outdated "
            "mart. Rebuild it with `make pipeline`."
        )
        st.stop()
    return query(
        "select *, preset_id as indication_profile_id "
        "from main_marts.mart_registry_feasibility_priority_queue"
        " where preset_id = ? and condition_group in (select unnest(?))"
        " order by active_recruiting_trials desc, distinct_sponsors desc, priority_queue_key asc",
        [preset.preset_id, _groups(preset)],
    )


@st.cache_data(ttl=600)
def trials_for_segment(
    preset: Preset,
    condition_group: str,
    state_normalized: str,
    phase_normalized: str,
) -> pd.DataFrame:
    """Fetch all recruiting clinical trials belonging to a specific segment.

    Args:
        preset: Preset the segment must belong to.
        condition_group: Condition group identifier.
        state_normalized: Two-letter U.S. state abbreviation (e.g. ``'CA'``).
        phase_normalized: Normalized trial phase (e.g. ``'PHASE3'``).

    Returns:
        pd.DataFrame: Trial records with NCT ID, brief title, overall status, phase,
            lead sponsor, enrollment count, study first post date, and registry URL.
    """
    sql = """
        select distinct
            d.nct_id,
            d.current_brief_title as brief_title,
            d.current_overall_status as overall_status,
            d.current_phase as phase,
            d.current_lead_sponsor as lead_sponsor,
            d.enrollment_count,
            d.study_first_post_date,
            d.registry_url
        from main_marts.dim_registry_trial d
        inner join main_marts.bridge_preset_trial p
            on p.nct_id = d.nct_id and p.current_record_flag
        inner join main_intermediate.int_registry_preset_condition_mapping m
            on m.nct_id = d.nct_id and m.snapshot_id = p.snapshot_id
            and m.preset_id = p.preset_id
        inner join main_marts.fct_registry_trial_site s
            on s.nct_id = d.nct_id and s.snapshot_id = p.snapshot_id
        where p.preset_id = ?
          and m.condition_group = ?
          and s.snapshot_date = (
              select max(snapshot_date) from main_marts.bridge_preset_trial
              where preset_id = ? and current_record_flag)
          and s.state_normalized = ?
          and coalesce(d.current_phase, '') = coalesce(?, '')
          and d.current_overall_status = 'RECRUITING'
        order by d.enrollment_count desc nulls last, d.nct_id
    """
    return _materialize(
        _connection()
        .execute(
            sql,
            [
                preset.preset_id,
                condition_group,
                preset.preset_id,
                state_normalized,
                phase_normalized,
            ],
        )
        .df()
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
def trial_similarity(preset: Preset, nct_id: str) -> pd.DataFrame:
    """Fetch pairwise trial similarity scores for one preset and index trial.

    Queries ``main_marts.mart_trial_similarity`` for candidate trials compared
    against ``nct_id``, ordered by descending similarity score / ascending rank.

    Args:
        preset: Preset whose profile owns the scores (profile-grain mart).
        nct_id: ClinicalTrials.gov NCT identifier of the index trial (e.g. "NCT01234567").

    Returns:
        pd.DataFrame: Similarity records with similarity rank, composite score,
            and component factor scores. Empty DataFrame if the similarity mart
            has not been built yet.
    """
    if not _table_exists("mart_registry_trial_similarity"):
        return pd.DataFrame()
    required_columns = ("preset_id", "nct_id_a", "nct_id_b", "similarity_rank")
    if not all(
        _has_column("mart_registry_trial_similarity", column) for column in required_columns
    ):
        st.error(
            "Trial Similarity is unavailable because the warehouse has an outdated "
            "similarity mart. Rebuild it with `make pipeline`."
        )
        return pd.DataFrame()
    return query(
        "select *, preset_id as indication_profile_id "
        "from main_marts.mart_registry_trial_similarity"
        " where preset_id = ?"
        " and nct_id_a = ? order by similarity_rank",
        [preset.preset_id, nct_id],
    )


def recruiting_competition(preset: Preset) -> pd.DataFrame:
    """Fetch the latest recruiting competition metrics for one preset.

    Args:
        preset: Preset whose condition keys scope the metrics.

    Returns:
        pd.DataFrame: Active recruiting competition indicators by geographic and
            indication segment, at that preset's own latest snapshot date.
    """
    # A preset's own latest snapshot avoids showing another preset's refresh date.
    return query(
        "select *, preset_id as indication_profile_id "
        "from main_marts.mart_registry_recruiting_competition"
        " where preset_id = ? and condition_group in (select unnest(?))"
        "   and snapshot_date = ("
        "     select max(snapshot_date) from main_marts.mart_registry_recruiting_competition"
        "     where preset_id = ? and condition_group in (select unnest(?)))",
        [preset.preset_id, _groups(preset), preset.preset_id, _groups(preset)],
    )


def condition_geography_trends(preset: Preset) -> pd.DataFrame:
    """Fetch longitudinal condition and geography trend metrics ordered by month.

    Args:
        preset: Preset whose condition keys scope the trends.

    Returns:
        pd.DataFrame: Historical trend records from
            ``main_marts.mart_condition_geography_trends`` ordered by ``activity_month``.
    """
    return query(
        "select *, preset_id as indication_profile_id "
        "from main_marts.mart_registry_condition_geography_trends"
        " where preset_id = ? and condition_group in (select unnest(?))"
        " order by activity_month",
        [preset.preset_id, _groups(preset)],
    )


def access_rates(preset: Preset) -> pd.DataFrame:
    """Fetch population- and burden-adjusted recruiting access for one preset.

    Args:
        preset: Preset whose profile owns the rates (profile-grain mart).

    Returns:
        pd.DataFrame: Rows of ``mart_condition_access_rates`` (profile x MeSH
            condition x state); empty when the mart is not built yet.
    """
    if not _table_exists("mart_registry_condition_access_rates"):
        return pd.DataFrame()
    return query(
        "select ? as indication_profile_id, * "
        "from main_marts.mart_registry_condition_access_rates "
        "where registry_condition_key in ("
        "select distinct c.registry_condition_key "
        "from main_marts.bridge_registry_trial_condition c "
        "inner join main_marts.dim_registry_trial d "
        "on d.nct_id = c.nct_id "
        "and coalesce(d.active_in_latest_snapshot_flag, true) "
        "inner join main_marts.bridge_preset_trial b "
        "on b.nct_id = d.nct_id and b.current_record_flag "
        "where b.preset_id = ?)",
        [preset.preset_id, preset.preset_id],
    )


def county_access(preset: Preset) -> pd.DataFrame:
    """Fetch county-level access to open trial sites for one preset.

    Args:
        preset: Preset whose profile owns the rows. ``mart_trial_access_by_geo`` is keyed
            on MeSH node (``mesh_id``), not ``registry_condition_key``, so like the other
            MeSH-grain marts it narrows to the owning profile only (re-key: #149).

    Returns:
        pd.DataFrame: Rows of ``mart_trial_access_by_geo`` (profile x MeSH condition x
            county); empty when the mart is not built yet.
    """
    if not _table_exists("mart_registry_trial_access_by_geo"):
        return pd.DataFrame()
    return query(
        "select ? as indication_profile_id, * "
        "from main_marts.mart_registry_trial_access_by_geo "
        "where registry_condition_key in ("
        "select distinct c.registry_condition_key "
        "from main_marts.bridge_registry_trial_condition c "
        "inner join main_marts.dim_registry_trial d "
        "on d.nct_id = c.nct_id "
        "and coalesce(d.active_in_latest_snapshot_flag, true) "
        "inner join main_marts.bridge_preset_trial b "
        "on b.nct_id = d.nct_id and b.current_record_flag "
        "where b.preset_id = ?)",
        [preset.preset_id, preset.preset_id],
    )


def county_access_data_date(preset: Preset) -> pd.Timestamp | None:
    """Latest captured snapshot date of one preset's profile (the county mart's data date).

    Args:
        preset: Preset whose profile owns the snapshots.

    Returns:
        pd.Timestamp | None: The date, or ``None`` when the profile has no snapshot.
    """
    row = query(
        "select max(snapshot_date) as latest from main_marts.bridge_preset_trial "
        "where preset_id = ? and current_record_flag",
        [preset.preset_id],
    )
    latest = row["latest"].iloc[0] if not row.empty else None
    return None if pd.isna(latest) else pd.Timestamp(latest)


def participation_prevalence(preset: Preset) -> pd.DataFrame:
    """Fetch participation-to-prevalence rows for one preset.

    Args:
        preset: Preset whose profile owns the rows (profile-grain mart).

    Returns:
        pd.DataFrame: Rows of ``mart_participation_prevalence_ratio`` (profile x MeSH
            condition x completion window x dimension x group); empty when the mart
            is not built yet.
    """
    if not _table_exists("mart_registry_participation_prevalence_ratio"):
        return pd.DataFrame()
    return query(
        "select ? as indication_profile_id, * "
        "from main_marts.mart_registry_participation_prevalence_ratio "
        "where registry_condition_key in ("
        "select distinct c.registry_condition_key "
        "from main_marts.bridge_registry_trial_condition c "
        "inner join main_marts.dim_registry_trial d "
        "on d.nct_id = c.nct_id "
        "and coalesce(d.active_in_latest_snapshot_flag, true) "
        "inner join main_marts.bridge_preset_trial b "
        "on b.nct_id = d.nct_id and b.current_record_flag "
        "where b.preset_id = ?)",
        [preset.preset_id, preset.preset_id],
    )


def site_overlap(preset: Preset) -> pd.DataFrame:
    """Fetch trial site overlap metrics for one preset's latest captured date.

    Queries ``main_marts.mart_site_overlap`` for facility co-location and trial
    congestion, ordered by recruiting trial count and listed trial count descending.

    Args:
        preset: Preset scoping the overlap (profile-grain mart, latest date by trial key).

    Returns:
        pd.DataFrame: Facility rows at the preset's latest ``fct_trial_snapshot`` date;
            empty when that capture has no attributable facilities.
    """
    return query(
        "select *, preset_id as indication_profile_id "
        "from main_marts.mart_registry_site_overlap"
        " where preset_id = ?"
        "   and snapshot_date = ("
        "     select max(f.snapshot_date)"
        "     from main_marts.fct_registry_trial_snapshot f"
        "     inner join main_marts.bridge_preset_trial b"
        "       on b.nct_id = f.nct_id and b.snapshot_id = f.snapshot_id"
        "     where b.preset_id = ? and b.current_record_flag)"
        " order by recruiting_trial_count desc, listed_trial_count desc",
        [preset.preset_id, preset.preset_id],
    )


def sponsor_landscape(preset: Preset) -> pd.DataFrame:
    """Aggregate active recruiting trial counts and phase mix by lead sponsor.

    Queries ``main_marts.dim_trial`` and ``main_marts.bridge_trial_sponsor`` for
    recruiting trials, returning sponsor organization classification and phase breakdown.

    Args:
        preset: Preset whose trials are aggregated.

    Returns:
        pd.DataFrame: Summary DataFrame with columns ``indication_profile_id``,
            ``lead_sponsor``, ``sponsor_class``, ``recruiting_trial_count``, and
            ``phase_mix``.
    """
    return query(
        """
        select
            ? as indication_profile_id,
            d.current_lead_sponsor as lead_sponsor,
            coalesce(s.sponsor_class, 'UNKNOWN') as sponsor_class,
            count(distinct d.nct_id) as recruiting_trial_count,
            string_agg(distinct d.current_phase, ' | ' order by d.current_phase) as phase_mix
        from main_marts.dim_registry_trial d
        inner join main_marts.bridge_preset_trial b
            on b.nct_id = d.nct_id and b.current_record_flag
        left join (
            select snapshot_id, nct_id, min(sponsor_class) as sponsor_class
            from main_staging.stg_registry_trial_sponsors
            where sponsor_role = 'lead_sponsor'
            group by 1, 2
        ) s on s.nct_id = d.nct_id and s.snapshot_id = d.snapshot_id
        where d.current_overall_status = 'RECRUITING'
            and b.preset_id = ?
        group by 1, 2, 3
        order by recruiting_trial_count desc, lead_sponsor
        """,
        [preset.preset_id, preset.preset_id],
    )


def trial_explorer(preset: Preset) -> pd.DataFrame:
    """Fetch denormalized trial registry records and active site states for browsing.

    Combines ``dim_trial`` with active US site states from ``fct_trial_site``
    for that profile's latest snapshot date.

    Args:
        preset: Preset whose trials are listed.

    Returns:
        pd.DataFrame: Trial records containing NCT ID, indication profile ID,
            brief title, overall status, phase, lead sponsor, post date, enrollment,
            and comma-delimited US state locations.
    """
    return query(
        """
        select
            d.nct_id,
            b.preset_id as indication_profile_id,
            d.registry_url,
            d.current_brief_title as brief_title,
            d.current_overall_status as overall_status,
            d.current_phase as phase,
            d.current_lead_sponsor as lead_sponsor,
            d.study_first_post_date,
            d.enrollment_count,
            string_agg(distinct s.state_normalized, ', ' order by s.state_normalized)
                as us_states
        from main_marts.dim_registry_trial d
        inner join main_marts.bridge_preset_trial b
            on b.nct_id = d.nct_id and b.current_record_flag
        left join main_marts.fct_registry_trial_site s
            on d.nct_id = s.nct_id
            and s.snapshot_date = b.snapshot_date
            and regexp_matches(s.state_normalized, '^[A-Z]{2}$')
        where b.preset_id = ?
        group by all
        order by d.study_first_post_date desc nulls last, d.nct_id
        """,
        [preset.preset_id],
    )


def data_reliability(preset: Preset) -> pd.DataFrame:
    """Fetch ingestion and data pipeline reliability metrics for one preset.

    Args:
        preset: Preset whose profile owns the runs (profile-grain mart).

    Returns:
        pd.DataFrame: Reliability metrics from ``main_marts.mart_data_reliability``
            ordered by snapshot date and ingestion run ID descending.
    """
    return query(
        "select * exclude (condition), ? as condition, "
        "preset_id as indication_profile_id "
        "from main_marts.mart_registry_data_reliability"
        " where preset_id = ?"
        " order by snapshot_date desc, ingestion_run_id desc",
        [
            preset.condition_label or (preset.mesh_terms[0] if preset.mesh_terms else ""),
            preset.preset_id,
        ],
    )


def overview_metrics(preset: Preset) -> dict[str, Any]:
    """Compute high-level summary KPIs for one preset.

    Args:
        preset: Preset every subquery is scoped to.

    Returns:
        dict: Mapping of metric keys to counts/dates:
            - ``total_trials``: One latest-observed row per trial across retained captures.
            - ``recruiting_trials``: Latest-observed trial status exactly 'RECRUITING'.
            - ``states_with_sites``: Distinct profile/state rows across retained locations.
            - ``listed_facilities``: Best-effort normalized name/city/state identities in
              ``mart_site_overlap`` at the profile's latest ``fct_trial_snapshot`` date;
              zero when that capture has no attributable facilities.
            - ``latest_snapshot``: Date of this profile's most recent captured snapshot.
            - ``snapshot_count``: Number of distinct captured dates for this profile.
    """
    row = query(
        """
        with scoped as (
            select d.current_overall_status
            from main_marts.dim_registry_trial d
            inner join main_marts.bridge_preset_trial b on b.nct_id = d.nct_id
            where b.preset_id = ? and b.current_record_flag
        )
        select
            (select count(*) from scoped) as total_trials,
            (select count(*) from scoped
             where current_overall_status = 'RECRUITING') as recruiting_trials,
            (select count(distinct s.state_normalized)
             from main_marts.fct_registry_trial_site s
             inner join main_marts.bridge_preset_trial b
                on b.nct_id = s.nct_id and b.snapshot_id = s.snapshot_id
             where b.preset_id = ? and b.current_record_flag) as states_with_sites,
            (select count(*) from main_marts.mart_registry_site_overlap
             where preset_id = ?
               and snapshot_date = (
                 select max(f.snapshot_date)
                 from main_marts.fct_registry_trial_snapshot f
                 inner join main_marts.bridge_preset_trial b
                   on b.nct_id = f.nct_id and b.snapshot_id = f.snapshot_id
                 where b.preset_id = ? and b.current_record_flag))
                 as listed_facilities,
            (select max(snapshot_date) from main_marts.bridge_preset_trial
             where preset_id = ? and current_record_flag) as latest_snapshot,
            (select count(distinct snapshot_date) from main_marts.bridge_preset_trial
             where preset_id = ?) as snapshot_count,
            (select count(*) from main_marts.dim_registry_trial) as registry_total_trials
        """,
        [preset.preset_id, *[preset.preset_id] * 5],
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


def audit_studies(preset: Preset, snapshot_ids: list[str]) -> pd.DataFrame:
    """Read captured studies for explicit run IDs; an empty selection means no rows."""
    if not _table_exists("mart_registry_study_snapshot_audit"):
        return _audit_unavailable()
    return query(
        "select ? as preset_id, a.* from main_marts.mart_registry_study_snapshot_audit a "
        "where a.snapshot_id in (select unnest(?)) and exists ("
        "select 1 from main_marts.bridge_preset_trial b "
        "where b.preset_id = ? and b.snapshot_id = a.snapshot_id and b.nct_id = a.nct_id) "
        "order by a.snapshot_date, a.snapshot_id, a.nct_id",
        [preset.preset_id, snapshot_ids, preset.preset_id],
    )


def audit_locations(preset: Preset, snapshot_ids: list[str]) -> pd.DataFrame:
    """Read original recorded-location rows, retaining their ordinal and provenance."""
    if not _table_exists("mart_registry_location_snapshot_audit"):
        return _audit_unavailable()
    return query(
        "select ? as preset_id, a.* from main_marts.mart_registry_location_snapshot_audit a "
        "where a.snapshot_id in (select unnest(?)) and exists ("
        "select 1 from main_marts.bridge_preset_trial b "
        "where b.preset_id = ? and b.snapshot_id = a.snapshot_id and b.nct_id = a.nct_id) "
        "order by a.snapshot_id, a.nct_id, a.location_ordinal",
        [preset.preset_id, snapshot_ids, preset.preset_id],
    )


def audit_condition_mapping(preset: Preset) -> pd.DataFrame:
    """Load mappings before geographic exclusions, keyed by captured run and study."""
    if not _table_exists("int_registry_preset_condition_mapping", "main_intermediate"):
        return _audit_unavailable()
    return query(
        "select preset_id, snapshot_id, nct_id, condition_group, mapping_source "
        "from main_intermediate.int_registry_preset_condition_mapping "
        "where preset_id = ?",
        [preset.preset_id],
    )


def metric_audit_observations(preset: Preset, metric: str = "competition") -> pd.DataFrame:
    """Return actual per-study observations before status/geographic exclusions.

    Competition uses its mart's latest date; facility uses the profile's latest
    fct_trial_snapshot date, even without facility rows. History keeps daily
    observations, and latest_study follows the latest-observed per-study status dimension.
    Callers may further select dates/months, without inventing a newest run.
    """
    if not _table_exists("mart_registry_study_snapshot_audit"):
        return _audit_unavailable()
    if metric in {
        "latest_study",
        "manifest_reconciled",
        "usable_location_share",
        "low_confidence_condition_share",
        "coordinates_exact_share",
        "coordinates_fallback_eligible_share",
    }:
        return query(
            "select b.preset_id, f.snapshot_id, f.nct_id, f.snapshot_date "
            "from main_marts.fct_registry_trial_snapshot f "
            "inner join main_marts.bridge_preset_trial b "
            "on b.nct_id = f.nct_id and b.snapshot_id = f.snapshot_id "
            "where b.preset_id = ? and b.current_record_flag",
            [preset.preset_id],
        )
    if metric in {"history", "recent_recruiting"}:
        return query(
            "select b.preset_id, f.snapshot_id, f.nct_id, f.snapshot_date, f.previous_status, "
            "lag(f.snapshot_id) over trial_window as predecessor_snapshot_id, "
            "lag(f.snapshot_date) over trial_window as predecessor_snapshot_date "
            "from main_marts.fct_registry_trial_snapshot f "
            "inner join main_marts.bridge_preset_trial b "
            "on b.nct_id = f.nct_id and b.snapshot_id = f.snapshot_id "
            "where b.preset_id = ? "
            "window trial_window as (partition by b.preset_id, f.nct_id "
            "order by f.snapshot_timestamp_utc)",
            [preset.preset_id],
        )
    if metric in {"competition", "first_post_registered"}:
        return query(
            "select b.preset_id, f.snapshot_id, f.nct_id, f.snapshot_date "
            "from main_marts.fct_registry_trial_snapshot f "
            "inner join main_marts.bridge_preset_trial b "
            "on b.nct_id = f.nct_id and b.snapshot_id = f.snapshot_id "
            "where b.preset_id = ? and f.snapshot_date = ("
            "select max(snapshot_date) from main_marts.mart_registry_recruiting_competition "
            "where preset_id = ? and condition_group in (select unnest(?)))",
            [preset.preset_id, preset.preset_id, _groups(preset)],
        )
    if metric == "facility":
        return query(
            "select b.preset_id, f.snapshot_id, f.nct_id, f.snapshot_date "
            "from main_marts.fct_registry_trial_snapshot f "
            "inner join main_marts.bridge_preset_trial b "
            "on b.nct_id = f.nct_id and b.snapshot_id = f.snapshot_id "
            "where b.preset_id = ? and b.current_record_flag "
            "and f.snapshot_date = ("
            "select max(f2.snapshot_date) "
            "from main_marts.fct_registry_trial_snapshot f2 "
            "inner join main_marts.bridge_preset_trial b2 "
            "on b2.nct_id = f2.nct_id and b2.snapshot_id = f2.snapshot_id "
            "where b2.preset_id = ? and b2.current_record_flag)",
            [preset.preset_id, preset.preset_id],
        )
    raise ValueError(f"Unknown audit metric: {metric}")


def early_stop_transparency(preset: Preset) -> pd.DataFrame:
    """Fetch terminated, withdrawn, and suspended trial counts and verbatim reasons.

    Args:
        preset: Preset whose profile owns the stopped trials (profile-grain mart).

    Returns:
        pd.DataFrame: Records from ``main_marts.mart_early_stop_transparency``
            containing condition group, sponsor class, stopped year, status,
            verbatim why_stopped reason, stopped trial count, and contributing NCT IDs.
    """
    if not _table_exists("mart_registry_early_stop_transparency"):
        return pd.DataFrame()
    return query(
        "select *, preset_id as indication_profile_id "
        "from main_marts.mart_registry_early_stop_transparency "
        "where preset_id = ? "
        "order by stopped_year desc nulls last, stopped_trial_count desc, condition_group",
        [preset.preset_id],
    )


def results_posting_transparency(preset: Preset) -> pd.DataFrame:
    """Fetch results posting transparency rates and confidence intervals by sponsor.

    Args:
        preset: Preset whose profile owns the metrics (profile-grain mart).

    Returns:
        pd.DataFrame: Records from ``main_marts.mart_results_posting_transparency``
            containing sponsor class, lead sponsor, evaluable trial counts,
            results posted counts, overdue counts, posting rates, Wilson score CIs,
            and ranking flags.
    """
    if not _table_exists("mart_registry_results_posting_transparency"):
        return pd.DataFrame()
    return query(
        "select *, preset_id as indication_profile_id "
        "from main_marts.mart_registry_results_posting_transparency "
        "where preset_id = ? "
        "order by total_evaluable_trials desc, results_posted_rate desc nulls last, lead_sponsor",
        [preset.preset_id],
    )
