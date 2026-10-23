from pathlib import Path

import pytest
import yaml

from src.config import get_config, load_config


def test_load_config():
    cfg = load_config()
    assert cfg.api.base_url
    assert cfg.paths.silver
    assert cfg.paths.duckdb
    assert get_config() is not None


def test_load_config_default_profile_resolution():
    """Verify that default load_config() resolves scope and query_params from adrd profile."""
    cfg = load_config()
    assert cfg.default_profile == "adrd"
    assert "query.cond" in cfg.api.query_params
    assert cfg.api.query_params["query.cond"] == "Alzheimer Disease"

    expected_statuses = [
        "RECRUITING",
        "ACTIVE_NOT_RECRUITING",
        "NOT_YET_RECRUITING",
        "COMPLETED",
        "TERMINATED",
        "WITHDRAWN",
        "SUSPENDED",
        "ENROLLING_BY_INVITATION",
    ]
    assert cfg.scope.get("statuses") == expected_statuses
    assert cfg.api.query_params.get("filter.overallStatus") == expected_statuses
    assert len(cfg.scope.get("statuses", [])) == 8


def test_load_config_missing_profile_fails_loudly(tmp_path: Path):
    """If default_profile points to a non-existent profile, load_config must fail loudly."""
    custom_yaml = tmp_path / "custom.yml"
    custom_yaml.write_text(
        yaml.dump(
            {
                "default_profile": "nonexistent_profile_xyz",
                "api": {"base_url": "https://example.com/api"},
                "paths": {
                    "bronze_api_responses": "data/bronze",
                    "bronze_manifests": "data/manifests",
                    "silver": "data/silver",
                    "gold": "data/gold",
                    "duckdb": "data/test.duckdb",
                    "quarantine": "data/quarantine",
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError, match="nonexistent_profile_xyz"):
        load_config(custom_yaml)


def test_load_config_invalid_profile_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If default_profile points to an invalid/corrupt profile, load_config must fail loudly."""
    profiles_dir = tmp_path / "config" / "profiles"
    profiles_dir.mkdir(parents=True)
    corrupt_profile = profiles_dir / "corrupt.yml"
    corrupt_profile.write_text("invalid_not_a_dict", encoding="utf-8")

    custom_yaml = tmp_path / "config" / "custom.yml"
    custom_yaml.write_text(
        yaml.dump(
            {
                "default_profile": "corrupt",
                "api": {"base_url": "https://example.com/api"},
                "paths": {
                    "bronze_api_responses": "data/bronze",
                    "bronze_manifests": "data/manifests",
                    "silver": "data/silver",
                    "gold": "data/gold",
                    "duckdb": "data/test.duckdb",
                    "quarantine": "data/quarantine",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CTI_PROJECT_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="invalid"):
        load_config(custom_yaml)


def test_load_config_standalone_without_default_profile(tmp_path: Path):
    """Standalone configs without default_profile should preserve their own query_params & scope."""
    standalone_yaml = tmp_path / "standalone.yml"
    custom_query_params = {"query.cond": "Custom Disease", "filter.overallStatus": ["RECRUITING"]}
    custom_scope = {"therapeutic_area": "Custom TA", "statuses": ["RECRUITING"]}
    standalone_yaml.write_text(
        yaml.dump(
            {
                "api": {
                    "base_url": "https://custom.api.gov",
                    "query_params": custom_query_params,
                },
                "scope": custom_scope,
                "paths": {
                    "bronze_api_responses": "data/bronze",
                    "bronze_manifests": "data/manifests",
                    "silver": "data/silver",
                    "gold": "data/gold",
                    "duckdb": "data/test.duckdb",
                    "quarantine": "data/quarantine",
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(standalone_yaml)
    assert cfg.default_profile is None
    assert cfg.api.query_params == custom_query_params
    assert cfg.scope == custom_scope


def test_load_config_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test CTI_CONFIG_PATH, CTI_HTTP_TIMEOUT_SECONDS, and CTI_MAX_RETRIES overrides."""
    custom_yaml = tmp_path / "env_test.yml"
    custom_yaml.write_text(
        yaml.dump(
            {
                "api": {
                    "base_url": "https://example.com/api",
                    "http": {"timeout_seconds": 10.0, "max_retries": 2},
                },
                "paths": {
                    "bronze_api_responses": "data/bronze",
                    "bronze_manifests": "data/manifests",
                    "silver": "data/silver",
                    "gold": "data/gold",
                    "duckdb": "data/test.duckdb",
                    "quarantine": "data/quarantine",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CTI_CONFIG_PATH", str(custom_yaml))
    monkeypatch.setenv("CTI_HTTP_TIMEOUT_SECONDS", "45.5")
    monkeypatch.setenv("CTI_MAX_RETRIES", "9")

    cfg = load_config()
    assert cfg.api.http.timeout_seconds == 45.5
    assert cfg.api.http.max_retries == 9
