"""Bronze asset: snapshot ClinicalTrials.gov studies into immutable raw pages."""

from dagster import (
    AssetExecutionContext,
    Backoff,
    Config,
    MaterializeResult,
    MetadataValue,
    RetryPolicy,
    asset,
)

from src.ingest.extract_studies import run_ingestion
from src.profiles import get_registry


class IngestParams(Config):
    """Job-level knobs applied to *every* refreshable profile.

    No `condition`: each profile's `query_params` in config/profiles/*.yml is
    its scope, and one override applied across profiles would query NSCLC with
    an Alzheimer's string.
    """

    full_refresh: bool = False
    max_pages: int | None = None


@asset(
    name="ctg_raw_pages",
    description=(
        "Paginated snapshot of ClinicalTrials.gov API v2 studies, one run per "
        "refreshable indication profile, written to bronze as raw JSON pages "
        "plus a signed ingestion manifest."
    ),
    # Three attempts with exponential backoff for transient registry outages.
    retry_policy=RetryPolicy(max_retries=2, delay=30, backoff=Backoff.EXPONENTIAL),
)
def ctg_raw_pages(context: AssetExecutionContext, config: IngestParams) -> MaterializeResult[None]:
    """One materialization = one refresh of every profile `make pipeline` refreshes.

    Per-profile asset keys were the alternative and are not taken: the
    container's refresh path is `make pipeline` (entrypoint.sh), so N keys would
    double the UI's surface for a graph nothing schedules, and the blocking
    checks would still have to aggregate across all N.
    """
    runs: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    for indication_profile in get_registry().refreshable():
        pid = indication_profile.profile_id
        manifest = run_ingestion(
            full_refresh=config.full_refresh,
            max_pages=config.max_pages,
            config=indication_profile,
        )
        if manifest.status == "failed":
            # Collect, do not raise: a profile that fails must not stop the
            # others' runs from being recorded in this materialization's log.
            failures.append(f"{pid}: {manifest.error or 'no error detail'}")
            continue
        context.log.info(
            f"[{pid}] ingestion run {manifest.ingestion_run_id} "
            f"status={manifest.status} records={manifest.record_count} "
            f"pages={manifest.page_count}"
        )
        runs[pid] = {
            "ingestion_run_id": manifest.ingestion_run_id,
            "status": manifest.status,
            "record_count": manifest.record_count,
            "page_count": manifest.page_count,
            "query_hash": manifest.query_hash,
        }
    if failures:
        raise RuntimeError("Ingestion failed for: " + "; ".join(failures))
    if not runs:
        raise RuntimeError(
            "No refreshable profiles produced a run — check config/profiles/*.yml "
            "and the ingest_only flags."
        )
    return MaterializeResult(metadata={"runs_by_profile": MetadataValue.json(runs)})
