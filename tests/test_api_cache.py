"""Unit tests for offline SQLite response cache and circuit breaker.

Verifies:
- Canonical cache key generation regardless of parameter ordering.
- Cache HIT, MISS, and TTL expiration (STALE).
- Synchronous stale-if-error fallback on outages or open circuit.
- Circuit breaker state transitions: CLOSED -> OPEN -> HALF-OPEN -> CLOSED.
- Concurrency deduplication (single-flight in-progress query coalescing).
- Cache and circuit breaker metrics reporting.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import requests

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

    cache._conn.execute("UPDATE api_cache SET created_at = created_at - 100")

    entry_stale = cache.get(key)
    assert entry_stale is not None
    assert entry_stale.state == CacheState.STALE
    assert entry_stale.payload == '{"data": "expiring"}'

    cache.close()


def test_stale_if_error_success_refreshes_cache() -> None:
    cache = SQLiteCache(db_path=":memory:")
    cb = CircuitBreaker()
    key = "swr_key"

    # Seed expired item
    cache.set(key, '{"version": 1}', ttl_seconds=0.01)
    cache._conn.execute("UPDATE api_cache SET created_at = created_at - 100")

    fetch_called = 0

    def mock_fetch() -> tuple[str, str]:
        nonlocal fetch_called
        fetch_called += 1
        return '{"version": 2}', 'W/"v2"'

    entry = cache.stale_if_error(key, mock_fetch, circuit_breaker=cb)

    assert fetch_called == 1
    assert entry.state == CacheState.MISS
    assert entry.payload == '{"version": 2}'
    assert entry.etag == 'W/"v2"'

    cache.close()


def test_stale_if_error_error_serves_stale() -> None:
    cache = SQLiteCache(db_path=":memory:")
    cb = CircuitBreaker()
    key = "swr_fail_key"

    # Seed expired item
    cache.set(key, '{"version": 1}', ttl_seconds=0.01, etag='W/"v1"')
    cache._conn.execute("UPDATE api_cache SET created_at = created_at - 100")

    def failing_fetch() -> tuple[str, str]:
        raise requests.Timeout("upstream timed out")

    entry = cache.stale_if_error(key, failing_fetch, circuit_breaker=cb)

    # Fallback to stale payload
    assert entry.state == CacheState.STALE
    assert entry.payload == '{"version": 1}'
    assert entry.etag == 'W/"v1"'
    assert cb.get_metrics()["consecutive_failures"] == 1

    cache.close()


def test_stale_if_error_miss_failure_raises() -> None:
    cache = SQLiteCache(db_path=":memory:")
    cb = CircuitBreaker()
    key = "swr_no_cache_key"

    def failing_fetch() -> tuple[str, str]:
        raise requests.ConnectionError("connection dropped")

    with pytest.raises(UpstreamUnavailableError):
        cache.stale_if_error(key, failing_fetch, circuit_breaker=cb)

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
    cb._last_state_change -= 1
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

    cb._last_state_change -= 1
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

    cache.set("k1", '{"value": 1}')
    cache.set("k2", '{"value": 2}')

    # Hit
    cache.stale_if_error("k1", lambda: ('{"value": 1}', "e1"), cb)
    # Miss + Success
    cache.stale_if_error("k3", lambda: ('{"value": 3}', "e3"), cb)

    metrics = cache.get_metrics()
    assert metrics["size_entries"] == 3
    assert metrics["hits"] == 1
    assert metrics["misses"] == 1
    assert metrics["hit_ratio"] == 0.5

    cache.close()


@pytest.mark.parametrize("outcome", ["success", "timeout", "cancel"])
def test_single_flight_accounts_once(monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    cache = SQLiteCache(":memory:")
    cb = CircuitBreaker()
    started = threading.Event()
    release = threading.Event()
    joined = threading.Barrier(6)
    calls = 0

    class Cancelled(BaseException):
        pass

    def fetch() -> tuple[str, str]:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(5)
        if outcome == "cancel":
            raise Cancelled()
        if outcome == "timeout":
            raise requests.Timeout("upstream timed out")
        return '{"value": 1}', 'W/"one"'

    def request() -> Any:
        try:
            return cache.stale_if_error("key", fetch, cb)
        except (UpstreamUnavailableError, Cancelled):
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        leader = pool.submit(request)
        assert started.wait(5)
        flight = cache._inflight["key"]
        original_wait = flight.event.wait

        def wait(timeout: float | None = None) -> bool:
            joined.wait(5)
            return original_wait(timeout)

        monkeypatch.setattr(flight.event, "wait", wait)
        followers = [pool.submit(request) for _ in range(5)]
        joined.wait(5)
        release.set()
        results = [future.result(5) for future in [leader, *followers]]
    assert calls == 1
    assert all(result == results[0] for result in results)
    metrics = cb.get_metrics()
    assert metrics["total_failures"] == int(outcome == "timeout")
    assert metrics["total_successes"] == int(outcome == "success")
    assert metrics["total_requests"] == 1
    assert cb.state == CircuitState.CLOSED
    assert cache.get_metrics()["misses"] == 1
    cache.close()


def test_half_open_allows_only_one_probe() -> None:
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0, success_threshold=2)
    cb.record_failure()
    assert cb.allow_request()
    assert not cb.allow_request()
    cb.record_success()
    assert cb.allow_request()
    assert not cb.allow_request()
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_old_unscrubbed_cache_is_invalidated(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    cache = SQLiteCache(path)
    cache._conn.execute("PRAGMA user_version = 0")
    cache._conn.execute(
        "INSERT INTO api_cache VALUES (?, ?, ?, ?, ?)",
        ("legacy", '{"centralContacts": [{"email": "private@example.com"}]}', 0, 60, "old"),
    )
    cache.close()
    cache = SQLiteCache(path)
    assert cache.get("legacy") is None
    assert cache.get_metrics()["size_entries"] == 0
    cache.close()
    assert b"private@example.com" not in path.read_bytes()


def test_cache_eviction_keeps_newest_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = SQLiteCache(":memory:")
    monkeypatch.setattr(cache, "MAX_ENTRIES", 2, raising=False)
    for index in range(3):
        cache.set(str(index), '{"ok": true}')
    assert cache.get("0") is None
    assert cache.get("1") is not None
    assert cache.get("2") is not None
    cache.close()


def test_cache_write_is_inside_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = SQLiteCache(":memory:")
    cb = CircuitBreaker()
    writing = threading.Event()
    release = threading.Event()
    original_set = cache.set

    def blocked_set(*args: Any, **kwargs: Any) -> None:
        writing.set()
        assert release.wait(5)
        original_set(*args, **kwargs)

    monkeypatch.setattr(cache, "set", blocked_set)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(cache.stale_if_error, "key", lambda: ('{"ok": true}', "etag"), cb)
        assert writing.wait(5)
        try:
            assert "key" in cache._inflight
        finally:
            release.set()
        result.result(5)
    assert cache.get("key") is not None
    cache.close()


def test_storage_boundary_scrubs_contacts_recursively() -> None:
    cache = SQLiteCache(":memory:")
    payload = {
        "nested": [
            {
                "centralContacts": [{"email": "private@example.com"}],
                "overallOfficials": [{"name": "Private Official"}],
                "locations": [{"contacts": [{"phone": "555-0100"}], "facility": "Hospital"}],
                "responsibleParty": {
                    "investigatorFullName": "Private Investigator",
                    "type": "SPONSOR",
                },
            }
        ]
    }
    cache.set("key", json.dumps(payload))
    row = cache._conn.execute("SELECT payload FROM api_cache").fetchone()
    assert json.loads(row[0]) == {
        "nested": [
            {
                "locations": [{"facility": "Hospital"}],
                "responsibleParty": {"type": "SPONSOR"},
            }
        ]
    }
    cache.close()


def test_client_error_releases_half_open_probe() -> None:
    cache = SQLiteCache(":memory:")
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0)
    cb.record_failure()

    def bad_query() -> tuple[str, str]:
        response = requests.Response()
        response.status_code = 400
        raise requests.HTTPError(response=response)

    with pytest.raises(requests.HTTPError):
        cache.stale_if_error("key", bad_query, cb)
    assert cb.get_metrics()["total_failures"] == 1
    assert cb.allow_request()
    cache.close()
