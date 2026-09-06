"""Prune old per-run bronze/silver artifacts so a fixed volume stays bounded.

Two independent horizons:

* bronze (raw API pages) — the most expensive bytes (measured 2026-09-05: 78.8 MB
  of bronze against 9.9 MB of silver for one run) and only needed to re-derive
  silver, so it keeps `bronze_runs_to_keep` runs.
* snapshots (silver parquet + manifest + summary) — these *are* the warehouse's
  history; dbt globs them, so pruning one deletes a snapshot_date from every
  trend and time-series mart. `snapshot_runs_to_keep` is therefore allowed to
  run deeper than `bronze_runs_to_keep`, and the depth it runs to is not capped:
  snapshots are the cheap bytes and the longitudinal record, raw pages are the
  expensive bytes and only exist to re-derive silver.

The manifests directory belongs to the *snapshot* horizon, not the bronze one:
`summary_*.parquet` is dbt's `ingestion_manifests` source, and `manifest_*.json`
is what `load_manifests` reads to decide anything at all. Emptying it to
`bronze_runs_to_keep` (1) would leave the next prune unable to see the history it
is supposed to bound, and silver would then grow one run per week forever.

What is enforced, in `prune_profile`, is `bronze_runs_to_keep <=
snapshot_runs_to_keep`. The reverse strands bytes: the runs between the two
horizons lose their manifest and silver while keeping their raw pages, and a page
directory with no manifest is invisible to every later prune, so those bytes can
only be reclaimed by hand. A horizon deep enough to hit that is one config edit
away and fails silently, which is why it is a refusal rather than a comment.

Silver is shared between profiles (config/shared_paths.yml) and its per-run files
are keyed by run id alone (`silver/silver_trials/run_id=X.parquet`), so nothing in
the *path* says which profile owns a file. What keeps one profile's prune inside
its own artifacts is that its candidates are named by that profile's own manifests,
plus the global uniqueness of `new_run_id()` (UTC-compact + 8 hex chars). The
containment check in `_remove` is the backstop if that assumption ever breaks.

Only `status == "success"` runs are candidates. `partial` / `failed` runs are the
forensic record of a bad ingest and are small (a page cap or a failure truncates
them). Quarantine reports are never touched.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from src.config import PathsConfig, RetentionConfig
from src.ingest.snapshot_manifest import load_manifests

if TYPE_CHECKING:
    from src.profiles import IndicationProfile, ProfileRegistry

# A run id becomes a path component in every deletion target below, and it is
# read out of manifest *content*. new_run_id() (src/ingest/snapshot_manifest.py:
# 39) emits UTC-compact + hex, so anything else — a slash, a dot-dot, an empty
# string — is a corrupt or forged manifest and must never be joined onto a root.
_RUN_ID = re.compile(r"[0-9A-Za-z_]+")


class RetentionError(Exception):
    """Refusal to prune under a config that would delete live warehouse history
    or strand raw pages beyond the reach of every later prune."""


@dataclass(frozen=True)
class PrunedRun:
    profile_id: str
    run_id: str
    removed: list[Path] = field(default_factory=list)


def _run_dir_name(run_id: str) -> str:
    return f"run_id={run_id}"


def runs_to_prune(run_ids: Sequence[str], keep: int) -> list[str]:
    """Oldest-first list of the runs to remove, keeping the newest `keep`.

    ingestion_run_id is UTC-compact (src/ingest/snapshot_manifest.py:39), so
    lexicographic order is chronological order.
    """
    if keep < 0:
        raise RetentionError(f"keep must be >= 0, got {keep}")
    ordered = sorted(run_ids)
    if keep >= len(ordered):
        return []
    return ordered[: len(ordered) - keep]


def _prunable_run_ids(run_ids: Iterable[str], *, profile_id: str) -> list[str]:
    """Drop ids that cannot be used to build a path (see _RUN_ID).

    Refusing them here means no deletion code downstream has to trust manifest
    content; a malformed id is reported, not joined.
    """
    safe: list[str] = []
    for run_id in run_ids:
        if _RUN_ID.fullmatch(run_id):
            safe.append(run_id)
        else:
            logger.error(
                "[{}] ignoring manifest with unusable ingestion_run_id {!r}: a run id "
                "must be a single path-safe segment.",
                profile_id,
                run_id,
            )
    return safe


def prune_profile(
    profile: IndicationProfile,
    retention: RetentionConfig | None = None,
    *,
    dry_run: bool = False,
) -> list[PrunedRun]:
    cfg = retention or profile.config.retention
    if cfg.bronze_runs_to_keep > cfg.snapshot_runs_to_keep:
        raise RetentionError(
            f"retention.bronze_runs_to_keep ({cfg.bronze_runs_to_keep}) exceeds "
            f"retention.snapshot_runs_to_keep ({cfg.snapshot_runs_to_keep}): the runs "
            "between the two horizons lose their manifest and silver while keeping "
            "their raw pages, and a page directory no manifest names is invisible "
            "to every later prune — those bytes strand. Raw pages are the "
            "expensive bytes and exist only to re-derive silver, so lower "
            "bronze_runs_to_keep to at or below the snapshot depth; keeping "
            "snapshots deeper than bronze is the safe direction."
        )

    paths = profile.config.paths
    manifests = load_manifests(paths.bronze_manifests)
    success_ids = _prunable_run_ids(
        (m.ingestion_run_id for m in manifests if m.status == "success"),
        profile_id=profile.profile_id,
    )

    # Which runs the warehouse actually holds, from the silver run files dbt globs.
    silver_runs = {
        p.name.split("=", 1)[1].removesuffix(".parquet")
        for p in paths.silver.glob("*/run_id=*.parquet")
    }

    snapshot_victims = set(runs_to_prune(success_ids, cfg.snapshot_runs_to_keep))
    bronze_victims = set(runs_to_prune(success_ids, cfg.bronze_runs_to_keep))

    # Bronze may only be dropped once its silver exists, otherwise the run is
    # unrecoverable: build_silver_entities raises FileNotFoundError for a missing
    # page directory (src/transform/build_silver_entities.py:59). And a snapshot
    # may only be dropped when its bronze is going too.
    #
    # The silver half of that is a no-op for an `ingest_only` profile, so it is
    # skipped: no transform ever runs for full_catalog, silver never exists by
    # design, and the guard therefore protected nothing while making
    # `bronze_runs_to_keep` inert — its bronze would have been pruned only where
    # the snapshot horizon prunes too, i.e. ~6 runs of a whole-registry pull kept
    # forever on a 1 GB volume. `config/profiles/full_catalog.yml` sets bronze to
    # 1 precisely because those are the largest single consumer, so for that
    # profile the bronze horizon is the only thing that bounds them. A
    # *refreshable* profile with no silver yet is the case the guard exists for —
    # it still has a transform to feed — and keeps the protection.
    unsafe: set[str] = set()
    if not profile.ingest_only:
        unsafe = {r for r in bronze_victims if r not in silver_runs and r not in snapshot_victims}
    bronze_victims -= unsafe
    for run_id in sorted(unsafe):
        logger.warning(
            "[{}] keeping bronze for run {}: silver absent, pruning it would make "
            "the run unrecoverable.",
            profile.profile_id,
            run_id,
        )

    pruned: list[PrunedRun] = []
    for run_id in sorted(snapshot_victims | bronze_victims):
        removed: list[Path] = []
        if run_id in snapshot_victims:
            removed += _snapshot_artifacts(paths, run_id)
        if run_id in bronze_victims:
            removed += _bronze_artifacts(paths, run_id)
        candidates = list(dict.fromkeys(removed))
        if not candidates:
            continue
        deleted = candidates if dry_run else _remove(paths, candidates, profile, run_id)
        pruned.append(PrunedRun(profile.profile_id, run_id, deleted))
        logger.info(
            "[{}] pruned run {} ({} path(s){})",
            profile.profile_id,
            run_id,
            len(deleted),
            ", dry-run" if dry_run else "",
        )
    return pruned


def _retention_roots(paths: PathsConfig) -> tuple[Path, ...]:
    """The only trees a prune may delete inside, for this profile."""
    return (paths.bronze_api_responses, paths.bronze_manifests, paths.silver)


def _contained(path: Path, roots: Sequence[Path]) -> bool:
    """Strict containment: the path must live *under* one of the profile roots."""
    resolved = path.resolve()
    return any(root in resolved.parents for root in roots)


def _remove(
    paths: PathsConfig, candidates: Sequence[Path], profile: IndicationProfile, run_id: str
) -> list[Path]:
    """Delete the candidates that are provably inside this profile's run roots.

    The run-id shape check in _prunable_run_ids already keeps a manifest from
    naming another tree; this is the second, structural layer — nothing reaches
    rmtree/unlink unless its resolved path is under a root this profile owns.
    """
    roots = tuple(root.resolve() for root in _retention_roots(paths))
    deleted: list[Path] = []
    for path in candidates:
        if not _contained(path, roots):
            logger.error(
                "[{}] refusing to delete {} for run {}: it does not resolve inside "
                "this profile's run roots.",
                profile.profile_id,
                path,
                run_id,
            )
            continue
        resolved = path.resolve()
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink(missing_ok=True)
        deleted.append(path)
    return deleted


def _existing(paths: Iterable[Path]) -> list[Path]:
    """Report only what is really there, so a dry run names real bytes."""
    return [path for path in paths if path.exists()]


def _bronze_artifacts(paths: PathsConfig, run_id: str) -> list[Path]:
    """One run's raw API pages. The manifests dir follows snapshots, not this."""
    return _existing([paths.bronze_api_responses / _run_dir_name(run_id)])


def _snapshot_artifacts(paths: PathsConfig, run_id: str) -> list[Path]:
    """Everything that records one warehouse snapshot: silver entities plus the
    run's manifest and flat summaries."""
    stem = _run_dir_name(run_id)
    out: list[Path] = [
        paths.silver / "silver_trials" / f"{stem}.parquet",
        paths.silver / "_profiles" / f"profile_{run_id}.json",
        paths.silver / "_transform_stats" / f"{stem}.json",
        paths.bronze_manifests / f"manifest_{run_id}.json",
        paths.bronze_manifests / f"summary_{run_id}.parquet",
        paths.bronze_manifests / f"summary_{run_id}.csv",
    ]
    if paths.silver.is_dir():
        for entity_dir in paths.silver.iterdir():
            if not entity_dir.is_dir() or entity_dir.name.startswith("_"):
                continue
            out.append(entity_dir / f"{stem}.parquet")
    return _existing(out)


def prune_all(registry: ProfileRegistry | None = None, *, dry_run: bool = False) -> list[PrunedRun]:
    """Prune every discovered profile, including ingest_only. Its bronze is the
    largest single consumer on the volume and, with no silver ever to wait for,
    `bronze_runs_to_keep` is the only horizon that bounds it."""
    if registry is None:
        from src.profiles import get_registry

        registry = get_registry()
    profiles = registry.all()
    if not profiles:
        raise RetentionError("Prune refused: the registry has no profiles, so nothing was bounded.")
    removed: list[PrunedRun] = []
    for profile in profiles:
        try:
            removed += prune_profile(profile, dry_run=dry_run)
        except RetentionError:
            raise
        except Exception as exc:  # a missing dir on one profile must not stop the rest
            logger.error("[{}] prune failed: {}", profile.profile_id, exc)
    return removed
