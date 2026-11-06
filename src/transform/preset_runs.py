"""Presets over the registry-wide run (issue #147, step 2 of 4 of #54).

One registry-wide ingestion run is the unit of ingest. An indication profile is
a preset over it: a name, MeSH node(s) and default filters, applied locally in a
silver pass that selects the preset's studies from the registry run's pages.
Each pass writes an ordinary per-profile run (silver + transform stats + a
manifest in the preset's own manifests tree), so the dbt sources, reconciliation,
the freshness marker and retention keep reading one run per profile.

Known difference from the old per-profile `query.cond` pull: CTG expanded the
condition server-side with its own synonym handling; the local match is the
MeSH terms CTG derived for each study. Measured differences are recorded in
docs/profile-retirement-expected-diffs.md.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from src.ingest.extract_studies import INCREMENTAL_OVERLAP_DAYS
from src.ingest.snapshot_manifest import (
    IngestionManifest,
    load_manifests,
    new_run_id,
    write_manifest,
    write_summary,
)
from src.transform.build_silver_entities import PresetSource, build_silver_for_run
from src.transform.flatten_studies import dig, extract_condition_mesh
from src.transform.silver_stats import load_transform_stats
from src.utils.dates import utc_now
from src.utils.logging import setup_logging
from src.utils.text import normalize_text

if TYPE_CHECKING:
    from src.profiles import IndicationProfile

# scope.* values meaning "no filter" on the registry-wide profile.
_NO_FILTER_PREFIX = "ALL"

# Projection entries that carry the MeSH terms the selector matches on.
_MESH_FIELDS = {"derivedSection", "derivedSection.conditionBrowseModule"}


def preset_selector(preset: IndicationProfile) -> Callable[[dict[str, Any]], bool]:
    """Predicate that is True for the studies belonging to `preset`.

    Contract:
    - A study matches when it passes the preset's default filters
      (`scope.study_type`, `scope.statuses`) AND any of its MeSH terms, direct or
      ancestor, equals one of `preset.mesh_terms` after text normalization.
    - A study with no derived MeSH never matches (about 4% of records).
    - Raises ValueError when the preset has no MeSH terms: it would select nothing.
    """
    if not preset.mesh_terms:
        raise ValueError(
            f"Profile '{preset.profile_id}' has no preset.mesh_terms in its "
            "config/profiles/*.yml, so it cannot be derived from the registry-wide run."
        )
    wanted = {t for t in map(normalize_text, preset.mesh_terms) if t}
    scope = preset.config.scope
    study_type = str(scope.get("study_type") or _NO_FILTER_PREFIX)
    statuses = set(scope.get("statuses") or [])

    def select(study: dict[str, Any]) -> bool:
        # 1. Default filters → cheap fields first, so most studies skip the MeSH walk.
        if not study_type.startswith(_NO_FILTER_PREFIX) and (
            dig(study, "protocolSection", "designModule", "studyType") != study_type
        ):
            return False
        if statuses and dig(study, "protocolSection", "statusModule", "overallStatus") not in (
            statuses
        ):
            return False
        # 2. MeSH node(s) → direct terms and ancestors both count: CTG lists the
        # closure, so "Dementia" reaches every dementia subtype.
        return any(normalize_text(row["term"]) in wanted for row in extract_condition_mesh(study))

    return select


def _carries_mesh(run: IngestionManifest) -> bool:
    """False for a run whose field projection leaves out the MeSH terms.

    No `fields` param means CTG sent whole records, which include them.
    """
    fields = run.params.get("fields")
    return not fields or not _MESH_FIELDS.isdisjoint(fields.split("|"))


def registry_run_is_incremental(registry: IndicationProfile, full_refresh: bool) -> bool:
    """True when the next registry-wide pull should be an incremental refresh.

    A weekly refresh fetches only studies changed since the last successful run
    (a whole-registry snapshot is ~5 GB); the first run, an explicit
    `full_refresh`, or a newest run without MeSH terms (written before presets
    existed, so no preset can be derived from it or from an increment on it)
    pulls everything.
    """
    if full_refresh:
        return False
    successes = [
        m for m in load_manifests(registry.config.paths.bronze_manifests) if m.status == "success"
    ]
    return bool(successes) and _carries_mesh(max(successes, key=lambda m: m.started_at_utc))


def pending_source_runs(
    registry: IndicationProfile, preset: IndicationProfile
) -> list[IngestionManifest]:
    """Registry-wide success runs `preset` has not been derived from, oldest first.

    Contract:
    - Order is monotonic: a run started before one the preset already derived from
      is never returned, so an older snapshot cannot be published under a newer
      run id.
    - The list starts at the latest full snapshot among the candidates: earlier
      runs are superseded by it.
    - Raises ValueError when no full base exists and the preset cannot continue
      from its last derived run: it has none, or a registry run between them was
      pruned before the preset derived it (silently skipping it would leave that
      run's changes out). Recover with `orchestrate --full-refresh`.
    - Raises ValueError when a candidate was pulled without MeSH terms.
    """
    sources = sorted(
        (
            m
            for m in load_manifests(registry.config.paths.bronze_manifests)
            if m.status == "success"
        ),
        key=lambda m: m.started_at_utc,
    )
    derived = [
        m
        for m in load_manifests(preset.config.paths.bronze_manifests)
        if m.status == "success" and m.source_run_id
    ]
    last = max(derived, key=lambda m: m.started_at_utc, default=None)
    done = {m.source_run_id for m in derived}
    pending = [
        m
        for m in sources
        if m.ingestion_run_id not in done
        and (last is None or m.started_at_utc > last.started_at_utc)
    ]
    if not pending:
        return pending
    fulls = [i for i, m in enumerate(pending) if not m.updated_since_utc]
    if fulls:
        pending = pending[fulls[-1] :]
    elif last is None:
        raise ValueError(
            f"Profile '{preset.profile_id}' has no derived run and the retained registry "
            "runs are all incremental; run 'orchestrate --full-refresh' for a base snapshot."
        )
    elif str(pending[0].updated_since_utc) > _covered_until(last):
        # The first increment starts after the day the last derived run began, so a
        # registry run in between is gone.
        raise ValueError(
            f"Profile '{preset.profile_id}' last derived registry run {last.source_run_id}, "
            f"but the next retained run {pending[0].ingestion_run_id} only covers changes since "
            f"{pending[0].updated_since_utc}: a registry run in between was pruned before this "
            "preset derived it. Run 'orchestrate --full-refresh' for a new base snapshot."
        )
    stale = [m.ingestion_run_id for m in pending if not _carries_mesh(m)]
    if stale:
        raise ValueError(
            f"Registry run(s) {', '.join(stale)} were pulled without derivedSection."
            "conditionBrowseModule, so no preset can be derived from them; run "
            "'orchestrate --full-refresh'."
        )
    return pending


def _covered_until(derived: IngestionManifest) -> str:
    """Day a registry run's successor starts its changed-since window at.

    Mirrors `latest_snapshot_date`: the run's start minus the overlap.
    """
    return (derived.started_at_utc - timedelta(days=INCREMENTAL_OVERLAP_DAYS)).date().isoformat()


def derive_preset_runs(registry: IndicationProfile, preset: IndicationProfile) -> list[str]:
    """Derive `preset`'s silver from every pending registry-wide run.

    Contract:
    - Returns the new derived run ids (empty when nothing is pending).
    - Each run writes a `running` manifest first and flips it to `success` (with
      the selected study count and a summary) only after silver is complete, so
      an interrupted pass leaves no success manifest without silver.
    - Raises on the first failing run, after recording it as `failed`.

    Limits: an incremental registry run only holds changed studies, so a study that
    stops matching without the registry reporting a change (a preset edit in
    config/profiles/*.yml, a study withdrawn from the registry) stays in the
    carried-forward silver until the next full registry snapshot. A study the run
    does touch is re-selected or dropped, never carried forward.
    """
    log = setup_logging()
    selector = preset_selector(preset)
    manifests_dir = preset.config.paths.bronze_manifests
    created: list[str] = []
    for source in pending_source_runs(registry, preset):
        run_id = new_run_id()
        # 1. Provisional manifest → record_count is the registry total, which
        # build_silver_for_run checks against the pages it scans.
        manifest = source.model_copy(
            update={
                "ingestion_run_id": run_id,
                "profile": preset.profile_id,
                "status": "running",
                "source_run_id": source.ingestion_run_id,
                "ended_at_utc": None,
            }
        )
        write_manifest(manifests_dir, manifest)
        run_dir = registry.config.paths.bronze_api_responses / f"run_id={source.ingestion_run_id}"
        try:
            # 2. Select + flatten → silver and transform stats for the derived run.
            build_silver_for_run(
                manifest,
                preset.config,
                profile=preset,
                source=PresetSource(run_dir=run_dir, select=selector),
            )
        except BaseException as exc:
            failed = manifest.model_copy(
                update={
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "ended_at_utc": utc_now(),
                }
            )
            write_manifest(manifests_dir, failed)
            raise
        # 3. Publish → the selected count is the run's own record/total count.
        stats = load_transform_stats(preset.config, run_id) or {}
        selected = int(stats.get("manifest_record_count", 0))
        done = manifest.model_copy(
            update={
                "status": "success",
                "record_count": selected,
                "total_count_reported": selected,
                "ended_at_utc": utc_now(),
            }
        )
        write_manifest(manifests_dir, done)
        write_summary(manifests_dir, done)
        log.info(
            "[{}] derived run {} from registry run {}: {} of {} studies selected.",
            preset.profile_id,
            run_id,
            source.ingestion_run_id,
            selected,
            source.record_count,
        )
        created.append(run_id)
    return created
