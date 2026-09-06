"""Tests for IndicationProfile, load_profile, and ProfileRegistry."""

from pathlib import Path

import pytest
import yaml

from src.profiles import (
    IndicationProfile,
    ProfileRegistry,
    SharedPaths,
    load_profile,
    load_shared_paths,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_PROFILE_YAML = """\
profile:
  id: test_ind
  display_name: Test Indication
  ingest_only: true
  taxonomy: null
  score_weights: null

api:
  base_url: https://clinicaltrials.gov/api/v2
  query_params: {}
  http:
    timeout_seconds: 30
    max_retries: 5
    backoff_initial_seconds: 1
    backoff_max_seconds: 60
    retry_on_status: [429, 500, 502, 503, 504]

paths:
  bronze_api_responses: data/bronze/test_ind/api_responses
  bronze_manifests: data/bronze/test_ind/manifests
  silver: data/silver
  gold: data/gold
  duckdb: data/warehouse/x.duckdb
  quarantine: data/bronze/test_ind/manifests/quarantine

ingestion:
  mode_default: incremental
  reuse_window_hours: 24
  page_file_pattern: "run_id={run_id}/page={page:05d}.json"
  manifest_file_pattern: "manifest_{run_id}.json"

guardrails:
  disclaimer: test disclaimer
"""

SHARED_PATHS_YAML = """\
silver: data/silver
gold: data/gold
duckdb: data/warehouse/x.duckdb
"""


@pytest.fixture()
def tmp_profiles_dir(tmp_path: Path) -> Path:
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "test_ind.yml").write_text(MINIMAL_PROFILE_YAML)
    return profiles_dir


@pytest.fixture()
def tmp_shared_paths(tmp_path: Path) -> Path:
    p = tmp_path / "shared_paths.yml"
    p.write_text(SHARED_PATHS_YAML)
    return p


# ---------------------------------------------------------------------------
# SharedPaths
# ---------------------------------------------------------------------------


def test_load_shared_paths(tmp_shared_paths: Path) -> None:
    shared = load_shared_paths(tmp_shared_paths)
    assert isinstance(shared, SharedPaths)
    assert shared.silver.name == "silver"
    assert shared.gold.name == "gold"
    assert "x.duckdb" in str(shared.duckdb)


# ---------------------------------------------------------------------------
# load_profile
# ---------------------------------------------------------------------------


def test_load_profile_ingest_only(tmp_profiles_dir: Path, tmp_shared_paths: Path) -> None:
    from src.profiles import load_shared_paths

    shared = load_shared_paths(tmp_shared_paths)
    profile = load_profile(tmp_profiles_dir / "test_ind.yml", shared=shared)

    assert isinstance(profile, IndicationProfile)
    assert profile.profile_id == "test_ind"
    assert profile.display_name == "Test Indication"
    assert profile.ingest_only is True
    assert profile.taxonomy is None
    assert profile.score_weights_path is None


def test_load_profile_bronze_paths_are_profile_scoped(
    tmp_profiles_dir: Path, tmp_shared_paths: Path
) -> None:
    from src.profiles import load_shared_paths

    shared = load_shared_paths(tmp_shared_paths)
    profile = load_profile(tmp_profiles_dir / "test_ind.yml", shared=shared)

    assert "test_ind" in str(profile.config.paths.bronze_api_responses)
    assert "test_ind" in str(profile.config.paths.bronze_manifests)


def test_load_profile_silver_gold_are_shared(
    tmp_profiles_dir: Path, tmp_shared_paths: Path
) -> None:
    from src.profiles import load_shared_paths

    shared = load_shared_paths(tmp_shared_paths)
    profile = load_profile(tmp_profiles_dir / "test_ind.yml", shared=shared)

    # Silver and gold come from shared_paths, not from the profile YAML.
    assert profile.config.paths.silver == shared.silver
    assert profile.config.paths.gold == shared.gold
    assert profile.config.paths.duckdb == shared.duckdb


# ---------------------------------------------------------------------------
# ProfileRegistry
# ---------------------------------------------------------------------------


def test_registry_discovers_profiles(tmp_profiles_dir: Path, tmp_shared_paths: Path) -> None:
    registry = ProfileRegistry(profiles_dir=tmp_profiles_dir, shared_paths_file=tmp_shared_paths)
    profiles = registry.all()
    assert len(profiles) == 1
    assert profiles[0].profile_id == "test_ind"


def test_registry_get_returns_correct_profile(
    tmp_profiles_dir: Path, tmp_shared_paths: Path
) -> None:
    registry = ProfileRegistry(profiles_dir=tmp_profiles_dir, shared_paths_file=tmp_shared_paths)
    profile = registry.get("test_ind")
    assert profile.profile_id == "test_ind"


def test_registry_get_unknown_raises(tmp_profiles_dir: Path, tmp_shared_paths: Path) -> None:
    registry = ProfileRegistry(profiles_dir=tmp_profiles_dir, shared_paths_file=tmp_shared_paths)
    with pytest.raises(KeyError, match="No profile 'nonexistent'"):
        registry.get("nonexistent")


def test_registry_multiple_profiles(tmp_profiles_dir: Path, tmp_shared_paths: Path) -> None:
    # Add a second profile
    second = dict(yaml.safe_load(MINIMAL_PROFILE_YAML))
    second["profile"] = dict(second["profile"])
    second["profile"]["id"] = "parkinsons"
    second["profile"]["display_name"] = "Parkinson's Disease"
    (tmp_profiles_dir / "parkinsons.yml").write_text(yaml.dump(second))

    registry = ProfileRegistry(profiles_dir=tmp_profiles_dir, shared_paths_file=tmp_shared_paths)
    ids = {p.profile_id for p in registry.all()}
    assert ids == {"test_ind", "parkinsons"}


def test_registry_refreshable_excludes_ingest_only(
    tmp_profiles_dir: Path, tmp_shared_paths: Path
) -> None:
    registry = ProfileRegistry(profiles_dir=tmp_profiles_dir, shared_paths_file=tmp_shared_paths)
    # MINIMAL_PROFILE_YAML is ingest_only, so nothing survives the filter.
    assert registry.refreshable() == []
    assert [p.profile_id for p in registry.all()] == ["test_ind"]


def test_registry_active_is_gone() -> None:
    """`active()` returned ingest_only profiles, which sent `orchestrate` after a
    full-registry pull. One vocabulary: all() or refreshable()."""
    assert not hasattr(ProfileRegistry, "active")


def test_normalize_profile_id_covers_the_legacy_aliases() -> None:
    from src.profiles import normalize_profile_id

    assert normalize_profile_id("default") == "adrd"
    assert normalize_profile_id("full-catalog") == "full_catalog"
    assert normalize_profile_id("adrd") == "adrd"
    assert normalize_profile_id("oncology_nsclc") == "oncology_nsclc"


def test_real_registry_advertises_both_refreshable_profiles() -> None:
    """config/profiles/oncology_nsclc.yml exists and is ingest_only: false; a
    regression here means the deployed refresh silently stays single-profile."""
    from src.profiles import get_registry

    get_registry.cache_clear()
    ids = {p.profile_id for p in get_registry().refreshable()}
    assert {"adrd", "oncology_nsclc"} <= ids
    assert "full_catalog" not in ids
    get_registry.cache_clear()


# ---------------------------------------------------------------------------
# indication_profile_id propagation through flatten_study
# ---------------------------------------------------------------------------


def test_flatten_study_stamps_profile_id() -> None:
    from src.transform.flatten_studies import flatten_study
    from src.transform.normalize_conditions import get_taxonomy
    from src.transform.normalize_locations import get_geography_rules

    study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT99999999"},
        }
    }
    rows = flatten_study(
        study,
        "run_test",
        "2026-01-01T00:00:00Z",
        get_taxonomy(),
        get_geography_rules(),
        indication_profile_id="parkinsons",
    )
    # Every entity row should carry the profile id
    assert rows["silver_trials"][0]["indication_profile_id"] == "parkinsons"
    # base dict propagates to child entities (conditions, interventions, etc.)
    for entity_name, entity_rows in rows.items():
        for row in entity_rows:
            assert row.get("indication_profile_id") == "parkinsons", (
                f"Missing indication_profile_id on {entity_name} row"
            )


def test_flatten_study_default_profile_id() -> None:
    """Callers that don't pass indication_profile_id get 'adrd' by default."""
    from src.transform.flatten_studies import flatten_study
    from src.transform.normalize_conditions import get_taxonomy
    from src.transform.normalize_locations import get_geography_rules

    study = {"protocolSection": {"identificationModule": {"nctId": "NCT11111111"}}}
    rows = flatten_study(
        study, "run1", "2026-01-01T00:00:00Z", get_taxonomy(), get_geography_rules()
    )
    assert rows["silver_trials"][0]["indication_profile_id"] == "adrd"


# ---------------------------------------------------------------------------
# Dedup key uses (nct_id, indication_profile_id)
# ---------------------------------------------------------------------------


def test_build_silver_dedup_key_uses_profile_id(tmp_path: Path) -> None:
    """The same NCT ID in two profiles is NOT deduplicated."""
    from src.transform.flatten_studies import flatten_study
    from src.transform.normalize_conditions import get_taxonomy
    from src.transform.normalize_locations import get_geography_rules

    # Verify that calling flatten_study with two different profile IDs produces
    # two different dedup keys (property test on the key logic itself).
    study = {"protocolSection": {"identificationModule": {"nctId": "NCT00000042"}}}
    rows_adrd = flatten_study(
        study,
        "r1",
        "2026-01-01T00:00:00Z",
        get_taxonomy(),
        get_geography_rules(),
        indication_profile_id="adrd",
    )
    rows_pk = flatten_study(
        study,
        "r1",
        "2026-01-01T00:00:00Z",
        get_taxonomy(),
        get_geography_rules(),
        indication_profile_id="parkinsons",
    )
    key_adrd = (
        rows_adrd["silver_trials"][0]["nct_id"],
        rows_adrd["silver_trials"][0]["indication_profile_id"],
    )
    key_pk = (
        rows_pk["silver_trials"][0]["nct_id"],
        rows_pk["silver_trials"][0]["indication_profile_id"],
    )
    assert key_adrd != key_pk


# ---------------------------------------------------------------------------
# Default config and the adrd profile must agree on the bronze root
# ---------------------------------------------------------------------------


def test_default_config_bronze_paths_match_the_adrd_profile() -> None:
    """Guard the ingest/transform pairing that `make pipeline` depends on.

    `ingest --profile default` resolves through the registry to the adrd profile
    and writes profile-scoped bronze, while `transform` and the quality readers
    load the global config. If the two disagree, `make pipeline` ingests
    snapshots the transform never sees and exits 0 with "No runs to transform",
    so the warehouse goes stale silently.
    """
    from src.config import load_config
    from src.profiles import get_registry

    default = load_config()
    adrd = get_registry().get("adrd").config

    assert default.paths.bronze_manifests == adrd.paths.bronze_manifests
    assert default.paths.bronze_api_responses == adrd.paths.bronze_api_responses
    assert default.paths.quarantine == adrd.paths.quarantine


def test_dbt_bronze_source_reads_the_configured_manifests_dir() -> None:
    """dbt cannot import src.config, so _sources.yml repeats the manifests root
    as a literal string. This test is the seam that keeps the two from drifting
    apart again."""
    import yaml

    from src.config import load_config
    from src.utils.paths import project_root

    sources = yaml.safe_load(
        (project_root() / "dbt_clinical_trials/models/staging/_sources.yml").read_text(
            encoding="utf-8"
        )
    )
    bronze = next(s for s in sources["sources"] if s["name"] == "bronze")
    manifests = next(t for t in bronze["tables"] if t["name"] == "ingestion_manifests")
    location: str = manifests["meta"]["external_location"]

    configured = load_config().paths.bronze_manifests.relative_to(project_root()).as_posix()
    assert f"'{configured}/summary_*.parquet'" in location


def test_no_top_level_config_declares_a_private_silver_tree() -> None:
    """One silver root, from config/shared_paths.yml.

    `config/full_catalog_config.yml` used to point `silver:` at
    data/silver_full_catalog: a tree dbt never globbed and nothing pruned.
    Profile YAMLs have silver injected, so this is now structural.
    """
    import yaml

    from src.profiles import load_shared_paths
    from src.utils.paths import project_root, resolve_path

    shared = load_shared_paths()
    config_dir = project_root() / "config"
    offenders: list[str] = []
    for yml in sorted(config_dir.glob("*.yml")):
        raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        silver = (raw.get("paths") or {}).get("silver")
        if silver is not None and resolve_path(silver) != shared.silver:
            offenders.append(f"{yml.name}: {silver}")
    assert offenders == []


def test_every_bronze_tree_is_profile_scoped() -> None:
    """Stray `data/bronze/api_responses` (no profile segment) is the skew that
    made `make pipeline` ingest into one tree and transform from another."""
    from src.profiles import get_registry

    get_registry.cache_clear()
    for profile in get_registry().all():
        p = profile.config.paths
        assert f"/{profile.profile_id}/" in p.bronze_api_responses.as_posix(), p
        assert f"/{profile.profile_id}/" in p.bronze_manifests.as_posix(), p
    get_registry.cache_clear()


# ---------------------------------------------------------------------------
# The deployed entry command must be multi-profile by construction
# ---------------------------------------------------------------------------


def test_make_pipeline_orchestrates_and_prunes() -> None:
    """`make pipeline` is what the container runs (entrypoint.sh:51). If it still
    names `ingest transform` directly it is single-profile by construction, and
    the second profile never refreshes — silently, with exit 0.

    Asserted on the target's prerequisites so a rename cannot dodge the guard.
    """
    import re

    from src.utils.paths import project_root

    makefile = (project_root() / "Makefile").read_text(encoding="utf-8")
    match = re.search(r"^pipeline:\s*(.*)$", makefile, re.MULTILINE)
    assert match, "no `pipeline:` target"
    prereqs = match.group(1).split()
    assert "orchestrate" in prereqs, prereqs
    assert "prune-data" in prereqs, prereqs
    assert "ingest" not in prereqs, prereqs
    assert "transform" not in prereqs, prereqs
    assert prereqs.index("orchestrate") < prereqs.index("prune-data") < prereqs.index("dbt-run")
