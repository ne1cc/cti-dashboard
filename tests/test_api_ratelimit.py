"""Per-client rate limit on the live (upstream-calling) API path."""

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.api.app import APIContainer, create_app
from src.api.ratelimit import SlidingWindowLimiter


def test_limiter_blocks_after_limit_and_frees_after_window() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
    assert limiter.retry_after("a", now=0) == 0
    assert limiter.retry_after("a", now=1) == 0
    assert limiter.retry_after("a", now=2) > 0
    assert limiter.retry_after("b", now=2) == 0  # other clients unaffected
    assert limiter.retry_after("a", now=61) == 0  # first hit aged out


def test_limiter_disabled_when_limit_is_zero() -> None:
    limiter = SlidingWindowLimiter(limit=0)
    assert all(limiter.retry_after("a", now=0) == 0 for _ in range(100))


def test_live_search_returns_429_with_retry_after(tmp_path: Path) -> None:
    ctg = MagicMock()
    ctg.fetch_studies.return_value = ({"studies": []}, {})
    deps = APIContainer(ctg_client=ctg, cache_path=tmp_path / "c.db")
    deps.live_limiter = SlidingWindowLimiter(limit=1)
    with TestClient(create_app(container=deps)) as client:
        assert client.get("/v1/trials?cond=x").status_code == 200
        resp = client.get("/v1/trials?cond=y")
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1
