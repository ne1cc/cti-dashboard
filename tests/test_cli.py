from types import SimpleNamespace

from src.cli import build_parser, main


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
    monkeypatch.setattr("src.quality.profiling.profile_run", lambda run_id: None)

    assert main(["orchestrate"]) == 0
    assert ingested == ["adrd", "oncology_nsclc"]
    assert transformed == ["adrd", "oncology_nsclc"]


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
    monkeypatch.setattr("src.quality.profiling.profile_run", lambda run_id: None)

    assert main(["orchestrate"]) != 0
    assert ingested == []


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

    class FakeProfileB:
        profile_id = "oncology_nsclc"
        ingest_only = False

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
