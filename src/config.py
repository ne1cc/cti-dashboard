"""Typed access to config/project_config.yml with .env overrides.

All API query parameters live in YAML — never hard-coded in ingestion code.
Indication scope and query parameters default to the authoritative profile
referenced by `default_profile` (e.g. `config/profiles/adrd.yml`).
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

    default_profile: str | None = None
    api: ApiConfig
    paths: PathsConfig
    ingestion: IngestionConfig
    retention: RetentionConfig = RetentionConfig()
    scope: dict[str, Any] = {}
    guardrails: dict[str, Any] = {}


def load_config(config_path: str | Path | None = None) -> ProjectConfig:
    load_dotenv(project_root() / ".env")
    env_path = os.getenv("CTI_CONFIG_PATH", "config/project_config.yml")
    path = Path(config_path if config_path is not None else env_path)
    if not path.is_absolute():
        path = project_root() / path
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Config at {path} is not a valid YAML dictionary")

    default_profile = raw.get("default_profile")
    if default_profile:
        from src.profiles import PROFILES_DIR, normalize_profile_id

        canonical_id = normalize_profile_id(str(default_profile))
        profile_path = project_root() / PROFILES_DIR / f"{canonical_id}.yml"
        if not profile_path.exists():
            raise FileNotFoundError(
                f"Referenced default profile '{default_profile}' not found at {profile_path}"
            )
        profile_raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        if (
            not isinstance(profile_raw, dict)
            or "api" not in profile_raw
            or not isinstance(profile_raw.get("api"), dict)
        ):
            raise ValueError(f"Profile at {profile_path} is invalid or missing 'api' configuration")
        resolved_query_params = profile_raw.get("api", {}).get("query_params", {})
        resolved_scope = profile_raw.get("scope", {})
    else:
        resolved_query_params = raw.get("api", {}).get("query_params", {})
        resolved_scope = raw.get("scope", {})

    api_raw = dict(raw.get("api", {}))
    api_raw["query_params"] = resolved_query_params
    http_raw = dict(api_raw.get("http", {}))
    if os.getenv("CTI_HTTP_TIMEOUT_SECONDS"):
        http_raw["timeout_seconds"] = float(os.environ["CTI_HTTP_TIMEOUT_SECONDS"])
    if os.getenv("CTI_MAX_RETRIES"):
        http_raw["max_retries"] = int(os.environ["CTI_MAX_RETRIES"])
    api_raw["http"] = http_raw

    paths_raw = {key: resolve_path(value) for key, value in raw.get("paths", {}).items()}

    return ProjectConfig(
        default_profile=default_profile,
        api=ApiConfig(**api_raw),
        paths=PathsConfig(**paths_raw),
        ingestion=IngestionConfig(**(raw.get("ingestion") or {})),
        retention=RetentionConfig.from_raw(raw.get("retention")),
        scope=resolved_scope,
        guardrails=raw.get("guardrails", {}),
    )


@lru_cache(maxsize=1)
def get_config() -> ProjectConfig:
    return load_config()
