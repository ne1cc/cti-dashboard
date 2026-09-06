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
    retained — i.e. the warehouse's longitudinal depth. Snapshot depth may
    exceed bronze depth, but only by the slack src/utils/retention.py refuses to
    go past; the manifests dbt reads stay on the snapshot horizon, so every
    silver run it globs still has a manifest.
    """

    model_config = ConfigDict(extra="ignore")

    bronze_runs_to_keep: int = 1
    snapshot_runs_to_keep: int = 6

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


def load_config(config_path: str | Path | None = None) -> ProjectConfig:
    load_dotenv(project_root() / ".env")
    env_path = os.getenv("CTI_CONFIG_PATH", "config/project_config.yml")
    path = Path(config_path if config_path is not None else env_path)
    if not path.is_absolute():
        path = project_root() / path
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    api_raw = dict(raw["api"])
    http_raw = dict(api_raw.get("http", {}))
    if os.getenv("CTI_HTTP_TIMEOUT_SECONDS"):
        http_raw["timeout_seconds"] = float(os.environ["CTI_HTTP_TIMEOUT_SECONDS"])
    if os.getenv("CTI_MAX_RETRIES"):
        http_raw["max_retries"] = int(os.environ["CTI_MAX_RETRIES"])
    api_raw["http"] = http_raw

    paths_raw = {key: resolve_path(value) for key, value in raw["paths"].items()}

    return ProjectConfig(
        api=ApiConfig(**api_raw),
        paths=PathsConfig(**paths_raw),
        ingestion=IngestionConfig(**(raw.get("ingestion") or {})),
        retention=RetentionConfig.from_raw(raw.get("retention")),
        scope=raw.get("scope", {}),
        guardrails=raw.get("guardrails", {}),
    )


@lru_cache(maxsize=1)
def get_config() -> ProjectConfig:
    return load_config()
