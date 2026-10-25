"""Offline SQLite response cache with canonical query hashing, TTL, and circuit breaker.

Implements bounded synchronous stale-if-error fallback and single-flight deduplication.
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, TypeVar

import requests

from src.ingest.retry_policy import RetryableHTTPStatusError

logger = logging.getLogger(__name__)
T = TypeVar("T")


class CacheState(StrEnum):
    """Cache lookup status."""

    HIT = "HIT"
    MISS = "MISS"
    STALE = "STALE"


class CircuitState(StrEnum):
    """Circuit breaker state."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF-OPEN"


class UpstreamUnavailableError(Exception):
    """Raised when upstream API is unreachable or circuit is OPEN with no cached response."""


@dataclass(frozen=True)
class CacheEntry:
    """Retrieved cache entry."""

    payload: str
    state: CacheState
    etag: str
    age_seconds: float
    created_at: float
    ttl_seconds: float


class _InFlight(Generic[T]):
    """Thread-safe state container for an in-flight request."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: T | None = None
        self.error: BaseException | None = None


def scrub_contact_pii(value: Any) -> Any:
    """Remove contact/official modules recursively before serialization or hashing."""
    if isinstance(value, dict):
        return {
            key: scrub_contact_pii(item)
            for key, item in value.items()
            if key
            not in {
                "centralContacts",
                "overallOfficials",
                "contacts",
                "investigatorFullName",
                "investigatorTitle",
            }
        }
    if isinstance(value, list):
        return [scrub_contact_pii(item) for item in value]
    return value


def _is_upstream_failure(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, RetryableHTTPStatusError):
        return exc.status_code == 429 or 500 <= exc.status_code < 600
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code == 429 or 500 <= exc.response.status_code < 600
    return False


def canonical_cache_key(namespace: str, params: dict[str, Any]) -> str:
    """Generate SHA-256 hash of canonicalized query parameters.

    Sorts keys alphabetically, filters out None values, and encodes to deterministic JSON.
    Guarantees query parameter ordering does not affect cache identity.
    """
    clean_items: list[tuple[str, Any]] = []
    for k, v in sorted(params.items()):
        if v is None:
            continue
        if isinstance(v, list):
            clean_items.append((str(k), sorted(str(x) for x in v)))
        else:
            clean_items.append((str(k), str(v)))

    canonical_repr = json.dumps(clean_items, separators=(",", ":"), sort_keys=True)
    full_string = f"{namespace}:{canonical_repr}"
    return hashlib.sha256(full_string.encode("utf-8")).hexdigest()


class CircuitBreaker:
    """Thread-safe circuit breaker protecting against upstream API outages and rate limits."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._probe_inflight = False
        self._total_requests = 0
        self._total_failures = 0
        self._total_successes = 0
        self._circuit_opened_count = 0
        self._last_state_change = time.time()
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_auto_transition()
            return self._state

    def _check_auto_transition(self) -> None:
        """Auto-transition from OPEN to HALF_OPEN after recovery_timeout."""
        if self._state == CircuitState.OPEN:
            now = time.time()
            if now - self._last_state_change >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._consecutive_successes = 0
                self._last_state_change = now

    def allow_request(self) -> bool:
        """Determine if a request should be forwarded upstream."""
        with self._lock:
            self._check_auto_transition()
            if self._state == CircuitState.OPEN:
                return False
            if self._state == CircuitState.HALF_OPEN:
                if self._probe_inflight:
                    return False
                self._probe_inflight = True
            self._total_requests += 1
            return True

    def release_probe(self) -> None:
        """Release a canary after a client error without treating it as an outage."""
        with self._lock:
            self._probe_inflight = False

    def record_success(self) -> None:
        """Record upstream call success."""
        with self._lock:
            self._probe_inflight = False
            self._total_successes += 1
            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._last_state_change = time.time()
            elif self._state == CircuitState.CLOSED:
                self._consecutive_failures = 0

    def record_failure(self) -> None:
        """Record upstream call failure (timeout, 5xx, or 429)."""
        with self._lock:
            self._probe_inflight = False
            self._total_failures += 1
            self._consecutive_failures += 1

            if self._state == CircuitState.CLOSED:
                if self._consecutive_failures >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._circuit_opened_count += 1
                    self._last_state_change = time.time()
            elif self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._circuit_opened_count += 1
                self._consecutive_failures = self.failure_threshold
                self._last_state_change = time.time()

    def reset(self) -> None:
        """Reset circuit breaker to initial CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._probe_inflight = False
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            self._last_state_change = time.time()

    def get_metrics(self) -> dict[str, Any]:
        """Return observability metrics."""
        with self._lock:
            self._check_auto_transition()
            total = self._total_requests
            err_rate = (self._total_failures / total) if total > 0 else 0.0
            return {
                "state": self._state.value,
                "consecutive_failures": self._consecutive_failures,
                "consecutive_successes": self._consecutive_successes,
                "total_requests": self._total_requests,
                "total_failures": self._total_failures,
                "total_successes": self._total_successes,
                "circuit_opened_count": self._circuit_opened_count,
                "error_rate": round(err_rate, 4),
            }


class SQLiteCache:
    """Thread-safe SQLite key-value store with configurable TTL and single-flight deduplication."""

    DEFAULT_TTL_SECONDS = 6.0 * 3600.0  # 6 hours default
    MAX_ENTRIES = 10_000

    def __init__(self, db_path: str | Path = ".api_cache.db") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()

        # Metrics counters
        self._hits = 0
        self._misses = 0
        self._stale_hits = 0
        self._sets = 0
        self._refreshes = 0

        # In-flight request deduplication map
        self._inflight_lock = threading.Lock()
        self._inflight: dict[str, _InFlight[Any]] = {}

        self._conn: sqlite3.Connection
        try:
            self._conn = sqlite3.connect(
                self.db_path, check_same_thread=False, timeout=1.0, isolation_level=None
            )
            if self.db_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._init_db()
        except sqlite3.Error:
            logger.warning(
                "SQLite cache unavailable; using an empty in-memory cache", exc_info=True
            )
            if hasattr(self, "_conn"):
                self._conn.close()
            self._conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
            self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            # Version 0 cached raw contacts; rebuild and erase its disposable payloads.
            if self._conn.execute("PRAGMA user_version").fetchone()[0] != 1:
                self._conn.execute("DROP TABLE IF EXISTS api_cache")
                self._conn.execute("VACUUM")
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.execute("PRAGMA user_version = 1")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    ttl_seconds REAL NOT NULL,
                    etag TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_created ON api_cache(created_at);"
            )

    def get(self, cache_key: str) -> CacheEntry | None:
        """Retrieve entry from SQLite cache, computing whether it is fresh or STALE."""
        now = time.time()
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT payload, created_at, ttl_seconds, etag "
                    "FROM api_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if not row:
                    return None
                payload, created_at, ttl_seconds, etag = row
                if not isinstance(json.loads(payload), dict):
                    return None
                age = max(0.0, now - float(created_at))
                state = CacheState.HIT if age <= float(ttl_seconds) else CacheState.STALE
                return CacheEntry(
                    payload=payload,
                    state=state,
                    etag=etag,
                    age_seconds=age,
                    created_at=float(created_at),
                    ttl_seconds=float(ttl_seconds),
                )
        except (sqlite3.Error, ValueError, TypeError):
            logger.warning("SQLite cache read failed; treating as MISS", exc_info=True)
            return None

    def set(
        self,
        cache_key: str,
        payload: str,
        ttl_seconds: float | None = None,
        etag: str | None = None,
    ) -> None:
        """Store or update response payload in SQLite cache."""
        parsed = json.loads(payload)
        sanitized = scrub_contact_pii(parsed)
        if sanitized != parsed:
            payload = json.dumps(sanitized, separators=(",", ":"))
            etag = None
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.DEFAULT_TTL_SECONDS
        if etag is None:
            etag = f'W/"{hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]}"'

        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO api_cache
                        (cache_key, payload, created_at, ttl_seconds, etag)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (cache_key, payload, now, ttl, etag),
                )
                self._conn.execute(
                    "DELETE FROM api_cache WHERE cache_key IN ("
                    "SELECT cache_key FROM api_cache ORDER BY created_at DESC, cache_key DESC "
                    "LIMIT -1 OFFSET ?)",
                    (self.MAX_ENTRIES,),
                )
                self._sets += 1
        except sqlite3.Error:
            logger.warning("SQLite cache write failed; returning upstream response", exc_info=True)

    def single_flight(self, key: str, fetch_fn: Callable[[], T]) -> T:
        """Deduplicate in-progress queries to prevent thundering herd against upstream."""
        flight: _InFlight[T]
        with self._inflight_lock:
            if key in self._inflight:
                flight = self._inflight[key]
                is_leader = False
            else:
                flight = _InFlight[T]()
                self._inflight[key] = flight
                is_leader = True

        if not is_leader:
            # Wait for leader to complete
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            assert flight.result is not None
            return flight.result

        # Leader path
        try:
            result = fetch_fn()
            flight.result = result
            return result
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            flight.event.set()
            with self._inflight_lock:
                self._inflight.pop(key, None)

    def stale_if_error(
        self,
        cache_key: str,
        fetch_fn: Callable[[], tuple[str, str]],
        circuit_breaker: CircuitBreaker,
        ttl_seconds: float | None = None,
    ) -> CacheEntry:
        """Return fresh data, or stale data on outages; client errors propagate.

        The flight leader owns admission, breaker accounting, and the cache write.
        Followers receive the same result, including its original retrieval timestamp.
        """
        entry = self.get(cache_key)
        if entry is not None and entry.state == CacheState.HIT:
            with self._lock:
                self._hits += 1
            return entry

        def execute() -> CacheEntry:
            # Recheck after joining the flight: a previous leader may just have committed.
            current = self.get(cache_key)
            if current is not None and current.state == CacheState.HIT:
                with self._lock:
                    self._hits += 1
                return current
            with self._lock:
                if current is None:
                    self._misses += 1
            try:
                if not circuit_breaker.allow_request():
                    raise UpstreamUnavailableError("Upstream service is temporarily unavailable.")
                try:
                    payload, etag = fetch_fn()
                except Exception as exc:
                    if _is_upstream_failure(exc):
                        circuit_breaker.record_failure()
                        raise UpstreamUnavailableError(
                            "Upstream service is temporarily unavailable."
                        ) from exc
                    circuit_breaker.release_probe()
                    raise
                except BaseException:
                    circuit_breaker.release_probe()
                    raise
                circuit_breaker.record_success()
                created_at = time.time()
                self.set(cache_key, payload, ttl_seconds=ttl_seconds, etag=etag)
                if current is not None:
                    with self._lock:
                        self._refreshes += 1
                return CacheEntry(
                    payload=payload,
                    state=CacheState.MISS,
                    etag=etag,
                    age_seconds=0.0,
                    created_at=created_at,
                    ttl_seconds=ttl_seconds
                    if ttl_seconds is not None
                    else self.DEFAULT_TTL_SECONDS,
                )
            except UpstreamUnavailableError:
                if current is None:
                    raise
                with self._lock:
                    self._stale_hits += 1
                return current

        return self.single_flight(cache_key, execute)

    def get_metrics(self) -> dict[str, Any]:
        """Expose cache metrics for health and status endpoints."""
        with self._lock:
            try:
                count_row = self._conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()
                count = count_row[0] if count_row else 0
            except sqlite3.Error:
                count = 0
            total_lookups = self._hits + self._misses + self._stale_hits + self._refreshes
            hit_ratio = (
                (self._hits + self._stale_hits) / total_lookups if total_lookups > 0 else 0.0
            )
            return {
                "size_entries": count,
                "hits": self._hits,
                "misses": self._misses,
                "stale_hits": self._stale_hits,
                "refreshes": self._refreshes,
                "total_lookups": total_lookups,
                "hit_ratio": round(hit_ratio, 4),
            }

    def close(self) -> None:
        """Close SQLite connection."""
        with self._lock:
            self._conn.close()
