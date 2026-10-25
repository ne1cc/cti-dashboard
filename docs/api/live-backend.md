# Live API patient search backend

Milestone M1-A ([roadmap](../roadmap.md), Issue #60) serves patient search from ClinicalTrials.gov API v2 without waiting for a registry-wide warehouse. The implemented endpoints are `GET /v1/trials`, `GET /v1/trials/{nct_id}`, `GET /health`, and `GET /v1/metadata/status`. The [OpenAPI contract](openapi.yaml) describes the response models.

## Startup and interactive transport

Run the application factory explicitly:

```sh
uv run uvicorn src.api.app:create_app --factory
```

Importing the API package does not open a client or create a database. `CTI_CACHE_PATH` selects the SQLite path (default `.api_cache.db`); `create_app(cache_path=...)` overrides it. Interactive calls use a five-second HTTP timeout and at most two attempts, with one second of backoff. These are request connect/read timeouts, not a hard end-to-end deadline. Ingestion retains its existing transport settings.

## Query mapping and validation

| Read API parameter | Upstream parameter or behavior |
|---|---|
| `cond` | `query.cond` |
| `status` | `filter.overallStatus`; defaults to `RECRUITING\|NOT_YET_RECRUITING\|ACTIVE_NOT_RECRUITING\|ENROLLING_BY_INVITATION` |
| `lat`, `lon`, `radius_mi` | `filter.geo=distance(lat,lon,radius_mi)` with the radius unit appended, for example `distance(37.7749,-122.4194,50.0mi)` |
| `limit` | `pageSize`; 1–100, default 20 |
| `cursor` | Opaque upstream `pageToken` |
| `min_age`, `max_age`, `sex`, `healthy_volunteers` | Filter the fetched page in memory; preserve unstated criteria |
| `phase`, `std_age_group`, `sponsor_class` | Filter the fetched page in memory; phase matches a complete phase token, including multi-phase studies |
| `intervention_type`, `updated_since` | Explicit HTTP 422 `UNSUPPORTED_FILTER` until implemented; invalid dates are rejected during validation |
| `sort` | Sort the fetched page in memory |
| Internal | `countTotal=true`, `format=json` |

Enums and numeric ranges follow the contract. Coordinates must be finite, within latitude/longitude bounds, and supplied together. The radius must be 1–500 miles, and ages must be 0–130 years. An inverted age range returns 400. `sort=distance` requires coordinates and otherwise returns 422. Invalid parameter types, enum values, and ranges return RFC 7807 400/422 problems before upstream dispatch.

Age filters compare stated bounds with the requested range; missing bounds do not exclude a study (Issue #62). Sex and healthy-volunteer filters also retain unspecified eligibility. A standard-age-group filter retains trials without declared groups; it never infers groups from numeric ages. Sponsor class requires an explicit matching class.

### Pagination and ordering limits

**Eligibility, phase, standard-age-group and sponsor filters, and all sorts operate on one upstream page.** They do not produce a globally filtered or globally sorted cursor sequence. A page can contain fewer than `limit` items, including zero, while `has_more=true`. Continue using `next_cursor` to inspect later upstream pages; `has_more` means the registry supplied another cursor, not that a later page is guaranteed to contain an eligible result.

`total_count` is `null` whenever an in-memory filter is requested or malformed studies were omitted. Otherwise it is the registry's total for the upstream condition/status/geography query. No approximation of a post-filtered total is claimed. A future index or upstream filtering implementation is needed for global eligibility pagination and ordering.

Distance uses the nearest listed site with valid coordinates and the Haversine formula:

```text
a = sin²(Δlatitude / 2)
    + cos(latitude₁) × cos(latitude₂) × sin²(Δlongitude / 2)
distance_miles = 2 × 3958.8 × atan2(√a, √(1 − a))
```

Angles are in radians. Sites without usable `geoPoint` values remain in detail responses with null coordinates/distance; they cannot supply the nearest-site distance. Missing distances sort last.

## Privacy and normalization

Under [ADR 0065](../decisions/0065-contact-policy.md), upstream responses are recursively scrubbed **before serialization, ETag hashing, or SQLite writes**. Keys `centralContacts`, `overallOfficials`, and `contacts` are removed at every nesting level, including location arrays. Responsible-party investigator names/titles are also removed. SQLite writes repeat this check at the storage boundary. Tests inspect the on-disk `api_cache.payload` table after both endpoints and verify that the synthetic fixture names, email addresses, and phone numbers are absent.

The cache holds scrubbed registry JSON, never verbatim upstream response text. Cache schema version 1 invalidates the earlier unsanitized cache and vacuums/checkpoints its disposable contents on startup. Response models expose facility addresses and official `registry_url`/`registry_contact_url` links for inquiries.

A missing or invalid NCT identifier causes the study to be logged and skipped from search, rather than assigned a synthetic ID. Other required strings use `UNKNOWN`; optional values stay null, and absent age groups remain empty. Invalid dates become null. Condition groups remain null until a taxonomy is applied. Provenance quality flags reuse the pipeline's quality checks, and detail retrieval timestamps come from the cache entry. A malformed search record cannot fail the entire page; malformed detail returns a clean 503 problem.

## SQLite cache and single flight

The six-hour TTL cache uses canonical SHA-256 query keys: keys are sorted, `None` values omitted, and lists sorted. It uses WAL mode, a short SQLite lock timeout, and a maximum of 10,000 rows, evicting the oldest writes. This bounds row count; payload sizes and concurrent distinct queries are not globally limited.

- `HIT`: return a fresh cached payload immediately.
- `MISS`: fetch upstream and attempt to cache the sanitized response. A successful stale refresh is also reported as `MISS` and increments `refreshes`.
- `STALE`: if refresh encounters a timeout, connection error, 429, or 5xx, or the breaker rejects admission, return the expired entry with a warning. With no entry, return 503.

This is **synchronous stale-if-error**, not background stale-while-revalidate. A stale request waits for the bounded upstream attempt before fallback. Concurrent identical requests share a flight: only the leader obtains breaker admission, records the outcome, updates metrics, and writes the cache. Followers receive the same result or exception. The flight remains registered until the write completes.

Unreadable/corrupt cache files fall back to an empty in-memory cache. Read errors or invalid JSON rows become misses; write errors do not fail a successful upstream response. Cache storage remains an optimization.

## Breaker, errors, and freshness

Five consecutive upstream outages open the breaker for 30 seconds. After that, `HALF-OPEN` admits **one probe at a time** across all keys. Two sequential successful probes close it; a failed probe reopens it. A client error releases the probe without incrementing successes or failures.

Only timeouts, connection failures, 429, and 5xx count as outages. Other upstream 4xx responses propagate as safe RFC 7807 400/404/422 problems, including bad cursors, and never serve stale data or trip the breaker. Unexpected programming errors return a generic 500 detail and are logged internally. Starlette 404/405 errors also use `application/problem+json`; 405 retains its `Allow` header. Problem `instance` consistently contains the full request URI.

Upstream 429 currently becomes 503 after bounded retries, with stale fallback when available. The live path does not yet honor the upstream `Retry-After` header; its backoff remains fixed by the shared retry policy.

All successful search/detail responses include:

| Header | Meaning |
|---|---|
| `Data-Snapshot: live-api` | Live registry source |
| `Data-Updated` | UTC retrieval time of the cached source payload, including on stale responses |
| `ETag` | Weak hash of the scrubbed registry payload |
| `Cache-Control` | `public, max-age=300` for fresh responses; `no-cache` for stale responses |
| `X-Cache-Status` | `HIT`, `MISS`, or `STALE` |
| `Warning` | `110 "Response is Stale"` on stale fallback |

Health endpoints expose cache hits/misses/stale hits/refreshes and breaker metrics. Breaker request counts include admitted upstream flights only, excluding fresh cache hits, followers, and rejected admissions. Client 4xx calls are admitted attempts but do not count as outage failures.

## Verification scope

Automated tests use mocked HTTP or recorded/synthetic fixtures and make no live network calls. The geo syntax is covered by fixture-based parameter tests; this fix round did not re-verify the current live registry, rate limits, or `fields` projection. Deployment configuration remains outside this milestone's review-fix scope. The current Starlette TestClient emits a dependency deprecation warning for `httpx`; migrating that dependency is deferred.
