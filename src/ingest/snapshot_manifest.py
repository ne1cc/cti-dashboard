"""Ingestion-run manifests: run identity, parameters, counts, and outcome.

Manifests make every snapshot reproducible and let incremental mode skip
re-downloading a query that already completed recently (same query hash).
"""

import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from loguru import logger
from pydantic import BaseModel, ConfigDict

from src.utils.dates import utc_now, utc_now_compact
from src.utils.paths import ensure_dir


class IngestionManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ingestion_run_id: str
    query_hash: str
    endpoint: str
    condition: str | None = None
    params: dict[str, str]
    mode: str = "incremental"
    profile: str = "default"  # which ingestion profile produced this run (e.g. "full-catalog")
    status: str = "running"  # running | success | failed
    started_at_utc: datetime
    ended_at_utc: datetime | None = None
    page_count: int = 0
    record_count: int = 0
    total_count_reported: int | None = None
    quarantined_record_count: int = 0
    error: str | None = None
    # Set on incremental-refresh runs: the Essie RANGE lower bound (inclusive,
    # YYYY-MM-DD) applied to AREA[LastUpdatePostDate]. Presence marks the run's
    # bronze as *changed studies only*, which silver upserts onto the previous
    # full snapshot (build_silver_entities.py). None on full snapshots.
    updated_since_utc: str | None = None


def new_run_id() -> str:
    return f"{utc_now_compact()}_{uuid.uuid4().hex[:8]}"


def manifest_path(manifests_dir: Path, run_id: str) -> Path:
    return manifests_dir / f"manifest_{run_id}.json"


def write_manifest(manifests_dir: Path, manifest: IngestionManifest) -> Path:
    ensure_dir(manifests_dir)
    path = manifest_path(manifests_dir, manifest.ingestion_run_id)
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_manifests(manifests_dir: Path) -> list[IngestionManifest]:
    manifests: list[IngestionManifest] = []
    if not manifests_dir.exists():
        return manifests
    for path in sorted(manifests_dir.glob("manifest_*.json")):
        try:
            manifests.append(
                IngestionManifest.model_validate_json(path.read_text(encoding="utf-8"))
            )
        except Exception as exc:
            logger.warning("Skipping unreadable manifest {}: {}", path.name, exc)
    return manifests


def find_reusable_run(
    manifests_dir: Path, query_hash: str, reuse_window_hours: int
) -> IngestionManifest | None:
    """Most recent successful run with the same query hash inside the reuse window."""
    cutoff = utc_now() - timedelta(hours=reuse_window_hours)
    candidates = [
        m
        for m in load_manifests(manifests_dir)
        if m.status == "success" and m.query_hash == query_hash and m.started_at_utc >= cutoff
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda m: m.started_at_utc)


def write_summary(manifests_dir: Path, manifest: IngestionManifest) -> tuple[Path, Path]:
    """Flat one-row summary per run as Parquet and CSV for easy warehousing."""
    ensure_dir(manifests_dir)
    row = manifest.model_dump(mode="json")
    row["params"] = str(row["params"])
    frame = pd.DataFrame([row])
    parquet_path = manifests_dir / f"summary_{manifest.ingestion_run_id}.parquet"
    csv_path = manifests_dir / f"summary_{manifest.ingestion_run_id}.csv"
    frame.to_parquet(parquet_path, index=False)
    frame.to_csv(csv_path, index=False)
    return parquet_path, csv_path
