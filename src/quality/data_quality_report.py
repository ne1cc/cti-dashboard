"""Assemble the human-readable data-quality report (Markdown).

Pulls run reliability from the warehouse, cross-layer reconciliation, and
the latest schema-drift result into `reports/data_quality_report.md`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import duckdb
from loguru import logger

from src.config import ProjectConfig, get_config
from src.ingest.snapshot_manifest import load_manifests
from src.profiles import get_registry
from src.quality.reconciliation import run_reconciliation
from src.quality.schema_drift import check_drift
from src.utils.dates import utc_now_iso

REPORT_PATH = Path("reports/data_quality_report.md")


@dataclass(frozen=True)
class Report:
    """What `build_report` produced: the artifact, and the counts behind it.

    A bare `Path` cannot carry the one number `make pipeline` needs. This report
    is the last step of the deployed refresh and nothing arms the Dagster asset
    checks, so if the reconciliation failures stay inside the Markdown the
    refresh that lost a whole profile exits 0 and the weekly marker advances.
    The file is the artifact; the code is the gate, and both read this number.
    """

    path: Path
    checks_total: int
    checks_failed: int


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _reliability_rows(cfg: ProjectConfig) -> list[dict[str, Any]]:
    if not cfg.paths.duckdb.exists():
        return []
    con = duckdb.connect(str(cfg.paths.duckdb), read_only=True)
    try:
        cursor = con.execute(
            """
            select indication_profile_id, ingestion_run_id, snapshot_date, status, page_count,
                   manifest_record_count, trial_row_count,
                   manifest_reconciled_flag, unique_nct_flag,
                   quarantined_record_count, flagged_record_share,
                   usable_location_share, low_confidence_condition_share
            from main_marts.mart_data_reliability
            order by snapshot_date desc, ingestion_run_id desc
            """
        )
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        con.close()


def build_report(cfg: ProjectConfig | None = None, output_path: Path | None = None) -> Report:
    cfg = cfg or get_config()
    output = output_path or REPORT_PATH
    output.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "# Data Quality Report",
        "",
        f"Generated: {utc_now_iso()} (UTC)",
        "",
        "Scope: public ClinicalTrials.gov registry snapshots taken by this",
        "project. Quality findings describe registry listings, not trial",
        "conduct or outcomes.",
        "",
        "## Ingestion run reliability",
        "",
    ]

    reliability = _reliability_rows(cfg)
    if reliability:
        headers = [
            "profile",
            "run",
            "snapshot date",
            "status",
            "pages",
            "manifest records",
            "silver rows",
            "reconciled",
            "unique NCT",
            "quarantined",
            "flagged share",
            "usable location share",
            "low-confidence cond. share",
        ]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + " --- |" * len(headers))
        for row in reliability:
            lines.append(
                "| "
                + " | ".join(
                    _fmt(row[key])
                    for key in (
                        "indication_profile_id",
                        "ingestion_run_id",
                        "snapshot_date",
                        "status",
                        "page_count",
                        "manifest_record_count",
                        "trial_row_count",
                        "manifest_reconciled_flag",
                        "unique_nct_flag",
                        "quarantined_record_count",
                        "flagged_record_share",
                        "usable_location_share",
                        "low_confidence_condition_share",
                    )
                )
                + " |"
            )
    else:
        lines.append("_Warehouse not built yet — run `make dbt-run` first._")

    lines += ["", "## Cross-layer reconciliation", ""]
    checks = run_reconciliation()
    lines.append("| check | profile | run | expected | actual | passed | note |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for check in checks:
        c = asdict(check)
        lines.append(
            f"| {c['check']} | {c['profile_id']} | {_fmt(c['run_id'])}"
            f" | {_fmt(c['expected'])} | {_fmt(c['actual'])} | {_fmt(c['passed'])} | {c['note']} |"
        )
    failed = sum(1 for c in checks if not c.passed)
    lines.append("")
    lines.append(f"**{len(checks) - failed}/{len(checks)} reconciliation checks passed.**")

    lines += ["", "## Schema drift", ""]
    for indication_profile in get_registry().refreshable():
        profile_cfg = indication_profile.config
        lines += [f"### {indication_profile.profile_id}", ""]
        success_runs = [
            m for m in load_manifests(profile_cfg.paths.bronze_manifests) if m.status == "success"
        ]
        if success_runs:
            latest = max(success_runs, key=lambda m: m.ingestion_run_id)
            drift = check_drift(latest.ingestion_run_id, cfg=profile_cfg)
            lines.append(f"- Run checked: `{drift['run_id']}`")
            lines.append(f"- Status: **{drift['status']}**")
            lines.append(f"- Observed field paths: {drift['observed_path_count']}")
            if drift.get("added_paths") or drift.get("removed_paths"):
                lines.append(f"- Added: {json.dumps(drift['added_paths'])}")
                lines.append(f"- Removed: {json.dumps(drift['removed_paths'])}")
        else:
            lines.append("_No complete ingestion runs to check._")
        lines.append("")

    lines += [
        "",
        "## Interpretation guardrails",
        "",
        "- Counts are registry listings, not patient availability.",
        "- Snapshot-transition metrics stay at zero until this project has",
        "  accrued multiple snapshots; registry-date proxies are labeled.",
        "- Facility identity is best-effort text matching; no site-capacity",
        "  or performance claims are made.",
        "",
    ]

    output.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Data quality report written to {}", output)
    return Report(path=output, checks_total=len(checks), checks_failed=failed)
