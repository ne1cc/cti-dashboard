"""First-class indication profile objects.

An IndicationProfile bundles everything needed to run the full pipeline for
one therapeutic indication: API query scope, bronze-specific paths, condition
taxonomy, and score-weight references.

ProfileRegistry auto-discovers all *.yml files in config/profiles/ and
exposes them by profile_id. The shared silver/gold/duckdb paths are loaded
once from config/shared_paths.yml and injected into every profile's config.

Backward compatibility
─────────────────────
get_config() and get_taxonomy() in src/config.py and
src/transform/normalize_conditions.py continue to work unchanged.
config/project_config.yml references config/profiles/adrd.yml as its single
source of truth via `default_profile: adrd`, dynamically resolving ADRD query
parameters and scope (all 8 overall statuses) while preserving standalone
config semantics when default_profile is omitted.
ProfileRegistry supplements — it does not replace — those singletons.

Adding a new indication
───────────────────────
1. Create config/profiles/<indication_id>.yml (copy adrd.yml as a template).
2. Add a condition taxonomy YAML if needed (reference it in the profile's
   taxonomy: field).
3. Run:  python -m src.cli orchestrate --max-pages 1   (smoke test).
   Then: make orchestrate                               (full run).
No Python changes required.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.config import (
    ApiConfig,
    HttpConfig,
    IngestionConfig,
    PathsConfig,
    ProjectConfig,
    RetentionConfig,
)
from src.transform.normalize_conditions import (
    REGISTRY_TAXONOMY,
    ConditionTaxonomy,
    load_taxonomy,
)
from src.utils.paths import project_root, resolve_path

PROFILES_DIR = "config/profiles"
SHARED_PATHS_FILE = "config/shared_paths.yml"

# User-facing profile names, normalized before any registry lookup. CLI users
# historically typed `default`/`full-catalog`; config/profiles/*.yml are keyed by
# profile.id. One function owns the mapping so ingest, transform and the
# dashboard selector cannot each invent their own accepted-spellings list.
# `default` is the registry-wide snapshot since issue #53: ingesting must not
# require an indication profile, so a bare `python -m src.cli ingest` targets
# the whole registry. Disease profiles are opt-in via their own ids.
PROFILE_ALIASES: dict[str, str] = {
    "default": "full_catalog",
    "full-catalog": "full_catalog",
}


def normalize_profile_id(raw: str) -> str:
    """Return the canonical profile_id for a user-supplied profile name."""
    return PROFILE_ALIASES.get(raw, raw)


# ---------------------------------------------------------------------------
# Shared paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SharedPaths:
    silver: Path
    gold: Path
    duckdb: Path


def load_shared_paths(path: str | Path | None = None) -> SharedPaths:
    """Load config/shared_paths.yml (or an override) into a SharedPaths object."""
    p = Path(path) if path else project_root() / SHARED_PATHS_FILE
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return SharedPaths(
        silver=resolve_path(raw["silver"]),
        gold=resolve_path(raw["gold"]),
        duckdb=resolve_path(raw["duckdb"]),
    )


# ---------------------------------------------------------------------------
# IndicationProfile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndicationProfile:
    """All runtime objects needed to run the pipeline for one indication.

    Attributes:
        profile_id:         Machine-readable identifier (e.g. "adrd", "parkinsons").
                            Used as the bronze path prefix and the value written to
                            the indication_profile_id column in silver/gold.
        display_name:       Human-readable label for dashboards and reports.
        ingest_only:        When True, the transform / dbt / dashboard steps are
                            skipped.  Used for the full_catalog profile.
        config:             ProjectConfig wired to this profile's bronze paths and
                            API query parameters, with shared silver/gold/duckdb
                            paths injected from config/shared_paths.yml.
        taxonomy:           Loaded ConditionTaxonomy, or None for ingest_only profiles.
                            A refreshable profile without a taxonomy file gets
                            REGISTRY_TAXONOMY (whole-registry profiles classify
                            nothing; conditions stay uncategorized).
        shared:             Shared medallion paths (silver, gold, duckdb).
        orchestrate:        Membership in refreshable() — the multi-profile
                            orchestrate loop and the dashboard's scope selector.
                            The registry-wide profile sets False: its weekly
                            refresh runs on its own command, and the dashboard
                            scopes stay indication-level until a later issue
                            generalizes the marts (epic #43).
    """

    profile_id: str
    display_name: str
    ingest_only: bool
    config: ProjectConfig
    taxonomy: ConditionTaxonomy | None
    shared: SharedPaths
    orchestrate: bool = True


# ---------------------------------------------------------------------------
# Profile loader
# ---------------------------------------------------------------------------


def _build_project_config(raw: dict[str, Any], shared: SharedPaths) -> ProjectConfig:
    """Build a ProjectConfig from a profile YAML dict.

    Shared silver/gold/duckdb paths from shared_paths.yml are injected so
    ProfileRegistry callers never need to duplicate those in every profile YAML.
    """
    api_raw = dict(raw["api"])
    http_raw = dict(api_raw.get("http", {}))
    api_raw["http"] = http_raw

    # Override silver/gold/duckdb with shared paths; bronze paths stay per-profile.
    paths_raw: dict[str, Any] = {k: resolve_path(v) for k, v in raw["paths"].items()}
    paths_raw["silver"] = shared.silver
    paths_raw["gold"] = shared.gold
    paths_raw["duckdb"] = shared.duckdb

    return ProjectConfig(
        api=ApiConfig(
            **{k: v for k, v in api_raw.items() if k != "http"},
            http=HttpConfig(**http_raw),
        ),
        paths=PathsConfig(**paths_raw),
        ingestion=IngestionConfig(**(raw.get("ingestion") or {})),
        retention=RetentionConfig.from_raw(raw.get("retention")),
        scope=raw.get("scope", {}),
        guardrails=raw.get("guardrails", {}),
    )


def load_profile(
    profile_path: str | Path,
    shared: SharedPaths | None = None,
) -> IndicationProfile:
    """Load one IndicationProfile from its YAML file.

    Args:
        profile_path: Path to the profile YAML (absolute, or relative to project root).
        shared:       Pre-loaded SharedPaths; loaded from config/shared_paths.yml if None.
    """
    path = Path(profile_path)
    if not path.is_absolute():
        path = project_root() / path

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    pmeta: dict[str, Any] = raw["profile"]
    profile_id: str = pmeta["id"]
    ingest_only: bool = bool(pmeta.get("ingest_only", False))

    resolved_shared = shared or load_shared_paths()
    cfg = _build_project_config(raw, resolved_shared)

    taxonomy: ConditionTaxonomy | None = None
    taxonomy_rel: str | None = pmeta.get("taxonomy")
    if not ingest_only:
        if taxonomy_rel:
            taxonomy = load_taxonomy(project_root() / taxonomy_rel)
        else:
            taxonomy = REGISTRY_TAXONOMY

    return IndicationProfile(
        profile_id=profile_id,
        display_name=str(pmeta.get("display_name", profile_id)),
        ingest_only=ingest_only,
        config=cfg,
        taxonomy=taxonomy,
        shared=resolved_shared,
        orchestrate=bool(pmeta.get("orchestrate", True)),
    )


# ---------------------------------------------------------------------------
# ProfileRegistry
# ---------------------------------------------------------------------------


class ProfileRegistry:
    """Discovers and caches IndicationProfile objects from config/profiles/.

    Profiles are loaded lazily on first access and sorted by profile_id for
    deterministic ordering.

    Usage::

        registry = ProfileRegistry()
        for profile in registry.refreshable():
            run_ingestion(config=profile)
            run_transform(profile=profile)
    """

    def __init__(
        self,
        profiles_dir: str | Path | None = None,
        shared_paths_file: str | Path | None = None,
    ) -> None:
        self._dir = Path(profiles_dir) if profiles_dir else project_root() / PROFILES_DIR
        self._shared = load_shared_paths(shared_paths_file)
        self._profiles: dict[str, IndicationProfile] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        for yml in sorted(self._dir.glob("*.yml")):
            p = load_profile(yml, shared=self._shared)
            self._profiles[p.profile_id] = p
        self._loaded = True

    def all(self) -> list[IndicationProfile]:
        """All discovered profiles, sorted by profile_id."""
        self._ensure_loaded()
        return list(self._profiles.values())

    def refreshable(self) -> list[IndicationProfile]:
        """Profiles eligible for the full pipeline: ingest + transform + dbt.

        Membership is `ingest_only: false` AND `orchestrate: true` (default).
        `ingest_only` profiles keep their bronze but never reach silver; the
        registry-wide profile transforms but opts out of the loop (`orchestrate:
        false`) because its weekly refresh is its own command and the dashboard
        scopes stay indication-level (epic #43).

        When `CTI_REFRESHABLE_PROFILES` is set (comma-separated profile ids),
        the returned set is narrowed to it. The Fly deployment sets this
        because the deployed volume and VM cannot run every refreshable
        profile — docs/DEPLOY_FLY.md, "Adding indication profiles", requires
        storage to be sized from a completed full build before a profile set
        is deployed. A name the registry does not know fails loudly here
        rather than silently shrinking the refresh.
        """
        profiles = [
            profile for profile in self.all() if not profile.ingest_only and profile.orchestrate
        ]
        scope = os.environ.get("CTI_REFRESHABLE_PROFILES")
        if scope is None:
            return profiles
        requested = {
            normalize_profile_id(name.strip()) for name in scope.split(",") if name.strip()
        }
        known = {profile.profile_id for profile in profiles}
        unknown = sorted(requested - known)
        if unknown:
            raise ValueError(
                f"CTI_REFRESHABLE_PROFILES names unknown refreshable profile(s) {unknown}; "
                f"known refreshable profiles: {sorted(known)}"
            )
        if not requested:
            raise ValueError(
                "CTI_REFRESHABLE_PROFILES is set but names no refreshable profile; "
                "refusing to refresh nothing."
            )
        return [profile for profile in profiles if profile.profile_id in requested]

    def get(self, profile_id: str) -> IndicationProfile:
        """Return the named profile, raising KeyError if not found."""
        self._ensure_loaded()
        if profile_id not in self._profiles:
            raise KeyError(
                f"No profile '{profile_id}' found in {self._dir}. "
                f"Known profiles: {sorted(self._profiles)}"
            )
        return self._profiles[profile_id]


@lru_cache(maxsize=1)
def get_registry() -> ProfileRegistry:
    """Cached singleton ProfileRegistry — use for normal runtime calls."""
    return ProfileRegistry()
