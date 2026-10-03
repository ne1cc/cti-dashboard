"""Bronze → Silver orchestration.

Processes completed (status=success) ingestion runs by default; partial and
failed runs are excluded from silver so no downstream metric can include an
incomplete snapshot. Duplicate NCT IDs within a run are deduplicated (first
occurrence kept) and reported, never silently ignored.

When called via the profile system, build_silver_for_run receives an
IndicationProfile that supplies the per-profile taxonomy and profile_id
(used to stamp every silver row with indication_profile_id and to correctly
de-duplicate: the same NCT ID in two profiles is valid, not a duplicate).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.config import ProjectConfig, get_config
from src.ingest.snapshot_manifest import IngestionManifest, load_manifests
from src.transform.export_parquet import DEFAULT_FLUSH_ROWS, SilverRunWriter
from src.transform.flatten_studies import flatten_study, iter_bronze_study_records
from src.transform.normalize_conditions import get_taxonomy
from src.transform.normalize_locations import get_geography_rules
from src.transform.silver_stats import write_transform_stats
from src.utils.logging import setup_logging

if TYPE_CHECKING:
    from src.profiles import IndicationProfile

# Module-level so tests can shrink the buffer without writing 50k rows.
FLUSH_ROWS = DEFAULT_FLUSH_ROWS
PROGRESS_EVERY = 100_000

ENTITY_NAMES = (
    "silver_trials",
    "silver_trial_conditions",
    "silver_trial_interventions",
    "silver_trial_sponsors",
    "silver_trial_locations",
    "silver_trial_outcomes",
)


def _already_transformed(cfg: ProjectConfig, run_id: str) -> bool:
    return all(
        (cfg.paths.silver / entity / f"run_id={run_id}.parquet").exists() for entity in ENTITY_NAMES
    )


def build_silver_for_run(
    manifest: IngestionManifest,
    cfg: ProjectConfig,
    profile: IndicationProfile | None = None,
) -> dict[str, int]:
    log = setup_logging()
    run_id = manifest.ingestion_run_id
    run_dir = cfg.paths.bronze_api_responses / f"run_id={run_id}"
    if not run_dir.exists():
        raise FileNotFoundError(f"Bronze pages missing for run {run_id}: {run_dir}")

    # A named profile with no taxonomy is a caller bug, not a licence to borrow
    # another profile's: the rows would carry this profile's
    # `indication_profile_id` while classifying conditions by someone else's
    # aliases. Only a caller that predates the registry (profile is None) gets the
    # global ADRD singleton.
    if profile is not None and profile.taxonomy is None:
        raise ValueError(
            f"Profile '{profile.profile_id}' has no condition_taxonomy and cannot be "
            "transformed; it is ingest_only (see its config/profiles/*.yml)."
        )
    taxonomy = profile.taxonomy if profile is not None else get_taxonomy()
    geography = get_geography_rules()
    profile_id = profile.profile_id if profile else "adrd"
    snapshot_ts = manifest.started_at_utc.isoformat()

    # Dedup key is (nct_id, indication_profile_id): the same NCT ID can legitimately
    # appear in two different indication profiles (e.g. a trial listed under both
    # ADRD and Parkinson's). Only deduplicate within the same profile's run.
    seen_dedup_keys: set[tuple[str, str]] = set()
    duplicate_count = 0
    skipped_no_nct = 0
    study_count = 0

    writer = SilverRunWriter(run_id, cfg.paths.silver, flush_rows=FLUSH_ROWS)
    try:
        for study, provenance in iter_bronze_study_records(run_dir):
            rows = flatten_study(
                study,
                run_id,
                snapshot_ts,
                taxonomy,
                geography,
                indication_profile_id=profile_id,
                **provenance,
            )
            nct_id = rows["silver_trials"][0]["nct_id"]
            if not nct_id:
                skipped_no_nct += 1  # already quarantined at ingestion
                continue
            dedup_key = (nct_id, profile_id)
            if dedup_key in seen_dedup_keys:
                duplicate_count += 1
                continue
            seen_dedup_keys.add(dedup_key)
            writer.add_rows(rows)
            study_count += 1
            if study_count % PROGRESS_EVERY == 0:
                log.info("Run {}: {} studies streamed.", run_id, study_count)
        counts = writer.close()
    except BaseException:
        writer.discard()
        raise

    if duplicate_count:
        log.warning(
            "Run {}: deduplicated {} repeated NCT IDs (kept first).", run_id, duplicate_count
        )
    if skipped_no_nct:
        log.warning(
            "Run {}: skipped {} records without NCT ID (quarantined).", run_id, skipped_no_nct
        )

    row_counts: dict[str, int] = {}
    for entity in ENTITY_NAMES:
        path = cfg.paths.silver / entity / f"run_id={run_id}.parquet"
        row_counts[entity] = counts[entity]
        log.info("Run {}: wrote {} rows -> {}", run_id, counts[entity], path)

    write_transform_stats(
        cfg,
        run_id,
        record_count=manifest.record_count,
        row_counts=row_counts,
        duplicate_nct_ids=duplicate_count,
        skipped_no_nct_id=skipped_no_nct,
    )

    expected = manifest.record_count - duplicate_count - skipped_no_nct
    if row_counts["silver_trials"] != expected:
        log.warning(
            "Run {}: silver_trials rows ({}) != manifest records minus exclusions ({}).",
            run_id,
            row_counts["silver_trials"],
            expected,
        )
    return row_counts


def run_transform(
    run_id: str | None = None,
    force: bool = False,
    config: ProjectConfig | None = None,
    profile: IndicationProfile | None = None,
) -> list[str]:
    """Transform bronze runs into silver. Returns processed run IDs.

    Args:
        profile: When provided, the transform uses the profile's taxonomy and
                 stamps indication_profile_id on every silver row. When None,
                 falls back to the global ADRD defaults (backward compatible).
    """
    log = setup_logging()
    cfg = config or (profile.config if profile else None) or get_config()
    manifests = load_manifests(cfg.paths.bronze_manifests)

    if run_id:
        selected = [m for m in manifests if m.ingestion_run_id == run_id]
        if not selected:
            raise ValueError(f"No manifest found for run {run_id}")
        if selected[0].status != "success":
            log.warning(
                "Run {} has status '{}' (not 'success'); transforming because it was "
                "explicitly requested. It remains excluded from complete-snapshot metrics.",
                run_id,
                selected[0].status,
            )
    else:
        selected = [m for m in manifests if m.status == "success"]

    processed: list[str] = []
    for manifest in selected:
        if not force and _already_transformed(cfg, manifest.ingestion_run_id):
            log.info(
                "Run {} already transformed; skipping (use --force to rebuild).",
                manifest.ingestion_run_id,
            )
            continue
        build_silver_for_run(manifest, cfg, profile=profile)
        processed.append(manifest.ingestion_run_id)

    if not processed:
        log.info("No runs to transform ({} manifests inspected).", len(manifests))
    return processed
