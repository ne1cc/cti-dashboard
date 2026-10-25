# Live-API Patient Search Backend & Offline Cache Architecture

This document specifies the architecture, query translation rules, caching mechanics, circuit breaker semantics, and observability metrics for the Live-API Patient Discovery backend ([Milestone M1-A](file:///docs/roadmap.md), Issue #60).

---

## 1. Background & Architecture Overview

The patient search service bridges prospective clinical trial participants with active, recruiting studies without waiting for the full-registry local warehouse build to complete. 

It exposes FastAPI endpoints (`/v1/trials` and `/v1/trials/{nct_id}`) backed by the hardened `CTGClient` (`src/ingest/ctg_client.py`), normalized into the OpenAPI 3.1 schema contract (`docs/api/openapi.yaml`).

```
                              +---------------------------------------+
                              |         Patient Discovery Client      |
                              +---------------------------------------+
                                                  |
                                                  | GET /v1/trials?cond=...
                                                  v
                              +---------------------------------------+
                              |         FastAPI Search Layer          |
                              |   Parameter Validation & RFC 7807     |
                              +---------------------------------------+
                                        |                   ^
                         Cache Lookup   |                   | Normalized Response
                                        v                   |
               +----------------------------------+         |
               |       SQLite Response Cache      |         |
               |  - Canonical SHA-256 Hashing     |         |
               |  - Configurable TTL (default 6h) |         |
               |  - In-flight Single-Flight Sync  |         |
               +----------------------------------+         |
                         | Cache Miss / Stale               |
                         v                                  |
               +----------------------------------+         |
               |      Circuit Breaker Monitor     |         |
               |  - CLOSED -> OPEN -> HALF-OPEN   |         |
               +----------------------------------+         |
                         | Allowed                          |
                         v                                  |
               +----------------------------------+         |
               |   ClinicalTrials.gov API v2      |---------+
               |   GET /api/v2/studies            |
               +----------------------------------+
```

---

## 2. Parameter Translation & Upstream Query Mapping

User queries against `/v1/trials` are mapped to the upstream ClinicalTrials.gov REST API v2 parameters:

| Read API Query Param | Upstream CTG v2 Parameter | Format / Derivation Rule |
|---|---|---|
| `cond` | `query.cond` | Unquoted condition string, e.g. `"Alzheimer Disease"` |
| `status` | `filter.overallStatus` | Pipe-separated enum string; defaults to `RECRUITING\|NOT_YET_RECRUITING\|ACTIVE_NOT_RECRUITING\|ENROLLING_BY_INVITATION` |
| `lat`, `lon`, `radius_mi` | `filter.geo` | Great-circle proximity filter: `distance(lat,lon,radius_mi mi)`, e.g. `distance(37.7749,-122.4194,50.0mi)` |
| `limit` | `pageSize` | Integer (1–100); default 20 |
| `cursor` | `pageToken` | Keyset pagination token |
| *(Internal)* | `countTotal` | Set to `"true"` on searches |
| *(Internal)* | `format` | Set to `"json"` |

### Geographic Filter Syntax
When both `lat` and `lon` are provided, the query builder constructs the `filter.geo` parameter:
```text
filter.geo=distance({lat},{lon},{radius_mi}mi)
```
If only one coordinate is provided, the API rejects the request with HTTP 400 Bad Request (`INVALID_COORDINATES`) using an RFC 7807 envelope.

### Sorting & Proximity
Sorting by `distance` requires valid coordinates. When `sort=distance` is requested without coordinates, the API returns HTTP 422 Unprocessable Entity (`MISSING_COORDINATES_FOR_SORT`). In-memory sorting calculates the great-circle distance to the nearest participating site for each study using the Haversine formula:
$$d = 2 R \arcsin\left(\sqrt{\sin^2\left(\frac{\Delta \phi}{2}\right) + \cos(\phi_1)\cos(\phi_2)\sin^2\left(\frac{\Delta \lambda}{2}\right)}\right)$$
where $R = 3958.8\text{ miles}$.

---

## 3. SQLite Response Caching & Deduplication

To minimize latency, avoid upstream rate limits, and provide offline resilience, query responses are cached in a thread-safe SQLite key-value store (`src/api/cache.py`).

### Canonical Cache Hashing
Cache keys are computed as a SHA-256 digest over the canonicalized JSON representation of the query parameters:
1. Keys are sorted alphabetically.
2. `None` and empty values are stripped.
3. List-valued parameters are sorted.
4. The serialized string `f"{namespace}:{json_str}"` is hashed to a 64-character hexadecimal digest.

Parameter order differences (e.g. `?cond=AD&limit=20` vs `?limit=20&cond=AD`) produce identical cache keys.

### Cache States & Stale-While-Revalidate
- **`HIT`**: The entry exists in the database and `age <= ttl_seconds`. The cached payload is returned immediately with `X-Cache-Status: HIT`.
- **`MISS`**: No entry exists. An upstream request is dispatched through the single-flight coalescer, cached, and returned with `X-Cache-Status: MISS`.
- **`STALE`**: The entry exists but `age > ttl_seconds`.
  - Under normal conditions, an upstream revalidation is attempted.
  - If the revalidation succeeds, the cache is updated and the fresh payload is returned with `X-Cache-Status: HIT`.
  - If upstream fails (timeout, 5xx, 429) or the circuit breaker is `OPEN`, the expired payload is returned with `X-Cache-Status: STALE` and a HTTP warning header:
    ```http
    Warning: 110 "Response is Stale"
    ```

### Single-Flight In-Progress Deduplication
When multiple concurrent requests query the same cache key during a `MISS` or `STALE` revalidation, the single-flight manager ensures only one thread issues an upstream HTTP request. All other callers await the leader's completion via thread events and reuse the result, preventing thundering herds.

---

## 4. Circuit Breaker Semantics

The circuit breaker (`CircuitBreaker`) protects the system when ClinicalTrials.gov experiences outages or rate limits:

```
               +---------------------------------------+
               |                CLOSED                 |
               |           (Normal Operation)          |
               +---------------------------------------+
                    |                             ^
       >= 5 Failures|                             | 2 Canary
                    v                             | Successes
               +------------------+        +------------------+
               |       OPEN       |------->|    HALF-OPEN     |
               | (Reject Upstream)| 30s    | (Canary Probing) |
               +------------------+        +------------------+
                                  Recovery
                                  Timeout
```

- **`CLOSED`**: Requests pass upstream. Consecutive failures are tracked.
- **`OPEN`**: Tripped when consecutive failures reach `failure_threshold` (default 5). Upstream calls are rejected. If cached data is available, stale responses are served; if no cache exists, HTTP 503 Service Unavailable (`UPSTREAM_UNAVAILABLE`) is returned.
- **`HALF-OPEN`**: After `recovery_timeout` (default 30 seconds), a canary request is allowed to probe upstream health. If consecutive successes reach `success_threshold` (default 2), the circuit transitions back to `CLOSED`. If any failure occurs, it re-opens immediately.

---

## 5. ADR 0065 Contact Privacy Guardrails

In strict adherence to [ADR 0065](file:///docs/decisions/0065-contact-policy.md):
1. **Zero Personal Contact PII**:
   - Upstream fields (`centralContacts`, `overallOfficials`, and location-level `contacts`) containing personal coordinator names, telephone numbers, and email addresses are **never** extracted, cached, or returned.
2. **Authoritative Official Registry Link**:
   - Prospective participants are directed to the canonical registry URL for study team inquiries:
     ```json
     {
       "registry_url": "https://clinicaltrials.gov/study/NCT04245644",
       "registry_contact_url": "https://clinicaltrials.gov/study/NCT04245644#contacts-and-locations"
     }
     ```
3. **Geographic Facilities Only**:
   - Facility listings include institution name, city, state, postal code, country, and calculated distance.

---

## 6. Observability & Health

The system exposes operational metrics via `GET /health` and `GET /v1/metadata/status`:

```json
{
  "status": "ok",
  "cache": {
    "size_entries": 142,
    "hits": 350,
    "misses": 48,
    "stale_hits": 12,
    "total_lookups": 410,
    "hit_ratio": 0.8829
  },
  "circuit_breaker": {
    "state": "CLOSED",
    "consecutive_failures": 0,
    "consecutive_successes": 15,
    "total_requests": 60,
    "total_failures": 2,
    "total_successes": 58,
    "circuit_opened_count": 0,
    "error_rate": 0.0333
  },
  "timestamp": "2026-10-25T03:55:00.000000Z"
}
```
