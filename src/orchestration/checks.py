"""Blocking asset checks that gate the publish path on data quality.

Two gates: `bronze_silver_reconciliation` blocks silver downstream (bad silver
never reaches dbt), and `warehouse_reconciliation` validates the warehouse dbt
built against the latest silver. Both aggregate every refreshable profile into
one result, and both fail if any profile fails.
"""

from typing import Any

from dagster import AssetCheckResult, MetadataValue, asset_check

from src.ingest.snapshot_manifest import load_manifests
from src.profiles import IndicationProfile, get_registry
from src.quality.reconciliation import (
    ReconciliationCheck,
    bronze_silver_checks,
    warehouse_checks,
)


def _refreshable_or_fail() -> list[IndicationProfile]:
    profiles = get_registry().refreshable()
    if not profiles:
        raise RuntimeError(
            "No refreshable profiles in config/profiles/*.yml — every check "
            "would pass vacuously, which is worse than failing."
        )
    return profiles


def _failed_payload(checks: list[ReconciliationCheck]) -> list[dict[str, str]]:
    return [
        {
            "profile": c.profile_id,
            "check": c.check,
            "run_id": str(c.run_id),
            "expected": str(c.expected),
            "actual": str(c.actual),
            "note": c.note,
        }
        for c in checks
        if not c.passed
    ]


def _aggregate(checks: list[ReconciliationCheck]) -> AssetCheckResult:
    failed = [c for c in checks if not c.passed]
    return AssetCheckResult(
        passed=not failed,
        metadata={
            "total_checks": len(checks),
            "failed_checks": MetadataValue.json(_failed_payload(checks)),
        },
    )


@asset_check(asset="ctg_raw_pages", name="manifest_integrity", blocking=True)
def manifest_integrity() -> AssetCheckResult:
    per_profile: dict[str, dict[str, Any]] = {}
    broken: list[str] = []
    for profile in _refreshable_or_fail():
        manifests = load_manifests(profile.config.paths.bronze_manifests)
        success_runs = [m for m in manifests if m.status == "success"]
        pid = profile.profile_id
        if not success_runs:
            per_profile[pid] = {"reason": "no_success_runs", "manifests_seen": len(manifests)}
            broken.append(pid)
            continue
        # ingestion_run_id ordering is UTC-compact (src/ingest/snapshot_manifest.py
        # new_run_id), so lexicographic max is the latest run.
        latest = max(success_runs, key=lambda m: m.ingestion_run_id)
        counts_agree = (
            latest.total_count_reported is None
            or latest.record_count == latest.total_count_reported
        )
        passed = latest.record_count > 0 and latest.page_count > 0 and counts_agree
        per_profile[pid] = {
            "ingestion_run_id": latest.ingestion_run_id,
            "record_count": latest.record_count,
            "total_count_reported": latest.total_count_reported,
            "page_count": latest.page_count,
            "passed": passed,
        }
        if not passed:
            broken.append(pid)
    return AssetCheckResult(
        passed=not broken,
        metadata={
            "profiles_checked": len(per_profile),
            "broken_profiles": MetadataValue.json(broken),
            "details": MetadataValue.json(per_profile),
        },
    )


@asset_check(asset="silver_entities", name="bronze_silver_reconciliation", blocking=True)
def bronze_silver_reconciliation() -> AssetCheckResult:
    """Pre-dbt gate: bad silver stops the build before dbt runs."""
    checks = [
        check
        for profile in _refreshable_or_fail()
        for check in bronze_silver_checks(profile.config, profile.profile_id)
    ]
    return _aggregate(checks)


@asset_check(asset="dim_trial", name="warehouse_reconciliation", blocking=True)
def warehouse_reconciliation() -> AssetCheckResult:
    """Post-build validation: the warehouse dbt built must cover each profile's
    latest silver run. Attached to dim_trial — an asset produced by the dbt
    multi-asset, so fct_trial_snapshot is populated by the time this runs."""
    checks = [
        check
        for profile in _refreshable_or_fail()
        for check in warehouse_checks(profile.config, profile.profile_id)
    ]
    return _aggregate(checks)
