"""Per-run transform stats: what the silver transform legitimately excluded.

`build_silver_for_run` drops records without an NCT ID and keeps only the
first occurrence of a repeated one, so "silver rows == bronze records" is the
wrong expectation for any run that had either. Reconciliation needs that exact
figure to stay a real data-loss guard without failing on legitimate dedup, so
the transform records its exclusions here instead of every caller re-deriving
them from the Parquet.
"""

import json
from pathlib import Path
from typing import Any, cast

from src.config import ProjectConfig
from src.utils.paths import ensure_dir

STATS_DIRNAME = "_transform_stats"


def stats_path(cfg: ProjectConfig, run_id: str) -> Path:
    return cfg.paths.silver / STATS_DIRNAME / f"run_id={run_id}.json"


def write_transform_stats(
    cfg: ProjectConfig,
    run_id: str,
    record_count: int,
    row_counts: dict[str, int],
    duplicate_nct_ids: int,
    skipped_no_nct_id: int,
) -> Path:
    path = stats_path(cfg, run_id)
    ensure_dir(path.parent)
    payload = {
        "ingestion_run_id": run_id,
        "manifest_record_count": record_count,
        "duplicate_nct_ids_dropped": duplicate_nct_ids,
        "records_without_nct_id": skipped_no_nct_id,
        "row_counts": row_counts,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_transform_stats(cfg: ProjectConfig, run_id: str) -> dict[str, Any] | None:
    """None when absent or unreadable, so callers fall back to the strict
    expectation rather than assuming nothing was excluded."""
    path = stats_path(cfg, run_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cast(dict[str, Any], data) if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def expected_trial_rows(stats: dict[str, Any] | None, record_count: int) -> int | None:
    """Exact silver_trials row count implied by a manifest and the transform's
    recorded exclusions.

    None means "cannot state an expectation": no stats, or stats written for a
    different record count (a re-ingested run under a reused run_id), in which
    case callers must not treat a shortfall as explained.
    """
    if stats is None:
        return None
    if stats.get("manifest_record_count") != record_count:
        return None
    try:
        duplicates = int(stats.get("duplicate_nct_ids_dropped", 0))
        skipped = int(stats.get("records_without_nct_id", 0))
    except (TypeError, ValueError):
        return None
    return record_count - duplicates - skipped
