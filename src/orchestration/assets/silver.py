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
    profiles = get_registry().refreshable()
    if not profiles:
        # Same refusal as `ctg_raw_pages` and `_refreshable_or_fail()`: a
        # materialization that loops nothing succeeds at nothing, and
        # `processed_count: 0` is then indistinguishable from a backlog that
        # happens to be empty.
        raise RuntimeError(
            "No refreshable profiles in the registry — check config/profiles/*.yml "
            "and the ingest_only flags before trusting a green silver asset."
        )
    processed_by_profile: dict[str, list[str]] = {}
    failures: list[str] = []
    for indication_profile in profiles:
        pid = indication_profile.profile_id
        try:
            processed = run_transform(run_id=None, force=False, profile=indication_profile)
        except Exception as exc:
            # Containment matching `src/cli.py`'s `orchestrate` loop and this
            # package's bronze asset: one profile's raise must not cost the
            # others their transform. The aggregate raise below keeps the run red.
            failures.append(f"{pid}: {exc}")
            continue
        processed_by_profile[pid] = processed
        # One bounded line per profile, as bronze does: interpolating the whole
        # {profile_id: [run_id, ...]} dict would size the log to the backlog.
        context.log.info(f"[{pid}] transformed {len(processed)} bronze run(s)")
    if failures:
        raise RuntimeError("Transform failed for: " + "; ".join(failures))
    total = sum(len(runs) for runs in processed_by_profile.values())
    context.log.info(
        f"Transformed {total} bronze run(s) across {len(processed_by_profile)} profile(s)"
    )
    return MaterializeResult(
        metadata={
            "processed_runs": MetadataValue.json(processed_by_profile),
            "processed_count": total,
        }
    )
