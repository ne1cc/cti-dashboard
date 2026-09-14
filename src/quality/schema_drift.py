"""Detect structural drift in bronze API responses against a stored baseline.

The registry can add, rename, or drop fields without notice. We record the
set of observed field paths (to a fixed depth) per run and compare against
`data/bronze/<profile_id>/_schema_baseline.json`, one baseline per profile.
Drift is reported, never auto-"fixed": a human decides whether to update the
baseline (--update-schema-baseline).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import ProjectConfig, get_config
from src.transform.flatten_studies import iter_bronze_studies
from src.utils.dates import utc_now_iso

MAX_DEPTH = 3
BASELINE_FILENAME = "_schema_baseline.json"


def collect_field_paths(node: Any, prefix: str = "", depth: int = 0) -> set[str]:
    """Field paths like 'protocolSection.statusModule.overallStatus'.

    Lists contribute their element structure under '<path>[]'.
    """
    paths: set[str] = set()
    if depth >= MAX_DEPTH:
        return paths
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            paths |= collect_field_paths(value, path, depth + 1)
    elif isinstance(node, list):
        for item in node[:20]:
            paths |= collect_field_paths(item, f"{prefix}[]", depth + 1)
    return paths


def observe_run_paths(run_id: str, cfg: ProjectConfig | None = None) -> set[str]:
    cfg = cfg or get_config()
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    if not run_dir.exists():
        raise FileNotFoundError(f"No bronze pages for run {run_id} at {run_dir}")
    observed: set[str] = set()
    for study in iter_bronze_studies(run_dir):
        observed |= collect_field_paths(study)
    return observed


def _baseline_path(cfg: ProjectConfig) -> Path:
    return cfg.paths.bronze_api_responses.parent / BASELINE_FILENAME


def check_drift(
    run_id: str,
    update_baseline: bool = False,
    cfg: ProjectConfig | None = None,
) -> dict[str, Any]:
    """Compare a run's observed paths to the baseline; write a drift report."""
    cfg = cfg or get_config()
    observed = observe_run_paths(run_id, cfg)
    baseline_path = _baseline_path(cfg)

    if not baseline_path.exists():
        baseline_path.write_text(
            json.dumps(
                {
                    "created_at_utc": utc_now_iso(),
                    "source_run_id": run_id,
                    "paths": sorted(observed),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("Schema baseline created from run {} ({} paths).", run_id, len(observed))
        return {
            "run_id": run_id,
            "status": "baseline_created",
            "observed_path_count": len(observed),
            "added_paths": [],
            "removed_paths": [],
        }

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_paths = set(baseline.get("paths", []))
    added = sorted(observed - baseline_paths)
    removed = sorted(baseline_paths - observed)
    status = "ok" if not added and not removed else "drift_detected"

    report = {
        "run_id": run_id,
        "checked_at_utc": utc_now_iso(),
        "status": status,
        "baseline_source_run_id": baseline.get("source_run_id"),
        "observed_path_count": len(observed),
        "baseline_path_count": len(baseline_paths),
        "added_paths": added,
        "removed_paths": removed,
    }
    report_path = baseline_path.parent / f"_schema_drift_{run_id}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if status == "drift_detected":
        logger.warning(
            "Schema drift in run {}: +{} / -{} paths (see {}).",
            run_id,
            len(added),
            len(removed),
            report_path,
        )
        if update_baseline:
            baseline_path.write_text(
                json.dumps(
                    {
                        "created_at_utc": utc_now_iso(),
                        "source_run_id": run_id,
                        "paths": sorted(observed),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            logger.info("Baseline updated to run {} by explicit request.", run_id)
            report["baseline_updated"] = True
    else:
        logger.info("No schema drift in run {} ({} paths).", run_id, len(observed))
    return report
