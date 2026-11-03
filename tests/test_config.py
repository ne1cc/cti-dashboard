from pathlib import Path

import pytest

from src.config import get_config, load_config


def test_load_config():
    cfg = load_config()
    assert cfg.api.base_url
    assert cfg.paths.silver
    assert cfg.paths.duckdb
    assert get_config() is not None


def test_default_config_resolves_full_catalog_profile():
    cfg = load_config()
    assert cfg.default_profile == "full_catalog"
    assert cfg.api.query_params == {}
    assert cfg.scope["statuses"] == []


def test_adrd_profile_resolves_adrd_scope():
    from src.profiles import load_profile

    adrd = load_profile("config/profiles/adrd.yml")
    cfg = adrd.config
    expected_statuses = {
        "RECRUITING",
        "ACTIVE_NOT_RECRUITING",
        "NOT_YET_RECRUITING",
        "COMPLETED",
        "TERMINATED",
        "WITHDRAWN",
        "SUSPENDED",
        "ENROLLING_BY_INVITATION",
    }
    assert set(cfg.api.query_params["filter.overallStatus"]) == expected_statuses
    assert set(cfg.scope["statuses"]) == expected_statuses
    assert cfg.api.query_params["query.cond"] == "Alzheimer Disease"
    assert cfg.api.query_params["filter.advanced"] == "AREA[StudyType]INTERVENTIONAL"


def test_load_config_env_var_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CTI_HTTP_TIMEOUT_SECONDS", "42.5")
    monkeypatch.setenv("CTI_MAX_RETRIES", "7")

    cfg = load_config()
    assert cfg.api.http.timeout_seconds == 42.5
    assert cfg.api.http.max_retries == 7


def test_standalone_config_semantics(tmp_path: Path):
    standalone_yaml = tmp_path / "custom_standalone.yml"
    standalone_yaml.write_text(
        """\
api:
  base_url: https://example.org/api
  query_params:
    query.cond: "Custom Disease"
    filter.overallStatus:
      - RECRUITING
paths:
  bronze_api_responses: data/bronze/custom/api_responses
  bronze_manifests: data/bronze/custom/manifests
  silver: data/silver
  gold: data/gold
  duckdb: data/warehouse/x.duckdb
  quarantine: data/bronze/custom/manifests/quarantine
scope:
  statuses:
    - RECRUITING
"""
    )

    cfg = load_config(standalone_yaml)
    assert cfg.default_profile is None
    assert cfg.api.base_url == "https://example.org/api"
    assert cfg.api.query_params["query.cond"] == "Custom Disease"
    assert cfg.scope["statuses"] == ["RECRUITING"]
