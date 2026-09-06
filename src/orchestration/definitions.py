"""Dagster Definitions: the single import surface for dagster dev and CI."""

from dagster import AssetSelection, Definitions, ScheduleDefinition, define_asset_job
from dagster_dbt import DbtCliResource

from src.orchestration.assets.bronze import ctg_raw_pages
from src.orchestration.assets.dbt_assets import clinical_trials_dbt_assets
from src.orchestration.assets.silver import silver_entities
from src.orchestration.checks import (
    bronze_silver_reconciliation,
    manifest_integrity,
    warehouse_reconciliation,
)

# Same scope as `make pipeline` (entrypoint.sh), which is what the container
# actually runs. This job exists for `dagster dev` and manual backfills; if the
# two ever diverge, the checks in src/orchestration/checks.py are the ones that
# will fail loudest, because they loop the registry too.
weekly_refresh = define_asset_job(
    name="weekly_refresh",
    selection=AssetSelection.all(),
)

# Registered on a cron, so `weekly_refresh` above is one scheduler daemon away
# from running unattended — no human ever has to materialize it. Nothing in the
# container arms it today (entrypoint.sh runs `make pipeline` and starts no
# `dagster dev`, webserver or daemon), but anyone who opens `dagster dev` for
# any reason, or adds a scheduler, starts a weekly refresh of every profile on
# the next Monday.
weekly_refresh_schedule = ScheduleDefinition(
    job=weekly_refresh,
    cron_schedule="0 13 * * 1",  # every Monday 13:00 UTC
    name="weekly_refresh_schedule",
)

defs = Definitions(
    assets=[ctg_raw_pages, silver_entities, clinical_trials_dbt_assets],
    asset_checks=[
        manifest_integrity,
        bronze_silver_reconciliation,
        warehouse_reconciliation,
    ],
    resources={
        "dbt": DbtCliResource(project_dir="dbt_clinical_trials"),
    },
    jobs=[weekly_refresh],
    schedules=[weekly_refresh_schedule],
)
