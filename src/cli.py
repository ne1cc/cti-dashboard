"""Command-line entry point: python -m src.cli <command>."""

import argparse

from src.profiles import IndicationProfile, get_registry, normalize_profile_id
from src.utils.logging import setup_logging


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with all pipeline subcommands.

    Configures argument definitions and flags for the four core subcommands:
    ``ingest``, ``transform``, ``quality-report``, and ``orchestrate``.

    Returns:
        argparse.ArgumentParser: Configured argument parser for the pipeline CLI.
    """
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description=(
            "Clinical Trial Access & Recruitment Competition Intelligence pipeline. "
            "Public-registry-based planning signals only — not a recruitment forecast."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest", help="Snapshot studies from ClinicalTrials.gov API v2 into bronze."
    )
    ingest.add_argument(
        "--condition",
        default=None,
        help='Condition query (default: query.cond from profile config, e.g. "Alzheimer Disease").',
    )
    ingest.add_argument(
        "--full-refresh",
        action="store_true",
        help="Force a new snapshot even if a recent completed run exists for this query.",
    )
    ingest.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional page cap for smoke tests (partial runs stay marked incomplete-by-cap).",
    )
    ingest.add_argument(
        "--profile",
        default="default",
        help=(
            "Indication profile ID from config/profiles/ (e.g. 'adrd', 'full_catalog'). "
            "Legacy aliases 'default' → adrd and 'full-catalog' → full_catalog are accepted. "
            "Use 'orchestrate' to run all discovered profiles in one command."
        ),
    )

    transform = subparsers.add_parser(
        "transform", help="Flatten bronze JSON runs into normalized silver Parquet entities."
    )
    transform.add_argument(
        "--run-id",
        default=None,
        help="Transform one specific run (default: all completed runs not yet transformed).",
    )
    transform.add_argument(
        "--force",
        action="store_true",
        help="Rebuild silver outputs even if they already exist.",
    )
    transform.add_argument(
        "--profile",
        default="default",
        help=(
            "Indication profile ID from config/profiles/ (e.g. 'adrd', "
            "'oncology_nsclc'). Legacy aliases 'default' and 'full-catalog' are "
            "accepted. ingest_only profiles are refused: they have no taxonomy."
        ),
    )

    quality = subparsers.add_parser(
        "quality-report",
        help="Build the Markdown data-quality report (reliability, reconciliation, drift).",
    )
    quality.add_argument(
        "--update-schema-baseline",
        action="store_true",
        help="Accept the latest run's structure as the new schema baseline.",
    )

    orchestrate = subparsers.add_parser(
        "orchestrate",
        help=(
            "Discover the refreshable indication profiles in config/profiles/ "
            "(everything that is not ingest_only) and run ingest + transform for each. "
            "Use 'ingest --profile full_catalog' for the opt-in registry-wide pull."
        ),
    )
    orchestrate.add_argument(
        "--profile",
        default=None,
        help="Optional profile ID to orchestrate just one profile (default: all active profiles).",
    )
    orchestrate.add_argument(
        "--full-refresh",
        action="store_true",
        help="Force new ingestion snapshots for all profiles, ignoring incremental state.",
    )
    orchestrate.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional page cap per profile (smoke test mode).",
    )

    subparsers.add_parser(
        "init-data-dirs",
        help=(
            "Create every bronze/silver/gold/warehouse/quarantine directory the "
            "discovered profiles need. Idempotent; prints one path per line."
        ),
    )

    prune = subparsers.add_parser(
        "prune-data",
        help="Delete run artifacts past the per-profile retention horizon.",
    )
    prune.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be removed without removing it.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Execute the pipeline CLI command specified by command-line arguments.

    Dispatches to the appropriate pipeline subroutines for ingestion, transformation,
    data quality reporting, or multi-profile orchestration based on parsed arguments.

    Args:
        argv: Command-line argument vector to parse. If None, arguments are read
            from sys.argv.

    Returns:
        int: Exit code (0 for success, non-zero for errors or failed runs).

    Raises:
        SystemExit: When invalid CLI arguments are provided to the parser.
    """
    args = build_parser().parse_args(argv)
    log = setup_logging()
    indication_profile: IndicationProfile | None = None

    if args.command == "ingest":
        from src.ingest.extract_studies import run_ingestion

        profile_id = normalize_profile_id(args.profile)

        try:
            registry = get_registry()
            indication_profile = registry.get(profile_id)
            manifest = run_ingestion(
                condition=args.condition,
                full_refresh=args.full_refresh,
                max_pages=args.max_pages,
                config=indication_profile,
            )
        except Exception as exc:
            log.error("Ingestion failed: {}", exc)
            return 1
        return 0 if manifest.status in ("success", "partial") else 1

    if args.command == "transform":
        from src.quality.profiling import profile_run
        from src.transform.build_silver_entities import run_transform

        try:
            indication_profile = get_registry().get(normalize_profile_id(args.profile))
        except KeyError as exc:
            log.error("Unknown profile: {}", exc)
            return 2
        if indication_profile.ingest_only:
            log.error(
                "Profile '{}' is ingest_only: no condition taxonomy, so bronze "
                "cannot be transformed. Use 'orchestrate' for the refreshable profiles.",
                indication_profile.profile_id,
            )
            return 2
        try:
            processed = run_transform(
                run_id=args.run_id, force=args.force, profile=indication_profile
            )
            for run_id in processed:
                profile_run(run_id, config=indication_profile.config)
        except Exception as exc:
            log.error("Transform failed: {}", exc)
            return 1
        return 0

    if args.command == "quality-report":
        from src.ingest.snapshot_manifest import load_manifests
        from src.quality.data_quality_report import build_report
        from src.quality.schema_drift import check_drift

        try:
            if args.update_schema_baseline:
                # One baseline per profile tree (src/quality/schema_drift.py:55);
                # updating only the default tree would freeze every other
                # profile's baseline against a run it never made.
                for indication_profile in get_registry().refreshable():
                    cfg = indication_profile.config
                    success = [
                        m
                        for m in load_manifests(cfg.paths.bronze_manifests)
                        if m.status == "success"
                    ]
                    if success:
                        latest = max(success, key=lambda m: m.ingestion_run_id)
                        check_drift(latest.ingestion_run_id, update_baseline=True, cfg=cfg)
            build_report()
        except Exception as exc:
            log.error("Quality report failed: {}", exc)
            return 1
        return 0

    if args.command == "orchestrate":
        from src.ingest.extract_studies import run_ingestion
        from src.quality.profiling import profile_run
        from src.transform.build_silver_entities import run_transform

        registry = get_registry()
        if args.profile:
            # Same contract as `transform` above: 2 = usage, 1 = data failure.
            # An ingest_only profile is refused *here*, before the loop, because
            # the branch that follows would otherwise pull ~600 pages of bronze
            # and then hand the profile to run_transform — which has no taxonomy
            # to classify with. See src/transform/build_silver_entities.py.
            try:
                profiles = [registry.get(normalize_profile_id(args.profile))]
            except KeyError as exc:
                log.error("Unknown profile: {}", exc)
                return 2
            if profiles[0].ingest_only:
                log.error(
                    "Profile '{}' is ingest_only: it has no taxonomy, so it can never be "
                    "transformed. Use 'orchestrate' without --profile for the refreshable "
                    "profiles.",
                    profiles[0].profile_id,
                )
                return 2
        else:
            profiles = registry.refreshable()
            if not profiles:
                log.error("No refreshable profiles in the registry; refusing to report success.")
                return 1
        log.info("Orchestrating {} profile(s): {}", len(profiles), [p.profile_id for p in profiles])

        failed: list[str] = []
        for indication_profile in profiles:
            pid = indication_profile.profile_id
            try:
                log.info("→ [{}] ingesting…", pid)
                manifest = run_ingestion(
                    full_refresh=args.full_refresh,
                    max_pages=args.max_pages,
                    config=indication_profile,
                )
                if manifest.status == "failed":
                    log.error("[{}] ingestion failed: {}", pid, manifest.error)
                    failed.append(pid)
                    continue

                log.info("→ [{}] transforming…", pid)
                processed = run_transform(profile=indication_profile)
                for run_id in processed:
                    # The profile's own config, as `transform` does above: with
                    # the default, profiling looks this run's manifest up in the
                    # adrd bronze tree and finds nothing for every other profile.
                    profile_run(run_id, config=indication_profile.config)
                log.info("→ [{}] done ({} run(s) transformed).", pid, len(processed))

            except Exception as exc:
                log.error("[{}] failed: {}", pid, exc)
                failed.append(pid)

        if failed:
            log.error("Orchestrate finished with errors on: {}", failed)
            return 1
        return 0

    if args.command == "init-data-dirs":
        from src.utils.paths import ensure_dir

        dirs: set = set()
        for indication_profile in get_registry().all():
            p = indication_profile.config.paths
            dirs.update(
                {
                    p.bronze_api_responses,
                    p.bronze_manifests,
                    p.quarantine,
                    p.silver,
                    p.gold,
                    p.duckdb.parent,
                }
            )
        if not dirs:
            log.error(
                "No profiles in the registry; refusing to report an empty data tree as success."
            )
            return 1
        for path in sorted(dirs):
            ensure_dir(path)
            print(path)
        return 0

    if args.command == "prune-data":
        from src.utils.retention import prune_all

        try:
            pruned = prune_all(dry_run=args.dry_run)
        except Exception as exc:
            log.error("Prune failed: {}", exc)
            return 1
        for entry in pruned:
            log.info(
                "[{}] pruned {} ({} path(s))",
                entry.profile_id,
                entry.run_id,
                len(entry.removed),
            )
        log.info("Prune complete: {} run(s) removed.", len(pruned))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
