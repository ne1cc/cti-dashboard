"""Typed access to config/project_config.yml with .env overrides.

All API query parameters live in YAML — never hard-coded in ingestion code.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from src.utils.paths import project_root, resolve_path


class HttpConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timeout_seconds: float = 30.0
    max_retries: int = 5
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    retry_on_status: list[int] = [429, 500, 502, 503, 504]


class ApiConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_url: str
    studies_endpoint: str = "/studies"
    format: str = "json"
    page_size: int = 100
    count_total: bool = True
    # Field projection (GET /studies `fields`): branch names under the study
    # record whose descendants are all included, e.g.
    # "protocolSection.identificationModule". Empty means "return everything",
    # which is what a slim registry build must never do (issue #53).
    fields: list[str] = []
    # Minimum wall-clock spacing between HTTP requests. The API publishes no
    # numeric rate limit (no rate-limit response headers, OpenAPI spec and NLM
    # bulletins silent as of 2026-10-30); ~50 requests/minute is the community
    # consensus, so the shipped configs set 1.2s. 429s retry with backoff
    # regardless (retry_policy.py).
    min_request_interval_seconds: float = 0.0
    query_params: dict[str, Any] = {}
    http: HttpConfig = HttpConfig()

    @property
    def studies_url(self) -> str:
        return self.base_url.rstrip("/") + self.studies_endpoint


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bronze_api_responses: Path
    bronze_manifests: Path
    silver: Path
    gold: Path
    duckdb: Path
    quarantine: Path


class IngestionConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode_default: str = "incremental"
    reuse_window_hours: int = 24
    page_file_pattern: str = "run_id={run_id}/page={page:05d}.json"
    manifest_file_pattern: str = "manifest_{run_id}.json"


class RetentionConfig(BaseModel):
    """How many past runs survive a prune, per profile.

    bronze_runs_to_keep counts *success* ingestion runs whose raw pages are
    retained; snapshot_runs_to_keep counts runs whose silver + manifest are
    retained — i.e. the warehouse's longitudinal depth. Snapshot depth may run
    deeper than bronze depth by any amount (snapshots are the cheap bytes and the
    history); bronze deeper than snapshot is what src/utils/retention.py refuses,
    because the runs in between keep raw pages no later prune can discover. An
    ingest_only profile never has silver, so for it bronze_runs_to_keep is the
    only horizon that bounds its raw pages at all.

    This default is the shipped horizon (3), not a looser one: the depth was
    brought down from 6 by a dated volume measurement (1 GB deployed, 499.7 MB
    steady state and 866.5 MB worst-instant at 3 snapshots for two profiles,
    2026-09-07, commit 78c2050 — docs/DEPLOY_FLY.md). A config with no
    `retention:` block must not reintroduce the setting that overflows it.
    """

    model_config = ConfigDict(extra="ignore")

    bronze_runs_to_keep: int = 1
    snapshot_runs_to_keep: int = 3

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "RetentionConfig":
        """Build from a config block, treating absent or empty as the defaults.

        A bare `retention:` line in YAML parses to None, and `cls(**None)` is a
        TypeError raised from inside the code path that deletes files. Loading
        retention must never be the thing that fails.
        """
        return cls(**(raw or {}))


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api: ApiConfig
    paths: PathsConfig
    ingestion: IngestionConfig
    retention: RetentionConfig = RetentionConfig()
    scope: dict[str, Any] = {}
    guardrails: dict[str, Any] = {}
    default_profile: str | None = None


def load_config(config_path: str | Path | None = None) -> ProjectConfig:
    load_dotenv(project_root() / ".env")
    env_path = os.getenv("CTI_CONFIG_PATH", "config/project_config.yml")
    path = Path(config_path if config_path is not None else env_path)
    if not path.is_absolute():
        path = project_root() / path
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    api_raw = dict(raw.get("api") or {})
    http_raw = dict(api_raw.get("http", {}))
    if os.getenv("CTI_HTTP_TIMEOUT_SECONDS"):
        http_raw["timeout_seconds"] = float(os.environ["CTI_HTTP_TIMEOUT_SECONDS"])
    if os.getenv("CTI_MAX_RETRIES"):
        http_raw["max_retries"] = int(os.environ["CTI_MAX_RETRIES"])
    api_raw["http"] = http_raw

    default_profile = raw.get("default_profile")
    if default_profile is not None:
        if not isinstance(default_profile, str) or not default_profile.strip():
            raise ValueError(f"default_profile must be a non-empty string, got {default_profile!r}")
        # Local import to prevent circular import loops (config -> profiles -> config)
        from src.profiles import PROFILES_DIR, load_profile, normalize_profile_id

        canonical_id = normalize_profile_id(default_profile.strip())
        profile_candidate = Path(default_profile.strip())

        if profile_candidate.is_absolute() and profile_candidate.is_file():
            profile_path = profile_candidate
        elif (path.parent / profile_candidate).is_file():
            profile_path = path.parent / profile_candidate
        elif (project_root() / profile_candidate).is_file():
            profile_path = project_root() / profile_candidate
        elif (path.parent / "profiles" / f"{canonical_id}.yml").is_file():
            profile_path = path.parent / "profiles" / f"{canonical_id}.yml"
        elif (path.parent / "profiles" / f"{canonical_id}.yaml").is_file():
            profile_path = path.parent / "profiles" / f"{canonical_id}.yaml"
        elif (project_root() / PROFILES_DIR / f"{canonical_id}.yml").is_file():
            profile_path = project_root() / PROFILES_DIR / f"{canonical_id}.yml"
        elif (project_root() / PROFILES_DIR / f"{canonical_id}.yaml").is_file():
            profile_path = project_root() / PROFILES_DIR / f"{canonical_id}.yaml"
        else:
            profile_path = project_root() / PROFILES_DIR / f"{canonical_id}.yml"

        if not profile_path.is_file():
            raise FileNotFoundError(
                f"Referenced default_profile '{default_profile}' not found at {profile_path}"
            )

        prof = load_profile(profile_path)
        api_raw["query_params"] = prof.config.api.query_params
        scope_raw = prof.config.scope
    else:
        scope_raw = raw.get("scope", {})

    paths_raw = {key: resolve_path(value) for key, value in (raw.get("paths") or {}).items()}

    return ProjectConfig(
        api=ApiConfig(**api_raw),
        paths=PathsConfig(**paths_raw),
        ingestion=IngestionConfig(**(raw.get("ingestion") or {})),
        retention=RetentionConfig.from_raw(raw.get("retention")),
        scope=scope_raw,
        guardrails=raw.get("guardrails", {}),
        default_profile=default_profile,
    )


@lru_cache(maxsize=1)
def get_config() -> ProjectConfig:
    return load_config()
