"""Unit tests for offline SQLite response cache and circuit breaker.

Verifies:
- Canonical cache key generation regardless of parameter ordering.
- Cache HIT, MISS, and TTL expiration (STALE).
- Stale-while-revalidate serving on upstream error or open circuit.
- Circuit breaker state transitions: CLOSED -> OPEN -> HALF-OPEN -> CLOSED.
- Concurrency deduplication (single-flight in-progress query coalescing).
- Cache and circuit breaker metrics reporting.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.api.cache import (
    CacheState,
    CircuitBreaker,
    CircuitState,
    SQLiteCache,
    UpstreamUnavailableError,
    canonical_cache_key,
)


def test_canonical_cache_key_order_independence() -> None:
    """Verifies canonical cache key produces identical SHA-256 regardless of param order."""
    params_a = {
        "cond": "Alzheimer Disease",
        "limit": 20,
        "radius_mi": 50,
        "lat": 37.77,
        "lon": -122.41,
    }
    params_b = {
        "limit": 20,
        "radius_mi": 50,
        "cond": "Alzheimer Disease",
        "lon": -122.41,
        "lat": 37.77,
    }
    params_c = {**params_a, "none_param": None}

    key_a = canonical_cache_key("trials_search", params_a)
    key_b = canonical_cache_key("trials_search", params_b)
    key_c = canonical_cache_key("trials_search", params_c)

    assert key_a == key_b
    assert key_a == key_c
    assert len(key_a) == 64  # SHA-256 hex length


def test_canonical_cache_key_distinct_for_different_queries() -> None:
    key1 = canonical_cache_key("trials_search", {"cond": "Alzheimer"})
    key2 = canonical_cache_key("trials_search", {"cond": "Parkinson"})
    key3 = canonical_cache_key("trial_detail", {"cond": "Alzheimer"})

    assert key1 != key2
    assert key1 != key3


def test_sqlite_cache_hit_and_miss() -> None:
    cache = SQLiteCache(db_path=":memory:")
    key = "test_key_123"

    assert cache.get(key) is None

    cache.set(key, '{"data": "test_payload"}', ttl_seconds=60.0)
    entry = cache.get(key)

    assert entry is not None
    assert entry.state == CacheState.HIT
    assert entry.payload == '{"data": "test_payload"}'
    assert entry.etag.startswith('W/"')
    assert entry.age_seconds >= 0.0

    cache.close()


def test_sqlite_cache_ttl_expiration_to_stale() -> None:
    cache = SQLiteCache(db_path=":memory:")
    key = "test_stale_key"

    # Set with 0.05 second TTL
    cache.set(key, '{"data": "expiring"}', ttl_seconds=0.05)
    entry_fresh = cache.get(key)
    assert entry_fresh is not None
    assert entry_fresh.state == CacheState.HIT

    time.sleep(0.08)

    entry_stale = cache.get(key)
    assert entry_stale is not None
    assert entry_stale.state == CacheState.STALE
    assert entry_stale.payload == '{"data": "expiring"}'

    cache.close()


def test_stale_while_revalidate_success_refreshes_cache() -> None:
    cache = SQLiteCache(db_path=":memory:")
    cb = CircuitBreaker()
    key = "swr_key"

    # Seed expired item
    cache.set(key, '{"version": 1}', ttl_seconds=0.01)
    time.sleep(0.03)

    fetch_called = 0

    def mock_fetch() -> tuple[str, str]:
        nonlocal fetch_called
        fetch_called += 1
        return '{"version": 2}', 'W/"v2"'

    payload, state, etag = cache.stale_while_revalidate(key, mock_fetch, circuit_breaker=cb)

    assert fetch_called == 1
    assert state == CacheState.HIT
    assert payload == '{"version": 2}'
    assert etag == 'W/"v2"'

    cache.close()


def test_stale_while_revalidate_error_serves_stale() -> None:
    cache = SQLiteCache(db_path=":memory:")
    cb = CircuitBreaker()
    key = "swr_fail_key"

    # Seed expired item
    cache.set(key, '{"version": 1}', ttl_seconds=0.01, etag='W/"v1"')
    time.sleep(0.03)

    def failing_fetch() -> tuple[str, str]:
        raise RuntimeError("Upstream CTG 503 Service Unavailable")

    payload, state, etag = cache.stale_while_revalidate(key, failing_fetch, circuit_breaker=cb)

    # Fallback to stale payload
    assert state == CacheState.STALE
    assert payload == '{"version": 1}'
    assert etag == 'W/"v1"'
    assert cb.get_metrics()["consecutive_failures"] == 1

    cache.close()


def test_stale_while_revalidate_miss_failure_raises() -> None:
    cache = SQLiteCache(db_path=":memory:")
    cb = CircuitBreaker()
    key = "swr_no_cache_key"

    def failing_fetch() -> tuple[str, str]:
        raise RuntimeError("Upstream 500 error")

    with pytest.raises(UpstreamUnavailableError):
        cache.stale_while_revalidate(key, failing_fetch, circuit_breaker=cb)

    cache.close()


def test_circuit_breaker_transitions() -> None:
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.1, success_threshold=2)

    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # 1. Trigger failures to trip to OPEN
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED

    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False

    # 2. Before recovery_timeout expires, request not allowed
    assert cb.allow_request() is False

    # 3. After recovery_timeout, probe request auto-transitions to HALF-OPEN
    time.sleep(0.12)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow_request() is True

    # 4. Canary successes transition back to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.HALF_OPEN  # needs 2 successes

    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True


def test_circuit_breaker_half_open_failure_reopens() -> None:
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05, success_threshold=2)

    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    time.sleep(0.06)
    assert cb.state == CircuitState.HALF_OPEN

    # Failure in half-open trips straight back to OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False


def test_single_flight_deduplication() -> None:
    cache = SQLiteCache(db_path=":memory:")
    key = "concurrent_key"
    call_count = 0

    def slow_fetch() -> tuple[str, str]:
        nonlocal call_count
        call_count += 1
        time.sleep(0.05)
        return '{"result": "ok"}', 'W/"123"'

    def worker() -> tuple[str, str]:
        return cache.single_flight(key, slow_fetch)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(worker) for _ in range(5)]
        results = [f.result() for f in futures]

    assert len(results) == 5
    for r in results:
        assert r == ('{"result": "ok"}', 'W/"123"')

    # Upstream function should have been called exactly once
    assert call_count == 1
    cache.close()


def test_cache_metrics() -> None:
    cache = SQLiteCache(db_path=":memory:")
    cb = CircuitBreaker()

    cache.set("k1", "v1")
    cache.set("k2", "v2")

    # Hit
    cache.stale_while_revalidate("k1", lambda: ("v1", "e1"), cb)
    # Miss + Success
    cache.stale_while_revalidate("k3", lambda: ("v3", "e3"), cb)

    metrics = cache.get_metrics()
    assert metrics["size_entries"] == 3
    assert metrics["hits"] == 1
    assert metrics["misses"] == 1
    assert metrics["hit_ratio"] == 0.5

    cache.close()
