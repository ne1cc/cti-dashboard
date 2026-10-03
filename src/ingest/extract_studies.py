"""Ingestion orchestrator.

Note on history: the ClinicalTrials.gov API returns only each study's
*current* record. Longitudinal status history is created by this project
through repeated snapshot runs — every run saves unmodified page JSON under
a unique ingestion_run_id, and downstream models compare snapshots.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.config import ProjectConfig, get_config
from src.ingest.ctg_client import CTGClient
from src.ingest.pagination import iter_pages
from src.ingest.snapshot_manifest import (
    IngestionManifest,
    find_reusable_run,
    new_run_id,
    write_manifest,
    write_summary,
)
from src.ingest.validate_api_payload import (
    QuarantineRecord,
    screen_studies,
    validate_page,
    write_quarantine_report,
)
from src.utils.dates import utc_now
from src.utils.hashing import sha256_json
from src.utils.logging import setup_logging
from src.utils.paths import ensure_dir

if TYPE_CHECKING:
    from src.profiles import IndicationProfile


def run_ingestion(
    condition: str | None = None,
    full_refresh: bool = False,
    max_pages: int | None = None,
    profile: str = "default",
    config: ProjectConfig | IndicationProfile | None = None,
) -> IngestionManifest:
    """Run a bronze ingestion snapshot.

    Args:
        config: Either a raw ProjectConfig (legacy callers) or an IndicationProfile.
                When an IndicationProfile is passed, the profile string arg is
                ignored — the profile's id is used instead.
    """
    log = setup_logging()

    # Unwrap IndicationProfile if that's what we received.
    from src.profiles import IndicationProfile

    if isinstance(config, IndicationProfile):
        profile = config.profile_id
        cfg = config.config
    elif isinstance(config, ProjectConfig):
        cfg = config
    elif config is not None and hasattr(config, "profile_id"):
        config_any: Any = config
        profile = str(config_any.profile_id)
        cfg = config_any.config if hasattr(config, "config") else get_config()
    else:
        cfg = get_config()

    if condition is not None:
        # Reject explicit-but-empty input instead of silently ignoring it (or
        # worse, sending a whitespace filter to the API), and strip stray
        # padding so query_hash reuse treats " X " and "X" as the same query.
        condition = condition.strip()
        if not condition:
            raise ValueError(
                "condition was passed but is empty or whitespace-only; omit "
                "--condition or provide a condition name."
            )

    if profile in ("full-catalog", "full_catalog") and condition:
        raise ValueError(
            f"The {profile} profile snapshots all conditions worldwide and "
            f"cannot be combined with a condition filter (got {condition!r}); "
            "use a condition-scoped profile for condition-scoped ingestion."
        )
    manifests_dir = cfg.paths.bronze_manifests

    with CTGClient(cfg.api) as client:
        params = client.build_params(condition=condition)
        query_hash = sha256_json({"endpoint": cfg.api.studies_url, "params": params})

        if not full_refresh:
            reusable = find_reusable_run(
                manifests_dir, query_hash, cfg.ingestion.reuse_window_hours
            )
            if reusable is not None:
                log.info(
                    "Incremental mode: reusing completed run {} (started {}); "
                    "use --full-refresh to force a new snapshot.",
                    reusable.ingestion_run_id,
                    reusable.started_at_utc,
                )
                return reusable

        run_id = new_run_id()
        run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
        ensure_dir(run_dir)

        manifest = IngestionManifest(
            ingestion_run_id=run_id,
            query_hash=query_hash,
            endpoint=cfg.api.studies_url,
            condition=params.get("query.cond"),
            params=params,
            mode="full_refresh" if full_refresh else "incremental",
            profile=profile,
            status="running",
            started_at_utc=utc_now(),
        )
        write_manifest(manifests_dir, manifest)
        log.info("Starting ingestion run {} against {}", run_id, cfg.api.studies_url)

        quarantined: list[QuarantineRecord] = []
        last_next_token: str | None = None
        try:
            for page in iter_pages(client, params, max_pages=max_pages):
                retrieved_at_utc = utc_now().isoformat()
                page_path = run_dir / f"page={page.page_number:05d}.json"
                page_path.write_text(page.raw_text, encoding="utf-8")
                metadata_dir = ensure_dir(run_dir / "_page_metadata")
                (metadata_dir / page_path.name).write_text(
                    json.dumps({"retrieved_at_utc": retrieved_at_utc}), encoding="utf-8"
                )
                envelope = validate_page(page.payload)
                if envelope.totalCount is not None:
                    manifest.total_count_reported = envelope.totalCount
                manifest.record_count += len(envelope.studies)
                manifest.page_count += 1
                last_next_token = envelope.nextPageToken
                quarantined.extend(screen_studies(envelope.studies, page.page_number))
                log.info(
                    "Saved page {} ({} studies, {} total so far)",
                    page.page_number,
                    len(envelope.studies),
                    manifest.record_count,
                )
            manifest.status = "partial" if last_next_token else "success"
            if manifest.status == "partial":
                log.warning(
                    "Run stopped by --max-pages before the API was exhausted; "
                    "marked 'partial' and excluded from reuse and downstream metrics."
                )
        except Exception as exc:
            manifest.status = "failed"
            manifest.error = f"{type(exc).__name__}: {exc}"
            log.error("Ingestion run {} failed: {}", run_id, manifest.error)
            raise
        finally:
            manifest.ended_at_utc = utc_now()
            manifest.quarantined_record_count = len(quarantined)
            write_manifest(manifests_dir, manifest)
            write_summary(manifests_dir, manifest)
            if quarantined:
                report = write_quarantine_report(cfg.paths.quarantine, run_id, quarantined)
                log.warning("{} records quarantined; report: {}", len(quarantined), report)

    log.info(
        "Run {} finished: status={} pages={} records={} quarantined={} total_reported={}",
        manifest.ingestion_run_id,
        manifest.status,
        manifest.page_count,
        manifest.record_count,
        manifest.quarantined_record_count,
        manifest.total_count_reported,
    )
    return manifest
