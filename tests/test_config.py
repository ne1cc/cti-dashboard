from src.config import get_config, load_config


def test_load_config():
    cfg = load_config()
    assert cfg.api.base_url
    assert cfg.paths.silver
    assert cfg.paths.duckdb
    assert get_config() is not None
