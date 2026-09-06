"""Silver asset: flatten bronze JSON runs into normalized Parquet entities."""

from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from src.profiles import get_registry
from src.transform.build_silver_entities import run_transform


@asset(
    name="silver_entities",
    deps=["ctg_raw_pages"],
    description=(
        "Flattened, normalized silver Parquet entities for every completed bronze "
        "run not yet transformed, across all refreshable profiles."
    ),
)
def silver_entities(context: AssetExecutionContext) -> MaterializeResult[None]:
    processed_by_profile: dict[str, list[str]] = {}
    for indication_profile in get_registry().refreshable():
        processed_by_profile[indication_profile.profile_id] = run_transform(
            run_id=None, force=False, profile=indication_profile
        )
    total = sum(len(runs) for runs in processed_by_profile.values())
    context.log.info(
        f"Transformed {total} bronze run(s) across "
        f"{len(processed_by_profile)} profile(s): {processed_by_profile}"
    )
    return MaterializeResult(
        metadata={
            "processed_runs": MetadataValue.json(processed_by_profile),
            "processed_count": total,
        }
    )
