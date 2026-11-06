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
from typing import TYPE_CHECKING, Any

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


def registry_run_is_incremental(registry: IndicationProfile, full_refresh: bool) -> bool:
    """True when the next registry-wide pull should be an incremental refresh.

    A weekly refresh fetches only studies changed since the last successful run
    (a whole-registry snapshot is ~5 GB); the first run, or an explicit
    `full_refresh`, pulls everything.
    """
    if full_refresh:
        return False
    return any(
        m.status == "success" for m in load_manifests(registry.config.paths.bronze_manifests)
    )


def pending_source_runs(
    registry: IndicationProfile, preset: IndicationProfile
) -> list[IngestionManifest]:
    """Registry-wide success runs `preset` has not been derived from, oldest first.

    A preset with no derived run yet starts at the latest full snapshot: earlier
    runs are superseded by it, and an incremental run needs a base to carry from.
    """
    sources = sorted(
        (
            m
            for m in load_manifests(registry.config.paths.bronze_manifests)
            if m.status == "success"
        ),
        key=lambda m: m.started_at_utc,
    )
    derived = {
        m.source_run_id
        for m in load_manifests(preset.config.paths.bronze_manifests)
        if m.status == "success" and m.source_run_id
    }
    pending = [m for m in sources if m.ingestion_run_id not in derived]
    if pending and not derived:
        fulls = [i for i, m in enumerate(pending) if not m.updated_since_utc]
        if not fulls:
            raise ValueError(
                f"Profile '{preset.profile_id}' has no derived run and the retained registry "
                "runs are all incremental; run 'orchestrate --full-refresh' for a base snapshot."
            )
        pending = pending[fulls[-1] :]
    return pending


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
