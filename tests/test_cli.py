from types import SimpleNamespace

import pytest

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
    """--profile default is still the default and maps to full_catalog (the
    registry-wide profile) at runtime."""
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


def test_cli_parser_max_pages_accepts_positive():
    args = build_parser().parse_args(["ingest", "--max-pages", "3"])
    assert args.max_pages == 3


def test_cli_parser_max_pages_refuses_zero():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["ingest", "--max-pages", "0"])
    assert exc.value.code == 2


def test_cli_parser_max_pages_refuses_negative():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["ingest", "--max-pages", "-1"])
    assert exc.value.code == 2


def test_cli_main_ingest_resolves_profile_via_registry(monkeypatch):
    """main() resolves --profile to an IndicationProfile via ProfileRegistry.

    The default is the registry-wide profile since issue #53."""
    captured = {}

    class FakeManifest:
        status = "success"

    def fake_run_ingestion(**kwargs):
        captured.update(kwargs)
        return FakeManifest()

    class FakeProfile:
        profile_id = "full_catalog"
        ingest_only = False
        # duck-typed as IndicationProfile for hasattr check
        pass

    class FakeRegistry:
        def get(self, profile_id):
            assert profile_id == "full_catalog"
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


class _Manifest:
    status = "success"
    error = None


class _Profile:
    ingest_only = False

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        self.config = SimpleNamespace(profile_id=profile_id)


class _OrchestrateRegistry:
    """Registry stand-in: one registry-wide profile plus the given presets."""

    def __init__(self, *preset_ids: str) -> None:
        self.registry_profile = _Profile("full_catalog")
        self.presets = [_Profile(pid) for pid in preset_ids]

    def registry_wide(self):
        return self.registry_profile

    def refreshable(self):
        return self.presets

    def get(self, profile_id):
        for p in [self.registry_profile, *self.presets]:
            if p.profile_id == profile_id:
                return p
        raise KeyError(f"No profile '{profile_id}' found in config/profiles/")

    def all(self):  # pragma: no cover - orchestrate must not call this
        raise AssertionError("orchestrate must use registry_wide()/refreshable(), not all()")


def _patch_orchestrate(monkeypatch, registry, *, ingest=None, derive=None, profile_run=None):
    """Wire the orchestrate collaborators; each defaults to a quiet success."""
    monkeypatch.setattr("src.cli.get_registry", lambda: registry)
    monkeypatch.setattr(
        "src.ingest.extract_studies.run_ingestion", ingest or (lambda **kw: _Manifest())
    )
    monkeypatch.setattr("src.transform.preset_runs.derive_preset_runs", derive or (lambda r, p: []))
    monkeypatch.setattr(
        "src.transform.preset_runs.registry_run_is_incremental", lambda profile, full: False
    )
    monkeypatch.setattr("src.quality.profiling.profile_run", profile_run or (lambda *a, **k: None))


def test_cli_main_orchestrate_ingests_the_registry_once_and_derives_each_preset(monkeypatch):
    """One registry-wide pull is the unit of ingest; each refreshable profile is
    a preset derived from it, so the multi-GB pull happens once, not per profile."""
    ingested: list[dict] = []
    derived: list[tuple[str, str]] = []

    def fake_ingest(**kwargs):
        ingested.append(kwargs)
        return _Manifest()

    def fake_derive(registry_profile, preset):
        derived.append((registry_profile.profile_id, preset.profile_id))
        return []

    registry = _OrchestrateRegistry("adrd", "oncology_nsclc")
    _patch_orchestrate(monkeypatch, registry, ingest=fake_ingest, derive=fake_derive)

    assert main(["orchestrate", "--full-refresh", "--max-pages", "3"]) == 0
    assert [k["config"].profile_id for k in ingested] == ["full_catalog"]
    assert ingested[0]["full_refresh"] is True
    assert ingested[0]["max_pages"] == 3
    assert ingested[0]["incremental_refresh"] is False
    assert ingested[0]["require_silver"] is False
    assert derived == [("full_catalog", "adrd"), ("full_catalog", "oncology_nsclc")]


def test_cli_main_orchestrate_single_preset_still_ingests_the_registry(monkeypatch):
    derived: list[str] = []
    ingested: list[str] = []

    def fake_ingest(**kwargs):
        ingested.append(kwargs["config"].profile_id)
        return _Manifest()

    registry = _OrchestrateRegistry("adrd", "oncology_nsclc")
    _patch_orchestrate(
        monkeypatch,
        registry,
        ingest=fake_ingest,
        derive=lambda r, p: derived.append(p.profile_id) or [],
    )

    assert main(["orchestrate", "--profile", "oncology_nsclc"]) == 0
    assert ingested == ["full_catalog"]
    assert derived == ["oncology_nsclc"]


def test_cli_main_orchestrate_refuses_the_registry_profile_as_a_preset(monkeypatch):
    """`orchestrate --profile full_catalog` is exit 2 before anything runs: the
    registry-wide profile is the run itself, not a preset to derive. The legacy
    hyphen alias takes the same path."""
    calls: list[str] = []
    registry = _OrchestrateRegistry("adrd")
    _patch_orchestrate(
        monkeypatch,
        registry,
        ingest=lambda **kw: calls.append("ingest"),
        derive=lambda r, p: calls.append("derive") or [],
    )

    assert main(["orchestrate", "--profile", "full_catalog"]) == 2
    assert main(["orchestrate", "--profile", "full-catalog"]) == 2
    assert calls == []


def test_cli_main_orchestrate_refuses_ingest_only_profile(monkeypatch):
    calls: list[str] = []
    registry = _OrchestrateRegistry("adrd")
    registry.presets[0].ingest_only = True
    _patch_orchestrate(
        monkeypatch, registry, ingest=lambda **kw: calls.append("ingest") or _Manifest()
    )

    assert main(["orchestrate", "--profile", "adrd"]) == 2
    assert calls == []


def test_cli_main_orchestrate_unknown_profile_is_usage_error(monkeypatch):
    """A typo'd `orchestrate --profile` id exits 2 (usage), never a traceback.

    Same convention `transform` keeps: 2 = usage, 1 = data failure.
    """
    calls: list[str] = []
    registry = _OrchestrateRegistry("adrd")
    _patch_orchestrate(monkeypatch, registry, ingest=lambda **kw: calls.append("ingest"))

    assert main(["orchestrate", "--profile", "no-such-profile"]) == 2
    assert calls == []


def test_cli_main_orchestrate_fails_when_registry_has_no_refreshable_profiles(monkeypatch):
    """An empty refreshable() set is a mis-mounted config/profiles/ or every
    profile marked ingest_only, not a refresh that landed data. `make pipeline`
    gates on this exit code, so it must never be 0."""
    ingested: list[str] = []
    registry = _OrchestrateRegistry()
    _patch_orchestrate(monkeypatch, registry, ingest=lambda **kw: ingested.append("x"))

    assert main(["orchestrate"]) != 0
    assert ingested == []


def test_cli_main_orchestrate_registry_ingest_failure_derives_nothing(monkeypatch):
    """A failed or raising registry pull is exit 1 and no preset derives from
    pages that are not there."""
    derived: list[str] = []

    class FailedManifest:
        status = "failed"
        error = "boom"

    registry = _OrchestrateRegistry("adrd")
    _patch_orchestrate(
        monkeypatch,
        registry,
        ingest=lambda **kw: FailedManifest(),
        derive=lambda r, p: derived.append(p.profile_id) or [],
    )
    assert main(["orchestrate"]) == 1

    def raising(**kw):
        raise RuntimeError("Connection reset by peer on page 4")

    _patch_orchestrate(
        monkeypatch,
        registry,
        ingest=raising,
        derive=lambda r, p: derived.append(p.profile_id) or [],
    )
    assert main(["orchestrate"]) == 1
    assert derived == []


def test_cli_main_orchestrate_contains_a_raise_and_attempts_the_rest(monkeypatch):
    """One preset's exception is recorded; it does not end the refresh.

    Both halves are pinned: the surviving preset ran, and the run is red.
    """
    derived: list[str] = []

    def fake_derive(registry_profile, preset):
        derived.append(preset.profile_id)
        if preset.profile_id == "adrd":
            raise RuntimeError("registry run pages hold 3 studies but its manifest records 4")
        return []

    registry = _OrchestrateRegistry("adrd", "oncology_nsclc")
    _patch_orchestrate(monkeypatch, registry, derive=fake_derive)

    assert main(["orchestrate"]) == 1
    assert derived == ["adrd", "oncology_nsclc"], "the raise ended the loop"


def test_cli_main_orchestrate_profiles_each_run_with_that_profiles_config(monkeypatch):
    """Each derived run is profiled against the config of the preset that made it.

    `profile_run` looks the run's manifest up in the config's bronze tree; the
    assertion is on identity so an equal-but-default config cannot pass.
    """
    profiled: list[dict] = []

    def fake_derive(registry_profile, preset):
        return [f"run_{preset.profile_id}_a", f"run_{preset.profile_id}_b"]

    def fake_profile_run(*args, **kwargs):
        profiled.append({"run_id": args[0], **kwargs})
        return {}

    registry = _OrchestrateRegistry("adrd", "oncology_nsclc")
    _patch_orchestrate(monkeypatch, registry, derive=fake_derive, profile_run=fake_profile_run)

    assert main(["orchestrate"]) == 0
    assert [p["run_id"] for p in profiled] == [
        "run_adrd_a",
        "run_adrd_b",
        "run_oncology_nsclc_a",
        "run_oncology_nsclc_b",
    ]
    assert profiled[0]["config"] is registry.presets[0].config
    assert profiled[-1]["config"] is registry.presets[1].config


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


def test_cli_main_transform_default_resolves_to_registry(monkeypatch):
    calls: list[dict] = []

    def fake_run_transform(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("src.transform.build_silver_entities.run_transform", fake_run_transform)
    monkeypatch.setattr("src.quality.profiling.profile_run", lambda *a, **k: {})

    assert main(["transform"]) == 0
    assert calls[0]["profile"].profile_id == "full_catalog"


def test_cli_main_transform_refuses_ingest_only_profile(monkeypatch, caplog):
    """Exit 2 = usage error: an ingest_only profile has no taxonomy, so there
    is nothing to transform and inventing one would produce a misleading
    warehouse. No shipped profile is ingest_only since issue #53, so a minimal
    duck-typed one stands in for the guard."""
    ran = []

    def must_not_run(**kwargs):
        ran.append(kwargs)
        return []

    class FakeIngestOnlyProfile:
        profile_id = "ingest_only_test"
        ingest_only = True

    class FakeRegistry:
        def get(self, profile_id):
            assert profile_id == "ingest_only_test"
            return FakeIngestOnlyProfile()

    monkeypatch.setattr("src.transform.build_silver_entities.run_transform", must_not_run)
    monkeypatch.setattr("src.cli.get_registry", lambda: FakeRegistry())

    assert main(["transform", "--profile", "ingest_only_test"]) == 2
    assert ran == []


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


def test_cli_main_transform_accepts_legacy_full_catalog_alias(monkeypatch):
    """The --profile help text promises 'full-catalog' is still accepted: it
    normalizes to full_catalog, which is transformable since issue #53."""
    calls: list[dict] = []

    def fake_run_transform(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("src.transform.build_silver_entities.run_transform", fake_run_transform)
    monkeypatch.setattr("src.quality.profiling.profile_run", lambda *a, **k: {})

    assert main(["transform", "--profile", "full-catalog"]) == 0
    assert [c["profile"].profile_id for c in calls] == ["full_catalog"]


def test_legacy_full_catalog_config_file_is_gone() -> None:
    from src.utils.paths import project_root

    assert not (project_root() / "config" / "full_catalog_config.yml").exists()


def test_cli_parser_quality_report():
    parser = build_parser()
    args = parser.parse_args(["quality-report", "--update-schema-baseline"])
    assert args.command == "quality-report"
    assert args.update_schema_baseline is True


def test_cli_quality_report_updates_the_registry_runs_baseline(project_root_tmp, monkeypatch):
    """`--update-schema-baseline` re-freezes the registry-wide profile's baseline.

    Schema drift is checked on the registry-wide run only: a preset's derived
    run has no pages of its own, so a baseline in its tree would freeze a run
    that never read the API (issue #147). The tree starts with a stale baseline
    frozen on an older run plus a newer run whose schema drifted from it: only
    `update_baseline=True` rewrites a baseline that already exists, and it must
    name the newer run. The first draft of the per-profile version of this
    test stayed green under a loop-truncation mutant when it only asserted
    existence (measured 2026-09-06), hence the stale-baseline setup.
    """
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

    registry_profile = get_registry().registry_wide()
    presets = get_registry().refreshable()
    assert presets, "the registry must hold presets for the negative assertion below"
    profile_cfg = registry_profile.config
    stale_run = "20260904T120000Z_o0000000"
    new_run = "20260905T120000Z_n0000000"
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

    baseline = profile_cfg.paths.bronze_api_responses.parent / BASELINE_FILENAME
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert payload["source_run_id"] == new_run, (
        f"the registry baseline still freezes the stale run {stale_run}: "
        "--update-schema-baseline never reached the registry-wide tree"
    )
    # Presets read the registry run's pages, so none gets a baseline of its own.
    for preset in presets:
        assert not (preset.config.paths.bronze_api_responses.parent / BASELINE_FILENAME).exists(), (
            f"{preset.profile_id} grew its own schema baseline"
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
