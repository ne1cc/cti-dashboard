"""Bronze asset: snapshot the whole ClinicalTrials.gov registry into immutable raw pages."""

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
from src.transform.preset_runs import registry_run_is_incremental


class IngestParams(Config):
    """Job-level knobs for the one registry-wide run.

    No `condition`: the registry run has no condition filter; each indication
    profile is a preset (MeSH node(s) + default filters) applied to its pages
    in `silver_entities`.
    """

    full_refresh: bool = False
    max_pages: int | None = None


@asset(
    name="ctg_raw_pages",
    description=(
        "Paginated snapshot of the whole ClinicalTrials.gov API v2 registry "
        "(first run and --full-refresh), or the studies changed since the last "
        "run, written to bronze as raw JSON pages plus an ingestion manifest."
    ),
    # Three attempts with exponential backoff for transient registry outages.
    retry_policy=RetryPolicy(max_retries=2, delay=30, backoff=Backoff.EXPONENTIAL),
)
def ctg_raw_pages(context: AssetExecutionContext, config: IngestParams) -> MaterializeResult[None]:
    """One materialization = one registry-wide run, the unit every preset derives from.

    Contract:
    - Returns metadata `registry_run` for a run that reached `success`.
    - Raises when the run fails or stops short (`partial`, from `max_pages`): the
      downstream silver and checks must not read a snapshot the API did not finish.
    """
    registry_profile = get_registry().registry_wide()
    pid = registry_profile.profile_id
    # 1. Pull the registry → incremental once a base snapshot exists, so a weekly
    # refresh fetches only changed studies instead of ~5 GB again.
    manifest = run_ingestion(
        full_refresh=config.full_refresh,
        max_pages=config.max_pages,
        config=registry_profile,
        incremental_refresh=registry_run_is_incremental(registry_profile, config.full_refresh),
        require_silver=False,
    )
    # 2. Refuse anything but a finished run → partial/failed would derive from missing pages.
    if manifest.status != "success":
        raise RuntimeError(
            f"Ingestion for {pid} ended {manifest.status}: {manifest.error or 'no error detail'}"
        )
    context.log.info(
        f"[{pid}] ingestion run {manifest.ingestion_run_id} "
        f"status={manifest.status} records={manifest.record_count} "
        f"pages={manifest.page_count}"
    )
    return MaterializeResult(
        metadata={
            "registry_run": MetadataValue.json(
                {
                    "profile": pid,
                    "ingestion_run_id": manifest.ingestion_run_id,
                    "status": manifest.status,
                    "record_count": manifest.record_count,
                    "page_count": manifest.page_count,
                    "query_hash": manifest.query_hash,
                }
            )
        }
    )
