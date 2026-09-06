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
    apart again.

    Since the two-profile fixture the literal is a glob whose one wildcard
    segment stands for the profile id, so the guard checks every profile the
    registry knows about, not just the default one. Comparison is segment-wise
    because DuckDB's `*` does not cross a `/`.
    """
    import fnmatch
    import re

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
    match = re.search(r"read_parquet\(\s*'([^']+)'", location)
    assert match, location
    pattern_parts = match.group(1).split("/")

    def matches(manifests_dir: Path) -> bool:
        parts = manifests_dir.relative_to(project_root()).as_posix().split("/") + [
            "summary_*.parquet"
        ]
        return len(parts) == len(pattern_parts) and all(
            fnmatch.fnmatchcase(actual, expected) if "*" in expected else actual == expected
            for actual, expected in zip(parts, pattern_parts, strict=True)
        )

    assert matches(load_config().paths.bronze_manifests)
    for profile in ProfileRegistry().all():
        assert matches(profile.config.paths.bronze_manifests), profile.profile_id


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

# The exact, ordered prerequisite list `make pipeline` is allowed to have. The
# plan mandates it verbatim: nothing dropped, nothing inserted.
PIPELINE_PREREQS = ["orchestrate", "prune-data", "dbt-run", "dbt-test", "quality-report"]

# The commands `make -n pipeline` must print, in the order make walks them
# (`dbt-run` pulls in `dbt-seed`). Matched on the subcommand rather than the
# whole line so `DBT_FLAGS` can change without this guard needing an edit.
PIPELINE_RECIPES = [
    "src.cli orchestrate",
    "src.cli prune-data",
    "dbt seed",
    "dbt run",
    "dbt test",
    "src.cli quality-report",
]


def _makefile_text() -> str:
    from src.utils.paths import project_root

    return (project_root() / "Makefile").read_text(encoding="utf-8")


def _pipeline_prereq_lists() -> list[list[str]]:
    """Prerequisites of every top-level ``pipeline:`` line, one list per line.

    Two details matter here:

    * ``[ \\t]*`` rather than ``\\s*`` — a ``pipeline:`` with no prerequisites and
      a recipe on the next line must not capture its own recipe as a prerequisite.
    * the ``## …`` help text lives on the same line and is a comment to make, so
      it is dropped before splitting. Left in, rewording the description to
      contain the word ``ingest`` would fail a *correct* Makefile (and ``make
      help`` needs that comment, so it is not going away).
    """
    import re

    return [
        m.group(1).split("##")[0].split()
        for m in re.finditer(r"^pipeline:[ \t]*(.*)$", _makefile_text(), re.MULTILINE)
    ]


def _target_recipe(lines: list[str], target: str) -> list[str] | None:
    """The recipe lines of a ``target:`` rule.

    ``None`` when the rule is absent, ``[]`` when it is declared but owns no
    recipe — which is exactly how ``make pipeline`` can refresh nothing while
    every prerequisite name is still spelled correctly.
    """
    head = target + ":"
    start = None
    for i, ln in enumerate(lines):
        declared = ln.split("##")[0].rstrip()
        if declared == head or declared.startswith(head + " "):
            start = i
            break
    if start is None:
        return None
    recipe: list[str] = []
    for ln in lines[start + 1 :]:
        if not ln.startswith("\t"):
            break
        recipe.append(ln[1:].strip())
    return recipe


def _lines_make_runs_anyway(makefile: str) -> list[str]:
    """Recipe lines make executes *even under* ``-n``, so a dry run is not inert.

    GNU make runs ``+``-prefixed lines and any line containing ``$(MAKE)``
    whatever ``-n`` says (verified on 3.81). An empty result means the
    ``make -n`` below cannot touch the working tree — hard requirement, since
    ``pipeline``'s real recipes include a non-dry ``prune-data``.
    """
    forced = [
        ln.strip()
        for ln in makefile.splitlines()
        if ln.startswith("\t") and ln[1:].lstrip().startswith("+")
    ]
    if "$(MAKE)" in makefile:
        forced.append("a line containing $(MAKE)")
    return forced


def test_make_pipeline_orchestrates_and_prunes() -> None:
    """`make pipeline` is what the container runs (entrypoint.sh:51). If it still
    names `ingest transform` directly it is single-profile by construction, and
    the second profile never refreshes — silently, with exit 0.

    Asserted on the target's prerequisites so a rename cannot dodge the guard —
    and on the *whole* ordered list, not on a couple of names plus a relative
    order. Checking only that `orchestrate` and `prune-data` are present and that
    `orchestrate < prune-data < dbt-run` let `pipeline: orchestrate prune-data
    dbt-run` pass green while quietly dropping `dbt-test` and `quality-report`
    off the deployed refresh.
    """
    defs = _pipeline_prereq_lists()
    # make *accumulates* prerequisites across repeated definitions, so a second
    # `pipeline: ingest transform` anywhere below would re-add the single-profile
    # step — and a first-match regex (`re.search`) cannot see it. Verified: with
    # such a line appended, `make -n pipeline` prints
    # `src.cli ingest --condition "Alzheimer Disease"` after the good chain.
    assert len(defs) == 1, f"`pipeline:` must be defined exactly once, found {len(defs)}: {defs}"
    prereqs = defs[0]
    assert prereqs == PIPELINE_PREREQS, prereqs
    # Kept as explicit diagnostics: the shape this task exists to remove.
    assert "ingest" not in prereqs, prereqs
    assert "transform" not in prereqs, prereqs


def test_makefile_serializes_pipeline_prerequisites() -> None:
    """The chain is only correct if its prerequisites run *in that order*.

    `pipeline`'s prerequisites include a destructive step: `prune-data`, whose
    recipe is a real prune with no `--dry-run`. make builds the prerequisites of
    a single target **concurrently** under `-j`, and `MAKEFLAGS` can carry `-j` in
    from a developer shell or the container image without anyone typing it — so
    `make -j8 pipeline` can prune bronze/silver that `orchestrate` is still
    writing. `.NOTPARALLEL:` forces prerequisites to be built one at a time, in
    the listed order.

    Honest scope: what this guards is make's job *scheduling*, which is not
    observable in a dry run. `make -n -j8 pipeline` prints prerequisites in
    listed order every time whether or not `.NOTPARALLEL:` is present (verified
    on this host's GNU Make 3.81: 5/5 in order with `-j8` once `.NOTPARALLEL:`
    was added, 1 of 5 runs inverted without it). So this is an assertion about
    the declaration; `test_make_pipeline_recipes_are_reachable_and_ordered`
    covers the recipe layer serially.
    """
    import re

    assert re.search(r"^\.NOTPARALLEL[ \t]*:", _makefile_text(), re.MULTILINE), (
        "Makefile has no `.NOTPARALLEL:` declaration, so `make -j pipeline` builds "
        "`pipeline`'s prerequisites concurrently and `prune-data` can delete a tree "
        "`orchestrate` is still writing"
    )


def test_make_pipeline_recipes_are_reachable_and_ordered() -> None:
    """Text-only assertions cannot see the recipe layer, and recipes are what runs.

    Stub or empty `orchestrate`'s recipe and every prerequisite-name assertion
    above still stays green while `make pipeline` refreshes nothing. `make -n`
    prints the *recipes*, so it can see this: the six steps must all be reachable
    from `pipeline`, in order, and the single-profile `ingest` chain must not be.
    A `-` prefix is the one exception — make strips `@`, `-` and `+` before
    echoing, so `test_pipeline_step_recipes_are_not_error_ignored` covers it.

    Strictly `-n`: nothing here executes. That is only true while the Makefile is
    free of `+`-prefixed recipe lines and `$(MAKE)` lines — GNU make executes those
    even under `-n` (verified on 3.81) — so the pre-flight below is load-bearing,
    not tidiness. The real `pipeline` target is never invoked.
    """
    import os
    import shutil
    import subprocess

    from src.utils.paths import project_root

    if shutil.which("make") is None:
        pytest.skip("`make` is not installed on this host")

    makefile = _makefile_text()
    # Pre-flight: prove the dry-run below really is inert.
    not_inert = _lines_make_runs_anyway(makefile)
    assert not not_inert, f"make runs these even under -n, so this guard is not dry: {not_inert}"

    # A MAKEFLAGS inherited from the caller could carry -j; serial is what the
    # dry-run ordering claim rests on.
    env = {k: v for k, v in os.environ.items() if k != "MAKEFLAGS"}
    proc = subprocess.run(
        ["make", "-n", "pipeline"],
        cwd=project_root(),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    steps = (next((s for s in PIPELINE_RECIPES if s in ln), None) for ln in lines)
    found = [s for s in steps if s]
    assert found == PIPELINE_RECIPES, proc.stdout
    assert "src.cli ingest" not in proc.stdout, proc.stdout
    # The deployed prune must stay a real prune: `--dry-run` here means retention
    # silently switched off with exit 0.
    assert "dry-run" not in proc.stdout, proc.stdout


def test_pipeline_step_recipes_are_not_error_ignored() -> None:
    """The one recipe edit `make -n` cannot reveal: a leading `-`.

    make strips `@`, `-` and `+` before echoing a command, so a dry run prints
    `uv run python -m src.cli orchestrate` identically whether or not the recipe
    says `-$(PYTHON) -m src.cli orchestrate`. The `-` matters: it tells make to
    swallow that step's exit status, so `pipeline` reaches the prune/dbt stages
    (and, on the deployed path, exits 0) even when the refresh failed.

    So this one is asserted on the text: every target `pipeline` reaches must own
    at least one recipe line, and none of those lines may carry an error-ignore
    prefix.
    """
    lines = _makefile_text().splitlines()
    # `dbt-run` pulls in `dbt-seed`; both are on the deployed path.
    for step in [*PIPELINE_PREREQS, "dbt-seed"]:
        recipe = _target_recipe(lines, step)
        assert recipe is not None, f"no `{step}:` target in the Makefile"
        assert recipe, f"`{step}` is declared with an empty recipe"
        ignored = [ln for ln in recipe if ln.startswith("-")]
        assert not ignored, f"`{step}` ignores its own failures (make's `-` prefix): {ignored}"
