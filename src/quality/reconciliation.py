"""Cross-layer reconciliation: bronze manifests vs silver Parquet vs warehouse.

Every complete (success) run must carry its trial count through each layer.
Bronze→silver allows only the drop the transform recorded on file — repeated
or NCT-less records — and treats an unrecorded shortfall as a failure.
Failures are reported, never silently corrected.

The checks are split by layer dependency: `bronze_silver_checks` needs only
bronze manifests and silver Parquet (usable before dbt builds the warehouse),
while `warehouse_checks` needs the DuckDB warehouse dbt produces. The public
composer `run_reconciliation` returns both, for every refreshable profile in
the registry; every check carries the `profile_id` it is about.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import duckdb
from loguru import logger

from src.config import ProjectConfig
from src.ingest.snapshot_manifest import IngestionManifest, load_manifests
from src.profiles import get_registry
from src.transform.silver_stats import expected_trial_rows, load_transform_stats


@dataclass
class ReconciliationCheck:
    profile_id: str
    check: str
    run_id: str | None
    expected: Any
    actual: Any
    passed: bool
    note: str = ""


def _silver_trial_stats(cfg: ProjectConfig, run_id: str) -> tuple[int, int] | None:
    path = cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet"
    if not path.exists():
        return None
    row = duckdb.sql(
        "select count(*), count(distinct nct_id) from read_parquet(?)",
        params=[path.as_posix()],
    ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def _silver_trial_nct_ids(cfg: ProjectConfig, run_id: str) -> set[str] | None:
    """NCT IDs in one run's silver_trials file.

    No profile filter: a run belongs to exactly one profile, so the file is
    already that profile's. The run_id is the scope.
    """
    path = cfg.paths.silver / "silver_trials" / f"run_id={run_id}.parquet"
    if not path.exists():
        return None
    rows = duckdb.sql(
        "select distinct nct_id from read_parquet(?)",
        params=[path.as_posix()],
    ).fetchall()
    return {str(row[0]) for row in rows}


def _success_runs(cfg: ProjectConfig) -> list[IngestionManifest]:
    return [m for m in load_manifests(cfg.paths.bronze_manifests) if m.status == "success"]


def bronze_silver_checks(cfg: ProjectConfig, profile_id: str) -> list[ReconciliationCheck]:
    """Bronze manifests vs silver Parquet, for one profile's bronze and silver.
    No warehouse dependency — safe to run as the pre-dbt gate on silver_entities.

    The row expectation is the manifest count minus the exclusions the transform
    recorded for this run: it keeps the first occurrence of a repeated NCT ID and
    quarantines NCT-less records, so a shortfall against the raw manifest count is
    normal, and gating on the raw count blocks every legitimate dedup.

    This is not a weaker loss guard. The expectation can only account for drops the
    transform itself reported, so any loss beyond those still fails, and stats that
    are missing, unreadable, or carry a disagreeing manifest count fall back to the
    full record count. Every branch stays exact — `rows <= record_count` would pass
    a silently truncated table, which is the failure this gate exists to catch.
    """
    checks: list[ReconciliationCheck] = []

    success_runs = _success_runs(cfg)
    if not success_runs:
        checks.append(
            ReconciliationCheck(
                profile_id=profile_id,
                check="success_runs_exist",
                run_id=None,
                expected=">= 1",
                actual=0,
                passed=False,
                note="No complete ingestion runs found.",
            )
        )
        _log_failures("Bronze→silver", checks)
        return checks

    for manifest in success_runs:
        stats = _silver_trial_stats(cfg, manifest.ingestion_run_id)
        if stats is None:
            checks.append(
                ReconciliationCheck(
                    profile_id=profile_id,
                    check="silver_exists_for_success_run",
                    run_id=manifest.ingestion_run_id,
                    expected="silver_trials parquet present",
                    actual="missing",
                    passed=False,
                    note="Run `make transform`.",
                )
            )
            continue
        rows, distinct_ncts = stats
        expected = expected_trial_rows(
            load_transform_stats(cfg, manifest.ingestion_run_id), manifest.record_count
        )
        note = ""
        if expected is None:
            # No recorded exclusions, so expect every bronze record to survive.
            expected, note = (
                manifest.record_count,
                (
                    "No transform stats on file, so legitimate dedup cannot be "
                    "accounted for; rebuild with `make transform --force`."
                ),
            )
        checks.append(
            ReconciliationCheck(
                profile_id=profile_id,
                check="bronze_manifest_vs_silver_rows",
                run_id=manifest.ingestion_run_id,
                expected=expected,
                actual=rows,
                passed=rows == expected,
                note=note,
            )
        )
        checks.append(
            ReconciliationCheck(
                profile_id=profile_id,
                check="silver_nct_ids_unique",
                run_id=manifest.ingestion_run_id,
                expected=rows,
                actual=distinct_ncts,
                passed=distinct_ncts == rows,
            )
        )

    _log_failures("Bronze→silver", checks)
    return checks


def warehouse_checks(cfg: ProjectConfig, profile_id: str) -> list[ReconciliationCheck]:
    """DuckDB warehouse vs the latest silver run *for one profile*. Requires the
    warehouse dbt builds — run as post-build validation on dim_trial."""
    checks: list[ReconciliationCheck] = []

    warehouse = cfg.paths.duckdb
    if not warehouse.exists():
        checks.append(
            ReconciliationCheck(
                profile_id=profile_id,
                check="warehouse_exists",
                run_id=None,
                expected=str(warehouse),
                actual="missing",
                passed=False,
                note="Run `make dbt-run`.",
            )
        )
        _log_failures("Warehouse", checks)
        return checks

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        success_runs = _success_runs(cfg)
        latest = max(success_runs, key=lambda m: m.ingestion_run_id) if success_runs else None
        silver_ids = _silver_trial_nct_ids(cfg, latest.ingestion_run_id) if latest else None
        dim_rows = con.execute(
            "select nct_id from main_marts.dim_trial where indication_profile_id = ?",
            [profile_id],
        ).fetchall()
        flag_row = con.execute(
            "select count(*) from main_marts.fct_trial_snapshot "
            "where current_record_flag and indication_profile_id = ?",
            [profile_id],
        ).fetchone()
        assert flag_row is not None
        current_flags = flag_row[0]
        similarity_row = con.execute(
            "select count(*) from main_marts.mart_trial_similarity where indication_profile_id = ?",
            [profile_id],
        ).fetchone()
        assert similarity_row is not None
        similarity_rows = similarity_row[0]
    finally:
        con.close()

    dim_ids = {str(row[0]) for row in dim_rows}
    if silver_ids is None or latest is None:
        checks.append(
            ReconciliationCheck(
                profile_id=profile_id,
                check="warehouse_covers_latest_silver",
                run_id=latest.ingestion_run_id if latest else None,
                expected="a complete silver run for this profile",
                actual="none found",
                passed=False,
                note="Run `make transform` for this profile.",
            )
        )
    else:
        missing = sorted(silver_ids - dim_ids)
        checks.append(
            ReconciliationCheck(
                profile_id=profile_id,
                check="warehouse_covers_latest_silver",
                run_id=latest.ingestion_run_id,
                expected=len(silver_ids),
                actual=len(silver_ids) - len(missing),
                passed=not missing,
                note=(
                    f"Not in dim_trial: {', '.join(missing[:5])}{' …' if len(missing) > 5 else ''}"
                    if missing
                    else "dim_trial may hold trials from earlier snapshots; only a "
                    "trial missing from the latest run is data loss."
                ),
            )
        )
    checks.append(
        ReconciliationCheck(
            profile_id=profile_id,
            check="warehouse_one_current_record_per_trial",
            run_id=None,
            expected=len(dim_ids),
            actual=current_flags,
            passed=current_flags <= len(dim_ids),
            note="Current records cannot exceed known trials for this profile.",
        )
    )
    checks.append(
        ReconciliationCheck(
            profile_id=profile_id,
            check="warehouse_profile_has_trials",
            run_id=None,
            expected=">= 1",
            actual=len(dim_ids),
            passed=len(dim_ids) > 0,
            note=(
                "dim_trial holds no rows for this profile; every other warehouse "
                "leg is a set difference or an inequality that zero satisfies."
                if not dim_ids
                else ""
            ),
        )
    )
    if current_flags >= 2:
        checks.append(
            ReconciliationCheck(
                profile_id=profile_id,
                check="warehouse_profile_has_similar_trials",
                run_id=None,
                expected=">= 1",
                actual=similarity_rows,
                passed=similarity_rows > 0,
                note=(
                    f"{current_flags} current trials in dim_trial but "
                    "mart_trial_similarity is empty for this profile: the "
                    "Similarity tab serves nothing and no other check reads it."
                    if similarity_rows == 0
                    else ""
                ),
            )
        )
    _log_failures("Warehouse", checks)
    return checks


def _log_failures(layer: str, checks: list[ReconciliationCheck]) -> None:
    for check in (c for c in checks if not c.passed):
        logger.warning("{} reconciliation FAILED: {}", layer, asdict(check))


def reconcile_profile(profile_id: str, cfg: ProjectConfig) -> list[ReconciliationCheck]:
    """Both layers for exactly one indication profile."""
    return bronze_silver_checks(cfg, profile_id) + warehouse_checks(cfg, profile_id)


def run_reconciliation() -> list[ReconciliationCheck]:
    """All cross-layer checks, one profile at a time (public API).

    No cfg: the warehouse is shared but each profile's bronze tree is not, and
    a check that mixed them would compare two profiles' row counts and call the
    difference a failure.
    """
    checks = [
        check
        for indication_profile in get_registry().refreshable()
        for check in reconcile_profile(indication_profile.profile_id, indication_profile.config)
    ]
    failed = [c for c in checks if not c.passed]
    if failed:
        logger.warning(
            "Reconciliation: {} of {} checks FAILED across {} profile(s).",
            len(failed),
            len(checks),
            len(get_registry().refreshable()),
        )
    else:
        logger.info(
            "All {} reconciliation checks passed across {} profile(s).",
            len(checks),
            len(get_registry().refreshable()),
        )
    return checks
