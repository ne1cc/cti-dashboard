from types import SimpleNamespace

from src.cli import build_parser, main
from src.quality.reconciliation import ReconciliationCheck


def test_cli_parser_ingest():
    parser = build_parser()
    args = parser.parse_args(
        ["ingest", "--condition", "Alzheimer Disease", "--full-refresh", "--max-pages", "5"]
    )
    assert args.command == "ingest"
    assert args.condition == "Alzheimer Disease"
    assert args.full_refresh is True
    assert args.max_pages == 5


def test_cli_parser_ingest_profile_default():
    """--profile default is still the default and maps to adrd at runtime."""
    parser = build_parser()
    args = parser.parse_args(["ingest"])
    assert args.profile == "default"


def test_cli_parser_ingest_profile_full_catalog_legacy_alias():
    """Legacy full-catalog hyphen alias is accepted by the parser."""
    parser = build_parser()
    args = parser.parse_args(["ingest", "--profile", "full-catalog"])
    assert args.profile == "full-catalog"


def test_cli_parser_ingest_profile_full_catalog_underscore():
    """New canonical full_catalog underscore name is accepted."""
    parser = build_parser()
    args = parser.parse_args(["ingest", "--profile", "full_catalog"])
    assert args.profile == "full_catalog"


def test_cli_main_ingest_resolves_profile_via_registry(monkeypatch):
    """main() resolves --profile to an IndicationProfile via ProfileRegistry."""
    captured = {}

    class FakeManifest:
        status = "success"

    def fake_run_ingestion(**kwargs):
        captured.update(kwargs)
        return FakeManifest()

    class FakeProfile:
        profile_id = "adrd"
        ingest_only = False
        # duck-typed as IndicationProfile for hasattr check
        pass

    class FakeRegistry:
        def get(self, profile_id):
            assert profile_id == "adrd"
            return FakeProfile()

    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr("src.ingest.extract_studies.run_ingestion", fake_run_ingestion)

    exit_code = main(["ingest"])

    assert exit_code == 0
    # config kwarg is now the IndicationProfile object (not None)
    assert captured.get("config") is not None


def test_cli_main_ingest_full_catalog_alias_resolves_to_full_catalog(monkeypatch):
    """--profile full-catalog (legacy alias) resolves to full_catalog profile."""
    resolved_ids = []

    class FakeManifest:
        status = "success"

    class FakeProfile:
        profile_id = "full_catalog"
        ingest_only = True
        pass

    class FakeRegistry:
        def get(self, profile_id):
            resolved_ids.append(profile_id)
            return FakeProfile()

    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        "src.ingest.extract_studies.run_ingestion",
        lambda **kw: FakeManifest(),
    )

    exit_code = main(["ingest", "--profile", "full-catalog"])

    assert exit_code == 0
    assert resolved_ids == ["full_catalog"]


def test_cli_main_rejects_condition_with_full_catalog_profile(monkeypatch):
    entered = []

    class NoNetworkClient:
        def __init__(self, api):
            pass

        def __enter__(self):
            entered.append(True)
            raise RuntimeError("network guard: client should never be entered")

        def __exit__(self, *args):
            return False

    class FakeProfile:
        profile_id = "full_catalog"
        ingest_only = True
        pass

    class FakeRegistry:
        def get(self, profile_id):
            return FakeProfile()

    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr("src.ingest.extract_studies.CTGClient", NoNetworkClient)

    exit_code = main(["ingest", "--profile", "full-catalog", "--condition", "Cancer"])

    assert exit_code == 1
    assert not entered


def test_cli_parser_orchestrate():
    parser = build_parser()
    args = parser.parse_args(["orchestrate"])
    assert args.command == "orchestrate"
    assert args.full_refresh is False
    assert args.max_pages is None


def test_cli_parser_orchestrate_full_refresh():
    parser = build_parser()
    args = parser.parse_args(["orchestrate", "--full-refresh", "--max-pages", "2"])
    assert args.full_refresh is True
    assert args.max_pages == 2


def test_cli_main_orchestrate_skips_ingest_only_profiles(monkeypatch):
    """orchestrate runs the *refreshable* profiles only. full_catalog is a
    ~600-page registry pull and must never be reachable from `make pipeline`."""
    ingested: list[str] = []
    transformed: list[str] = []

    class FakeManifest:
        status = "success"
        error = None

    class FakeProfileA:
        profile_id = "adrd"
        ingest_only = False

    class FakeProfileB:
        profile_id = "oncology_nsclc"
        ingest_only = False

    class FakeRegistry:
        def refreshable(self):
            return [FakeProfileA(), FakeProfileB()]

        def all(self):  # pragma: no cover - orchestrate must not call this
            raise AssertionError("orchestrate must use refreshable(), not all()")

    def fake_ingest(**kwargs):
        ingested.append(kwargs["config"].profile_id)
        return FakeManifest()

    def fake_transform(**kwargs):
        transformed.append(kwargs["profile"].profile_id)
        return []

    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr("src.ingest.extract_studies.run_ingestion", fake_ingest)
    monkeypatch.setattr("src.transform.build_silver_entities.run_transform", fake_transform)
    monkeypatch.setattr("src.quality.profiling.profile_run", lambda *a, **k: None)

    assert main(["orchestrate"]) == 0
    assert ingested == ["adrd", "oncology_nsclc"]
    assert transformed == ["adrd", "oncology_nsclc"]


def test_cli_main_orchestrate_refuses_ingest_only_profile(monkeypatch):
    """`orchestrate --profile full_catalog` is exit 2 before anything runs.

    The test above pins the default branch — `orchestrate` without --profile
    takes `refreshable()`, so full_catalog is not in it. The single-profile
    branch went straight to `registry.get()`, and the alias normalises
    `full-catalog` straight in, so the ~600-page registry pull was reachable
    from the command that also transforms: the branch review measured exit 0
    with `('transform', 'full_catalog', True)` recorded, i.e. ADRD-classified
    rows stamped `indication_profile_id = 'full_catalog'` written into the
    *shared* silver tree that dbt globs. `transform` already refuses this
    profile with exit 2; `orchestrate` has to hold the same line *before* the
    loop so no ingestion is attempted either.
    """
    calls: list[str] = []

    class FakeManifest:
        status = "success"
        error = None

    class FullCatalogProfile:
        profile_id = "full_catalog"
        ingest_only = True

    class RefreshableProfile:
        profile_id = "adrd"
        ingest_only = False

    class FakeRegistry:
        def refreshable(self):
            calls.append("refreshable")
            return [RefreshableProfile()]

        def get(self, profile_id):
            calls.append(f"get:{profile_id}")
            return FullCatalogProfile()

    def fake_ingest(**kwargs):
        calls.append("ingest")
        return FakeManifest()

    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr("src.ingest.extract_studies.run_ingestion", fake_ingest)
    monkeypatch.setattr(
        "src.transform.build_silver_entities.run_transform",
        lambda **kw: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    monkeypatch.setattr(
        "src.quality.profiling.profile_run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    assert main(["orchestrate", "--profile", "full_catalog"]) == 2
    # the legacy hyphen alias is what the reviewer's probe used
    assert main(["orchestrate", "--profile", "full-catalog"]) == 2
    assert "ingest" not in calls
    assert calls == ["get:full_catalog", "get:full_catalog"]


def test_cli_main_orchestrate_unknown_profile_is_usage_error(monkeypatch):
    """A typo'd `orchestrate --profile` id exits 2 (usage), never a traceback.

    Same convention `transform` keeps at 1449a5c: 2 = usage, 1 = data failure.
    `orchestrate` built its one-profile list outside any try, so the registry's
    KeyError escaped as an uncaught exception.
    """
    calls: list[str] = []

    class FakeRegistry:
        def get(self, profile_id):
            raise KeyError(f"No profile '{profile_id}' found in config/profiles/")

        def refreshable(self):  # pragma: no cover - --profile must not widen
            calls.append("refreshable")
            return []

    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        "src.ingest.extract_studies.run_ingestion",
        lambda **kw: calls.append("ingest"),
    )
    monkeypatch.setattr("src.transform.build_silver_entities.run_transform", lambda **kw: [])

    assert main(["orchestrate", "--profile", "no-such-profile"]) == 2
    assert calls == []


def test_cli_main_orchestrate_fails_when_registry_has_no_refreshable_profiles(monkeypatch):
    """An empty refreshable() set is a mis-mounted config/profiles/ or every
    profile marked ingest_only — not a refresh that landed data. `make pipeline`
    gates on this exit code from Task 5 onward, so it must never be 0."""
    ingested: list[str] = []

    class FakeManifest:
        status = "success"
        error = None

    class EmptyRegistry:
        def refreshable(self):
            return []

    def fake_ingest(**kwargs):
        ingested.append(kwargs["config"].profile_id)
        return FakeManifest()

    monkeypatch.setattr("src.cli.get_registry", lambda: EmptyRegistry())
    monkeypatch.setattr("src.ingest.extract_studies.run_ingestion", fake_ingest)
    monkeypatch.setattr("src.transform.build_silver_entities.run_transform", lambda **kw: [])
    monkeypatch.setattr("src.quality.profiling.profile_run", lambda *a, **k: None)

    assert main(["orchestrate"]) != 0
    assert ingested == []


def test_cli_main_orchestrate_profiles_each_run_with_that_profiles_config(monkeypatch):
    """Each run is profiled against the config of the profile that made it.

    `transform` calls `profile_run(run_id, config=indication_profile.config)`;
    `orchestrate` called it with the bare run id, so profiling used the
    *default* profile's paths and looked the run up in
    `data/bronze/adrd/manifests/`. For `oncology_nsclc` the manifest is not
    there, so `silver/_profiles/profile_<run>.json` recorded a reconciliation
    block it had no business calling clean. The assertion is on identity, not
    equality: a profile that happened to carry an equal-but-default config
    would pass the weaker test while profiling the wrong tree.
    """
    profiled: list[dict] = []

    class FakeManifest:
        status = "success"
        error = None

    class AdrdProfile:
        profile_id = "adrd"
        ingest_only = False
        config = SimpleNamespace(profile_id="adrd")

    class NsclcProfile:
        profile_id = "oncology_nsclc"
        ingest_only = False
        config = SimpleNamespace(profile_id="oncology_nsclc")

    class FakeRegistry:
        def refreshable(self):
            return [AdrdProfile(), NsclcProfile()]

    def fake_ingest(**kwargs):
        return FakeManifest()

    def fake_transform(**kwargs):
        pid = kwargs["profile"].profile_id
        return [f"run_{pid}_a", f"run_{pid}_b"]

    def fake_profile_run(*args, **kwargs):
        profiled.append({"run_id": args[0], **kwargs})
        return {}

    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr("src.ingest.extract_studies.run_ingestion", fake_ingest)
    monkeypatch.setattr("src.transform.build_silver_entities.run_transform", fake_transform)
    monkeypatch.setattr("src.quality.profiling.profile_run", fake_profile_run)

    assert main(["orchestrate"]) == 0
    assert [p["run_id"] for p in profiled] == [
        "run_adrd_a",
        "run_adrd_b",
        "run_oncology_nsclc_a",
        "run_oncology_nsclc_b",
    ]
    assert [p["config"].profile_id for p in profiled] == [
        "adrd",
        "adrd",
        "oncology_nsclc",
        "oncology_nsclc",
    ]
    assert profiled[0]["config"] is AdrdProfile.config
    assert profiled[-1]["config"] is NsclcProfile.config


def test_cli_has_no_local_profile_alias_table() -> None:
    """The alias map moved to src.profiles so ingest/transform/dashboard agree."""
    import src.cli

    assert not hasattr(src.cli, "_PROFILE_ALIASES")


def test_cli_parser_transform():
    parser = build_parser()
    args = parser.parse_args(["transform", "--run-id", "2026-09-04", "--force"])
    assert args.command == "transform"
    assert args.run_id == "2026-09-04"
    assert args.force is True


def test_cli_parser_transform_defaults_to_legacy_name():
    parser = build_parser()
    assert parser.parse_args(["transform"]).profile == "default"


def test_cli_parser_transform_accepts_any_profile_id():
    """No `choices=`: adding config/profiles/<x>.yml must be enough to transform it."""
    parser = build_parser()
    assert parser.parse_args(["transform", "--profile", "oncology_nsclc"]).profile == (
        "oncology_nsclc"
    )


def test_cli_main_transform_resolves_profile_from_registry(monkeypatch):
    calls: list[dict] = []

    def fake_run_transform(**kwargs):
        calls.append({"fn": "transform", **kwargs})
        return ["r1"]

    def fake_profile_run(run_id, **kwargs):
        calls.append({"fn": "profile", "run_id": run_id, **kwargs})
        return {}

    monkeypatch.setattr("src.transform.build_silver_entities.run_transform", fake_run_transform)
    monkeypatch.setattr("src.quality.profiling.profile_run", fake_profile_run)

    assert main(["transform", "--profile", "oncology_nsclc"]) == 0
    profile = calls[0]["profile"]
    assert profile.profile_id == "oncology_nsclc"
    assert str(profile.config.paths.bronze_manifests).endswith(
        "data/bronze/oncology_nsclc/manifests"
    )
    assert calls[1]["config"] is profile.config


def test_cli_main_transform_default_resolves_to_adrd(monkeypatch):
    calls: list[dict] = []

    def fake_run_transform(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("src.transform.build_silver_entities.run_transform", fake_run_transform)
    monkeypatch.setattr("src.quality.profiling.profile_run", lambda *a, **k: {})

    assert main(["transform"]) == 0
    assert calls[0]["profile"].profile_id == "adrd"


def test_cli_main_transform_refuses_ingest_only_profile(monkeypatch, caplog):
    monkeypatch.setattr(
        "src.transform.build_silver_entities.run_transform",
        lambda **k: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    # Exit 2 = usage error: full_catalog has no taxonomy, so there is nothing
    # to transform and inventing one would produce a misleading warehouse.
    assert main(["transform", "--profile", "full_catalog"]) == 2


def test_cli_main_transform_unknown_profile_is_usage_error_not_data_failure(monkeypatch):
    """A typo'd profile id exits 2 (usage error), never 1 (data failure).

    `choices=` was removed in favour of registry resolution, so an unknown id
    used to escape as an uncaught KeyError: exit 1, the same code a real
    transform failure returns. From Task 5 `make pipeline` gates on these
    codes, so they must stay distinct.
    """
    calls: list[dict] = []

    def failing_run_transform(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("bronze unreadable")

    monkeypatch.setattr("src.transform.build_silver_entities.run_transform", failing_run_transform)
    monkeypatch.setattr("src.quality.profiling.profile_run", lambda *a, **k: {})

    assert main(["transform", "--profile", "no-such-profile"]) == 2
    assert calls == []
    # a genuine transform failure on a real profile stays exit 1
    assert main(["transform", "--profile", "adrd"]) == 1
    assert [c["profile"].profile_id for c in calls] == ["adrd"]


def test_cli_main_transform_refuses_legacy_full_catalog_alias(monkeypatch):
    """The --profile help text promises 'full-catalog' is still accepted: it
    normalizes to full_catalog, which is ingest_only and refused the same way."""
    monkeypatch.setattr(
        "src.transform.build_silver_entities.run_transform",
        lambda **k: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    assert main(["transform", "--profile", "full-catalog"]) == 2


def test_legacy_full_catalog_config_file_is_gone() -> None:
    from src.utils.paths import project_root

    assert not (project_root() / "config" / "full_catalog_config.yml").exists()


def test_cli_parser_quality_report():
    parser = build_parser()
    args = parser.parse_args(["quality-report", "--update-schema-baseline"])
    assert args.command == "quality-report"
    assert args.update_schema_baseline is True


def test_cli_quality_report_updates_a_baseline_per_profile_tree(project_root_tmp, monkeypatch):
    """`--update-schema-baseline` loops the registry so each profile's
    baseline is re-frozen against *that profile's* latest run — the loop's
    own comment at src/cli.py:208-210 says updating only the default tree
    "would freeze every other profile's baseline against a run it never
    made", and reviewer F5 measured that no test reached the loop at all
    (task-13-review-quality.md §2D: the only prior coverage asserted the
    parser sets the flag).

    The attribution has to survive build_report's own drift loop, which
    *creates* a missing baseline as a side effect — a first draft of this
    test that only asserted baseline existence stayed green under a
    loop-truncation mutant (measured 2026-09-06). So each tree here starts
    with a stale baseline frozen on an older run, plus a newer run whose
    schema drifted from it; only `update_baseline=True` rewrites a baseline
    that already exists, and each rewritten baseline must name its own
    profile's newer run."""
    import json

    from src.ingest.snapshot_manifest import write_manifest
    from src.profiles import get_registry
    from src.quality.schema_drift import BASELINE_FILENAME, collect_field_paths
    from tests.test_build_silver import make_manifest, make_study, write_bronze_page

    stale_paths = sorted(collect_field_paths(make_study("NCT1")))
    drifted_study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT1"},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "X"}},
        }
    }

    runs: dict[str, tuple[str, str]] = {}
    profiles = get_registry().refreshable()
    assert len(profiles) >= 2, "a one-profile loop cannot test a per-profile flag"
    for index, indication_profile in enumerate(profiles):
        profile_cfg = indication_profile.config
        stale_run = f"20260904T120000Z_o000000{index}"
        new_run = f"20260905T120000Z_n000000{index}"
        runs[indication_profile.profile_id] = (stale_run, new_run)
        write_bronze_page(
            profile_cfg.paths.bronze_api_responses / f"run_id={stale_run}", 1, [make_study("NCT1")]
        )
        write_bronze_page(
            profile_cfg.paths.bronze_api_responses / f"run_id={new_run}", 1, [drifted_study]
        )
        profile_cfg.paths.bronze_manifests.mkdir(parents=True, exist_ok=True)
        write_manifest(profile_cfg.paths.bronze_manifests, make_manifest(stale_run, record_count=1))
        write_manifest(profile_cfg.paths.bronze_manifests, make_manifest(new_run, record_count=1))
        (profile_cfg.paths.bronze_api_responses.parent / BASELINE_FILENAME).write_text(
            json.dumps(
                {
                    "created_at_utc": "2026-09-04T12:00:00Z",
                    "source_run_id": stale_run,
                    "paths": stale_paths,
                }
            ),
            encoding="utf-8",
        )

    # main() reaches build_report() with its default output; keep that write
    # inside the temp root instead of the checkout's reports/ directory.
    monkeypatch.setattr(
        "src.quality.data_quality_report.REPORT_PATH",
        project_root_tmp / "reports" / "data_quality_report.md",
    )
    # This tree has bronze runs and no silver or warehouse, so the real
    # reconciliation fails 6 checks — and `quality-report` is now a gate that
    # exits 1 on exactly that. The subject here is the baseline loop; the gate
    # is covered by test_quality_report_exits_non_zero_when_a_check_fails.
    monkeypatch.setattr("src.quality.data_quality_report.run_reconciliation", lambda: [])
    assert main(["quality-report", "--update-schema-baseline"]) == 0

    for pid, (stale_run, new_run) in runs.items():
        baseline = (
            get_registry().get(pid).config.paths.bronze_api_responses.parent / BASELINE_FILENAME
        )
        assert baseline.exists(), f"no schema baseline in {pid}'s tree"
        payload = json.loads(baseline.read_text(encoding="utf-8"))
        assert payload["source_run_id"] == new_run, (
            f"{pid}'s baseline still freezes the stale run {stale_run}: the "
            "--update-schema-baseline loop never reached this profile's tree"
        )


def _quality_report_exit(project_root_tmp, monkeypatch, checks) -> tuple[int, str]:
    """Run `quality-report` against the given reconciliation results."""
    monkeypatch.setattr("src.quality.data_quality_report.run_reconciliation", lambda: checks)
    report_path = project_root_tmp / "reports" / "data_quality_report.md"
    # main() calls build_report() with its default output path.
    monkeypatch.setattr("src.quality.data_quality_report.REPORT_PATH", report_path)
    code = main(["quality-report"])
    return code, report_path.read_text(encoding="utf-8")


def _check(passed: bool) -> ReconciliationCheck:
    """One `warehouse_profile_has_trials` result — the check whose whole job is
    noticing that a profile produced nothing."""
    return ReconciliationCheck(
        profile_id="oncology_nsclc",
        check="warehouse_profile_has_trials",
        run_id="r1",
        expected=10,
        actual=10 if passed else 0,
        passed=passed,
        note="ok" if passed else "profile vanished from the warehouse",
    )


def test_quality_report_exits_non_zero_when_a_check_fails(project_root_tmp, monkeypatch):
    """`make pipeline`'s last step must be a gate, not a print.

    The pipeline is `orchestrate prune-data dbt-run dbt-test quality-report` and
    nothing arms the Dagster asset checks, so this report is the only thing on
    the deployed path that looks at cross-layer reconciliation at all. It already
    counted the failures — `build_report` computed `failed` to write one Markdown
    line — and then threw the number away: a whole profile vanishing from the
    warehouse printed "0/1 checks passed" inside a green container, and the
    weekly marker advanced as if the refresh had landed.
    """
    code, text = _quality_report_exit(project_root_tmp, monkeypatch, [_check(passed=False)])

    assert code != 0, "a failed reconciliation check cannot be exit 0"
    assert "0/1 reconciliation checks passed" in text, (
        "the exit code and the artifact have to come from the same count"
    )


def test_quality_report_exits_zero_when_every_check_passes(project_root_tmp, monkeypatch):
    """The other direction: a clean report must stay green, or the gate is a wall."""
    code, text = _quality_report_exit(
        project_root_tmp, monkeypatch, [_check(passed=True), _check(passed=True)]
    )

    assert code == 0
    assert "2/2 reconciliation checks passed" in text


def test_cli_parser_transform_profile_accepts_indication():
    parser = build_parser()
    args = parser.parse_args(["transform", "--profile", "oncology_nsclc"])
    assert args.profile == "oncology_nsclc"


def test_cli_parser_orchestrate_with_profile():
    parser = build_parser()
    args = parser.parse_args(["orchestrate", "--profile", "oncology_nsclc"])
    assert args.profile == "oncology_nsclc"


def test_cli_main_orchestrate_with_profile_runs_only_named_profile(monkeypatch):
    ingested = []
    transformed = []

    class FakeManifest:
        status = "success"
        error = None

    class FakeProfileA:
        profile_id = "adrd"
        ingest_only = False
        config = SimpleNamespace(profile_id="adrd")

    class FakeProfileB:
        profile_id = "oncology_nsclc"
        ingest_only = False
        config = SimpleNamespace(profile_id="oncology_nsclc")

    class FakeRegistry:
        def get(self, profile_id):
            if profile_id == "oncology_nsclc":
                return FakeProfileB()
            return FakeProfileA()

        def active(self):
            return [FakeProfileA(), FakeProfileB()]

    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        "src.ingest.extract_studies.run_ingestion",
        lambda **kw: (ingested.append(kw["config"].profile_id), FakeManifest())[1],
    )
    monkeypatch.setattr(
        "src.transform.build_silver_entities.run_transform",
        lambda **kw: (transformed.append(kw["profile"].profile_id), ["r1"])[1],
    )
    monkeypatch.setattr("src.quality.profiling.profile_run", lambda *a, **k: {})

    exit_code = main(["orchestrate", "--profile", "oncology_nsclc"])

    assert exit_code == 0
    assert ingested == ["oncology_nsclc"]
    assert transformed == ["oncology_nsclc"]


def test_cli_main_transform_custom_profile_passes_profile(monkeypatch):
    transform_calls = []
    profile_calls = []

    class FakeProfile:
        profile_id = "oncology_nsclc"
        ingest_only = False
        config = object()

    class FakeRegistry:
        def get(self, profile_id):
            assert profile_id == "oncology_nsclc"
            return FakeProfile()

    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        "src.transform.build_silver_entities.run_transform",
        lambda **kwargs: (transform_calls.append(kwargs), ["r1"])[1],
    )
    monkeypatch.setattr(
        "src.quality.profiling.profile_run",
        lambda run_id, **kwargs: (profile_calls.append({"run_id": run_id, **kwargs}), {})[1],
    )

    exit_code = main(["transform", "--profile", "oncology_nsclc"])

    assert exit_code == 0
    assert len(transform_calls) == 1
    assert transform_calls[0]["profile"].profile_id == "oncology_nsclc"
    assert profile_calls[0]["config"] is FakeProfile.config


def test_cli_main_init_data_dirs_creates_every_profile_tree(tmp_path, monkeypatch):
    """A fresh volume must end up with every profile's dirs, not just adrd's.

    CTI_PROJECT_ROOT is redirected so resolve_path() puts the created tree under
    tmp_path; without that the test would write into the real repo's data/.
    """
    from src.config import load_config
    from src.utils.paths import project_root, resolve_path

    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    load_config.cache_clear() if hasattr(load_config, "cache_clear") else None

    class FakePaths:
        def __init__(self, pid: str) -> None:
            root = project_root() / "data" / "bronze" / pid
            self.bronze_api_responses = root / "api_responses"
            self.bronze_manifests = root / "manifests"
            self.quarantine = root / "quarantine"
            self.silver = resolve_path("data/silver")
            self.gold = resolve_path("data/gold")
            self.duckdb = resolve_path("data/warehouse/clinical_trials.duckdb")

    class FakeProfile:
        def __init__(self, pid: str) -> None:
            self.profile_id = pid
            self.config = SimpleNamespace(paths=FakePaths(pid))

    class FakeRegistry:
        def all(self):
            return [FakeProfile("adrd"), FakeProfile("oncology_nsclc")]

    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())

    assert main(["init-data-dirs"]) == 0

    for pid in ("adrd", "oncology_nsclc"):
        assert (tmp_path / f"data/bronze/{pid}/api_responses").is_dir()
        assert (tmp_path / f"data/bronze/{pid}/manifests").is_dir()
        assert (tmp_path / f"data/bronze/{pid}/quarantine").is_dir()
    assert (tmp_path / "data/silver").is_dir()
    assert (tmp_path / "data/gold").is_dir()
    # DuckDB does not create parent directories for a new database file.
    assert (tmp_path / "data/warehouse").is_dir()


def test_cli_main_init_data_dirs_fails_on_empty_registry(tmp_path, monkeypatch):
    """A mis-mounted config/profiles/ must not boot as success.

    entrypoint.sh guards the boot with `init-data-dirs … || exit 1`, so exiting 0
    on an empty tree means the container comes up with no data directories and
    DuckDB fails later on a missing parent — the symptom instead of the cause.
    """
    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))

    class FakeRegistry:
        def all(self):
            return []

    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())

    assert main(["init-data-dirs"]) != 0
    assert not list((tmp_path / "data").glob("bronze/**"))
