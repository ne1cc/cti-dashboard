"""Local refresh metadata and disk guard; no server or external alert transport."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

import duckdb
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from src.ingest.snapshot_manifest import load_manifests
from src.profiles import get_registry, load_shared_paths
from src.quality.reconciliation import reconcile_profile
from src.utils.dates import utc_now

Count = Annotated[int, Field(strict=True, ge=0)]
TABLES = ("dim_trial", "fct_trial_snapshot", "mart_trial_similarity")


class Capture(BaseModel):
    model_config = ConfigDict(strict=True)

    run_id: str = Field(min_length=1)
    captured_at_utc: AwareDatetime
    row_counts: dict[str, Count]


class Reconciliation(BaseModel):
    model_config = ConfigDict(strict=True)

    checks_total: int = Field(gt=0)
    checks_failed: Count


class Success(BaseModel):
    model_config = ConfigDict(strict=True)

    pipeline_completed_at_utc: AwareDatetime
    source_api_version: Literal["v2"]
    source_release_at_utc: None
    profiles: dict[str, Capture]
    reconciliation: Reconciliation


class Marker(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    attempt_at_utc: AwareDatetime
    attempt_status: Literal["running", "success", "failed"]
    error: str | None = None
    last_success: Success | None = None


def marker_path() -> Path:
    return load_shared_paths().duckdb.parent.parent / ".freshness.json"


def _read_marker() -> Marker:
    return Marker.model_validate_json(marker_path().read_text(encoding="utf-8"))


def _write_marker(payload: dict[str, Any]) -> None:
    """Publish a complete marker atomically; a failed replace preserves the old file."""
    path = marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, suffix=".tmp", delete=False
        ) as f:
            temporary = Path(f.name)
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _record_attempt(status: Literal["running", "failed"], error: str | None = None) -> None:
    try:
        raw = marker_path().read_text(encoding="utf-8")
        Marker.model_validate_json(raw)
        previous = json.loads(raw).get("last_success")
    except (OSError, ValueError):
        previous = None
    _write_marker(
        {
            "schema_version": 1,
            "attempt_at_utc": utc_now().isoformat(),
            "attempt_status": status,
            "error": error,
            "last_success": previous,
        }
    )


def _verify_success(marker: Marker) -> Success:
    """Reject incomplete verification independently of capture age."""
    if marker.attempt_status == "success" and marker.error is not None:
        raise ValueError("Successful attempt contains an error")
    success = marker.last_success
    expected = {profile.profile_id for profile in get_registry().refreshable()}
    if not expected or success is None or set(success.profiles) != expected:
        raise ValueError("Missing verification for the configured refreshable profiles")
    if success.reconciliation.checks_failed or success.reconciliation.checks_total < len(expected):
        raise ValueError("Reconciliation did not pass for every required profile")
    now = utc_now()
    if not success.pipeline_completed_at_utc <= marker.attempt_at_utc <= now:
        raise ValueError("Invalid or future pipeline/attempt timestamp")
    for pid, capture in success.profiles.items():
        if capture.captured_at_utc > success.pipeline_completed_at_utc:
            raise ValueError(f"Invalid or future capture timestamp for {pid}")
        if (
            set(capture.row_counts) != set(TABLES)
            or capture.row_counts["dim_trial"] == 0
            or capture.row_counts["fct_trial_snapshot"] == 0
        ):
            raise ValueError(f"Missing warehouse counts for {pid}")
    return success


def status(profile_id: str | None = None) -> dict[str, Any]:
    """Return JSON-ready freshness; unknown, failed and stale states have ok=false.

    Reads only the marker and registry. Wall-clock decisions are never cached.
    """
    result: dict[str, Any] = {"ok": False, "metadata": None, "errors": []}
    try:
        max_days = float(os.environ.get("CTI_FRESHNESS_MAX_AGE_DAYS", "10"))
        if not math.isfinite(max_days) or max_days <= 0:
            raise ValueError("CTI_FRESHNESS_MAX_AGE_DAYS must be positive and finite")
        marker = _read_marker()
        result["metadata"] = marker.model_dump(mode="json")
        success = _verify_success(marker)
        if marker.attempt_status != "success":
            result["errors"].append(f"Latest refresh attempt is {marker.attempt_status}")
        profiles = [profile_id] if profile_id is not None else list(success.profiles)
        for pid in profiles:
            capture = success.profiles[pid]
            age_days = (utc_now() - capture.captured_at_utc).total_seconds() / 86400
            if age_days > max_days:
                result["errors"].append(
                    f"{pid} capture is stale ({age_days:.2f} days > {max_days:g})"
                )
        result["ok"] = not result["errors"]
    except (OSError, ValueError, KeyError) as exc:
        result["errors"].append(f"Freshness unknown: {exc}")
    return result


def _collect_success() -> Success:
    """Reconcile every required profile and confirm its accepted run reached the warehouse."""
    profiles = get_registry().refreshable()
    if not profiles:
        raise ValueError("No refreshable profiles")
    captures = {}
    total = 0
    for profile in profiles:
        pid, cfg = profile.profile_id, profile.config
        manifests = load_manifests(cfg.paths.bronze_manifests)
        latest = max(manifests, key=lambda m: m.ingestion_run_id) if manifests else None
        if latest is None or latest.status != "success" or latest.ended_at_utc is None:
            raise ValueError(f"Missing successful capture for {pid}")
        if (
            latest.started_at_utc.tzinfo is None
            or latest.ended_at_utc.tzinfo is None
            or not latest.started_at_utc <= latest.ended_at_utc <= utc_now()
        ):
            raise ValueError(f"Invalid capture timestamps for {pid}")
        checks = reconcile_profile(pid, cfg)
        if not checks or any(not check.passed for check in checks):
            raise ValueError(f"Reconciliation failed for {pid}")
        total += len(checks)
        with duckdb.connect(str(cfg.paths.duckdb), read_only=True) as con:
            counts = {}
            for table in TABLES:
                row = con.execute(
                    f"select count(*) from main_marts.{table} where indication_profile_id = ?",
                    [pid],
                ).fetchone()
                assert row is not None
                counts[table] = int(row[0])
            accepted = con.execute(
                "select count(*) from main_marts.fct_trial_snapshot "
                "where indication_profile_id = ? and ingestion_run_id = ? and current_record_flag",
                [pid, latest.ingestion_run_id],
            ).fetchone()
            if not accepted or accepted[0] == 0:
                raise ValueError(f"Latest capture was not published for {pid}")
        captures[pid] = Capture(
            run_id=latest.ingestion_run_id,
            captured_at_utc=latest.started_at_utc,
            row_counts=counts,
        )
    return Success(
        pipeline_completed_at_utc=utc_now(),
        source_api_version="v2",
        source_release_at_utc=None,
        profiles=captures,
        reconciliation=Reconciliation(checks_total=total, checks_failed=0),
    )


def preflight() -> dict[str, Any]:
    floor = int(os.environ.get("CTI_REFRESH_MIN_FREE_BYTES", "500000000"))
    if floor <= 0:
        raise ValueError("CTI_REFRESH_MIN_FREE_BYTES must be a positive integer")
    path = load_shared_paths().duckdb.parent
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(path).free
    return {"ok": free >= floor, "free_bytes": free, "min_free_bytes": floor}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    for action in ("check", "preflight", "record-attempt", "record-success", "record-failure"):
        actions.add_argument(f"--{action}", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.preflight:
            result = preflight()
        elif args.record_attempt or args.record_failure:
            _record_attempt("running" if args.record_attempt else "failed")
            result = {"ok": True}
        elif args.record_success:
            try:
                success = _collect_success()
                marker = Marker(
                    schema_version=1,
                    attempt_at_utc=utc_now(),
                    attempt_status="success",
                    last_success=success,
                )
                _verify_success(marker)
            except Exception as exc:
                _record_attempt("failed", str(exc))
                raise
            _write_marker(marker.model_dump(mode="json"))
            result = {"ok": True}
        else:
            result = status()
            if args.check:
                result["disk"] = preflight()
                result["ok"] = result["ok"] and result["disk"]["ok"]
        print(json.dumps(result))
        return 0 if result["ok"] or not any(vars(args).values()) else 1
    except Exception as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}),
            file=sys.stdout if args.check else sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
