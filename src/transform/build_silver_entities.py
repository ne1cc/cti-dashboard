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

import pyarrow.compute as pc
import pyarrow.parquet as pq

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


def previous_full_snapshot_id(
    cfg: ProjectConfig, current_run_id: str, manifests: list[IngestionManifest] | None = None
) -> str | None:
    """Run id of the newest completed full snapshot that silver already holds.

    An incremental run carries unchanged studies forward from this snapshot so
    its silver stays a complete registry state (dbt unions every run parquet).
    """
    manifests = manifests if manifests is not None else load_manifests(cfg.paths.bronze_manifests)
    candidates = [
        m
        for m in manifests
        if m.status == "success"
        and m.updated_since_utc is None
        and m.ingestion_run_id != current_run_id
        and _already_transformed(cfg, m.ingestion_run_id)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda m: m.started_at_utc).ingestion_run_id


def _carry_forward_rows(
    writer: SilverRunWriter,
    cfg: ProjectConfig,
    prev_run_id: str,
    seen_nct_ids: set[str],
) -> dict[str, int]:
    """Append rows of the previous full snapshot whose NCT IDs this incremental
    run did not touch. Returns per-entity carried row counts."""
    log = setup_logging()
    import pyarrow as pa

    carried: dict[str, int] = {}
    value_set = pa.array(sorted(seen_nct_ids))
    for entity in ENTITY_NAMES:
        prev_path = cfg.paths.silver / entity / f"run_id={prev_run_id}.parquet"
        table = pq.read_table(prev_path)
        mask = pc.invert(pc.is_in(table.column("nct_id"), value_set=value_set))
        kept = table.filter(mask)
        writer.add_table(entity, kept)
        carried[entity] = kept.num_rows
        log.info(
            "Run {}: carried {} unchanged {} row(s) from snapshot {}.",
            prev_path.parent.name.removeprefix("run_id="),
            kept.num_rows,
            entity,
            prev_run_id,
        )
    return carried


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
    if profile is not None:
        assert profile.taxonomy is not None
        taxonomy = profile.taxonomy
    else:
        taxonomy = get_taxonomy()
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

    # An incremental-refresh run holds changed studies only; silver must still
    # describe the complete registry state, so unchanged rows are carried
    # forward from the previous full snapshot. Resolve it before opening the
    # writer so a missing base fails loudly without leaving artifacts.
    updated_since = manifest.updated_since_utc
    carry_from: str | None = None
    if updated_since:
        carry_from = previous_full_snapshot_id(cfg, run_id)
        if carry_from is None:
            raise ValueError(
                f"Run {run_id} is an incremental refresh (updated_since_utc="
                f"{updated_since}) but no previous full snapshot with silver "
                "exists; run a full ingestion and transform first."
            )

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
        carried_counts: dict[str, int] = {}
        if carry_from is not None:
            carried_counts = _carry_forward_rows(
                writer, cfg, carry_from, {key[0] for key in seen_dedup_keys}
            )
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
    if updated_since:
        # Incremental: silver holds this run's changed studies plus the
        # previous snapshot's unchanged rows.
        expected += carried_counts.get("silver_trials", 0)
    if row_counts["silver_trials"] != expected:
        log.warning(
            "Run {}: silver_trials rows ({}) != manifest records minus exclusions "
            "plus carried rows ({}).",
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
