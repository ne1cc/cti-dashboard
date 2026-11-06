"""Read-only DuckDB access for the dashboard (never writes to the warehouse).

Every reader takes a `Preset` first and always binds its owning profile
(`preset.profile_id`) as `indication_profile_id = ?`, so a preset can never read
another profile's rows or count a shared trial twice. The preset's
`registry_condition_keys` narrow further as one `?` list parameter, in one of three
shapes depending on what the table carries:

- condition-grain marts (a `registry_condition_key` column): profile and key;
- trial-grain tables (`trial_key`): the profile's trials under those keys in
  `bridge_trial_condition`, plus its trials with no bridge row (no mapped
  condition), which the #146 baseline counted;
- profile-grain marts (neither column): the owning profile only. These cannot be
  narrowed below the profile until the marts re-key (#149).

An unscoped query does not fail — it blends indications into one plausible
number, which is what this file exists to make impossible.
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


def _keys(preset: Preset) -> list[str]:
    return list(preset.registry_condition_keys)


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
        _freshness_banner(preset.preset_id)


def _freshness_banner(profile_id: str) -> None:
    freshness = freshness_status(profile_id)
    if not freshness["ok"]:
        st.warning("Data freshness warning: " + "; ".join(freshness["errors"]))
    metadata = freshness["metadata"]
    success = metadata.get("last_success") if metadata else None
    if success and profile_id in success["profiles"]:
        capture = success["profiles"][profile_id]
        st.caption(
            f"Last verified pipeline completion (UTC): {success['pipeline_completed_at_utc']} · "
            f"{profile_id} capture (UTC): {capture['captured_at_utc']}"
        )


@st.cache_data(ttl=600)
def _nodes_missing(preset: Preset) -> bool:
    """True when the preset's profile is in the warehouse but none of its nodes are.

    That is a taxonomy edit or typo, not "not yet refreshed". A profile with no
    `dim_condition` rows at all is the refresh case and returns False.
    """
    if not _has_column("dim_condition", "registry_condition_key"):
        return False
    row = (
        _connection()
        .execute(
            "select count(*) filter (where registry_condition_key in (select unnest(?))), "
            "count(*) from main_marts.dim_condition where indication_profile_id = ?",
            [_keys(preset), preset.profile_id],
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
    if not _has_column("bridge_trial_condition", "registry_condition_key"):
        return 0
    row = (
        _connection()
        .execute(
            "select count(*) from main_marts.dim_trial where indication_profile_id = ? "
            "and (trial_key in (select trial_key from main_marts.bridge_trial_condition "
            "where registry_condition_key in (select unnest(?))) "
            "or trial_key not in (select trial_key from main_marts.bridge_trial_condition))",
            [preset.profile_id, _keys(preset)],
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
    if not _has_column("mart_feasibility_priority_queue", "operational_completeness_share"):
        st.error(
            "Priority Queue is unavailable because the warehouse has an outdated "
            "mart. Rebuild it with `make pipeline`."
        )
        st.stop()
    return query(
        "select * from main_marts.mart_feasibility_priority_queue"
        " where indication_profile_id = ? and registry_condition_key in (select unnest(?))"
        " order by active_recruiting_trials desc, distinct_sponsors desc, priority_queue_key asc",
        [preset.profile_id, _keys(preset)],
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
    mesh_clause = (
        "and c.mesh_id is null" if _has_column("bridge_trial_condition", "mesh_id") else ""
    )
    sql = f"""
        select distinct
            d.nct_id,
            d.current_brief_title as brief_title,
            d.current_overall_status as overall_status,
            d.current_phase as phase,
            d.current_lead_sponsor as lead_sponsor,
            d.enrollment_count,
            d.study_first_post_date,
            d.registry_url
        from main_marts.dim_trial d
        inner join main_marts.bridge_trial_condition c on c.trial_key = d.trial_key
        inner join main_marts.fct_trial_site s on s.trial_key = d.trial_key
        where d.indication_profile_id = ?
          and c.registry_condition_key in (select unnest(?))
          and s.snapshot_date = (
              select max(snapshot_date) from main_marts.fct_trial_site
              where indication_profile_id = ?)
          and c.condition_group = ?
          {mesh_clause}
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
                preset.profile_id,
                _keys(preset),
                preset.profile_id,
                condition_group,
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
    if not _table_exists("mart_trial_similarity"):
        return pd.DataFrame()
    required_columns = ("indication_profile_id", "nct_id_a", "nct_id_b", "similarity_rank")
    if not all(_has_column("mart_trial_similarity", column) for column in required_columns):
        st.error(
            "Trial Similarity is unavailable because the warehouse has an outdated "
            "similarity mart. Rebuild it with `make pipeline`."
        )
        return pd.DataFrame()
    return query(
        "select * from main_marts.mart_trial_similarity"
        " where indication_profile_id = ?"
        " and nct_id_a = ? order by similarity_rank",
        [preset.profile_id, nct_id],
    )


def recruiting_competition(preset: Preset) -> pd.DataFrame:
    """Fetch the latest recruiting competition metrics for one preset.

    Args:
        preset: Preset whose condition keys scope the metrics.

    Returns:
        pd.DataFrame: Active recruiting competition indicators by geographic and
            indication segment, at that preset's own latest snapshot date.
    """
    # "latest snapshot" is per preset: one may have refreshed and another not, and
    # a shared max() would show the stale preset's newest date as if it had data.
    return query(
        "select * from main_marts.mart_recruiting_competition"
        " where indication_profile_id = ? and registry_condition_key in (select unnest(?))"
        "   and snapshot_date = ("
        "     select max(snapshot_date) from main_marts.mart_recruiting_competition"
        "     where indication_profile_id = ? and registry_condition_key in (select unnest(?)))",
        [preset.profile_id, _keys(preset), preset.profile_id, _keys(preset)],
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
        "select * from main_marts.mart_condition_geography_trends"
        " where indication_profile_id = ? and registry_condition_key in (select unnest(?))"
        " order by activity_month",
        [preset.profile_id, _keys(preset)],
    )


def access_rates(preset: Preset) -> pd.DataFrame:
    """Fetch population- and burden-adjusted recruiting access for one preset.

    Args:
        preset: Preset whose profile owns the rates (profile-grain mart).

    Returns:
        pd.DataFrame: Rows of ``mart_condition_access_rates`` (profile x MeSH
            condition x state); empty when the mart is not built yet.
    """
    if not _table_exists("mart_condition_access_rates"):
        return pd.DataFrame()
    return query(
        "select * from main_marts.mart_condition_access_rates where indication_profile_id = ?",
        [preset.profile_id],
    )


def participation_prevalence(preset: Preset) -> pd.DataFrame:
    """Fetch participation-to-prevalence rows for one preset.

    Args:
        preset: Preset whose profile owns the rows (profile-grain mart).

    Returns:
        pd.DataFrame: Rows of ``mart_participation_prevalence_ratio`` (profile x MeSH
            condition x completion window x dimension x group); empty when the mart
            is not built yet.
    """
    if not _table_exists("mart_participation_prevalence_ratio"):
        return pd.DataFrame()
    return query(
        "select * from main_marts.mart_participation_prevalence_ratio"
        " where indication_profile_id = ?",
        [preset.profile_id],
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
        "select * from main_marts.mart_site_overlap"
        " where indication_profile_id = ?"
        "   and snapshot_date = ("
        "     select max(snapshot_date) from main_marts.fct_trial_snapshot"
        "     where indication_profile_id = ?)"
        " order by recruiting_trial_count desc, listed_trial_count desc",
        [preset.profile_id, preset.profile_id],
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
    # No profile predicate on the join: bridge_trial_sponsor.trial_key is
    # md5(nct_id, indication_profile_id) (Task 9), so it cannot match across
    # profiles. The column is selected so the frame says which profile owns the rows.
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
            and (d.trial_key in (
                    select trial_key from main_marts.bridge_trial_condition
                    where registry_condition_key in (select unnest(?)))
                or d.trial_key not in (
                    select trial_key from main_marts.bridge_trial_condition))
        group by 1, 2
        order by recruiting_trial_count desc, lead_sponsor
        """,
        [preset.profile_id, _keys(preset)],
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
    # Joins on trial_key, not nct_id: an nct_id-only join would attach the
    # other profile's sites to a shared trial and show them as its own.
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
            and (d.trial_key in (
                    select trial_key from main_marts.bridge_trial_condition
                    where registry_condition_key in (select unnest(?)))
                or d.trial_key not in (
                    select trial_key from main_marts.bridge_trial_condition))
        group by all
        order by d.study_first_post_date desc nulls last, d.nct_id
        """,
        [preset.profile_id, preset.profile_id, _keys(preset)],
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
        "select * from main_marts.mart_data_reliability"
        " where indication_profile_id = ?"
        " order by snapshot_date desc, ingestion_run_id desc",
        [preset.profile_id],
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
    # Seven `?` below: the trial scope binds the profile and the keys, then five
    # profile binds. Trials with no mapped condition have no bridge row; the
    # baseline counted them, so the scope keeps them in the owning profile.
    row = query(
        """
        with scoped as (
            select current_overall_status from main_marts.dim_trial
            where indication_profile_id = ?
              and (trial_key in (select trial_key from main_marts.bridge_trial_condition
                                 where registry_condition_key in (select unnest(?)))
                   or trial_key not in (select trial_key from main_marts.bridge_trial_condition))
        )
        select
            (select count(*) from scoped) as total_trials,
            (select count(*) from scoped
             where current_overall_status = 'RECRUITING') as recruiting_trials,
            (select count(*) from main_marts.dim_geography
             where indication_profile_id = ?) as states_with_sites,
            (select count(*) from main_marts.mart_site_overlap
             where indication_profile_id = ?
               and snapshot_date = (
                 select max(snapshot_date) from main_marts.fct_trial_snapshot
                 where indication_profile_id = ?)) as listed_facilities,
            (select max(snapshot_date) from main_marts.fct_trial_snapshot
             where indication_profile_id = ?) as latest_snapshot,
            (select count(distinct snapshot_date) from main_marts.fct_trial_snapshot
             where indication_profile_id = ?) as snapshot_count
        """,
        [preset.profile_id, _keys(preset), *[preset.profile_id] * 5],
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
    if not _table_exists("mart_study_snapshot_audit"):
        return _audit_unavailable()
    return query(
        "select * from main_marts.mart_study_snapshot_audit "
        "where indication_profile_id = ? "
        "and snapshot_id in (select unnest(?)) "
        "order by snapshot_date, snapshot_id, nct_id",
        [preset.profile_id, snapshot_ids],
    )


def audit_locations(preset: Preset, snapshot_ids: list[str]) -> pd.DataFrame:
    """Read original recorded-location rows, retaining their ordinal and provenance."""
    if not _table_exists("mart_location_snapshot_audit"):
        return _audit_unavailable()
    return query(
        "select * from main_marts.mart_location_snapshot_audit "
        "where indication_profile_id = ? "
        "and snapshot_id in (select unnest(?)) "
        "order by snapshot_id, nct_id, location_ordinal",
        [preset.profile_id, snapshot_ids],
    )


def audit_condition_mapping(preset: Preset) -> pd.DataFrame:
    """Load mappings before geographic exclusions, keyed by captured run and study."""
    if not _table_exists("int_trial_condition_mapping", "main_intermediate"):
        return _audit_unavailable()
    if _has_column("int_trial_condition_mapping", "mapping_source", "main_intermediate"):
        return query(
            "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, "
            "condition_group, mapping_source "
            "from main_intermediate.int_trial_condition_mapping "
            "where indication_profile_id = ?",
            [preset.profile_id],
        )
    return query(
        "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, condition_group "
        "from main_intermediate.int_trial_condition_mapping "
        "where indication_profile_id = ?",
        [preset.profile_id],
    )


def metric_audit_observations(preset: Preset, metric: str = "competition") -> pd.DataFrame:
    """Return actual per-study observations before status/geographic exclusions.

    Competition uses its mart's latest date; facility uses the profile's latest
    fct_trial_snapshot date, even without facility rows. History keeps daily
    observations, and latest_study follows the latest-observed per-study status dimension.
    Callers may further select dates/months, without inventing a newest run.
    """
    if not _table_exists("mart_study_snapshot_audit"):
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
            "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, snapshot_date "
            "from main_intermediate.int_current_trial_status "
            "where indication_profile_id = ?",
            [preset.profile_id],
        )
    if metric in {"history", "recent_recruiting"}:
        return query(
            "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, snapshot_date, "
            "previous_status, lag(ingestion_run_id) over trial_window as predecessor_snapshot_id, "
            "lag(snapshot_date) over trial_window as predecessor_snapshot_date "
            "from main_intermediate.int_trial_status_history "
            "where indication_profile_id = ? "
            "window trial_window as (partition by indication_profile_id, "
            "nct_id order by snapshot_date)",
            [preset.profile_id],
        )
    if metric in {"competition", "first_post_registered"}:
        return query(
            "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, snapshot_date "
            "from main_intermediate.int_trial_status_history "
            "where indication_profile_id = ? "
            "and snapshot_date = (select max(snapshot_date) from main_marts."
            "mart_recruiting_competition where indication_profile_id = ? "
            "and registry_condition_key in (select unnest(?)))",
            [preset.profile_id, preset.profile_id, _keys(preset)],
        )
    if metric == "facility":
        return query(
            "select indication_profile_id, ingestion_run_id as snapshot_id, nct_id, snapshot_date "
            "from main_intermediate.int_trial_status_history "
            "where indication_profile_id = ? "
            "and snapshot_date = (select max(snapshot_date) from main_marts."
            "fct_trial_snapshot where indication_profile_id = ?)",
            [preset.profile_id, preset.profile_id],
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
    if not _table_exists("mart_early_stop_transparency"):
        return pd.DataFrame()
    return query(
        "select * from main_marts.mart_early_stop_transparency "
        "where indication_profile_id = ? "
        "order by stopped_year desc nulls last, stopped_trial_count desc, condition_group",
        [preset.profile_id],
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
    if not _table_exists("mart_results_posting_transparency"):
        return pd.DataFrame()
    return query(
        "select * from main_marts.mart_results_posting_transparency "
        "where indication_profile_id = ? "
        "order by total_evaluable_trials desc, results_posted_rate desc nulls last, lead_sponsor",
        [preset.profile_id],
    )
