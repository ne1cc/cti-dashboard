# Local DuckDB/Parquet Warehouse Serving Engine

Milestone M2 ([Issue #59](https://github.com/ne1cc/cti-dashboard/issues/59), Epic #44) implements the local DuckDB/Parquet warehouse-backed read engine under the exact same OpenAPI 3.1 specification (`docs/api/openapi.yaml`) as the live ClinicalTrials.gov search service.

The service exposes high-performance, low-latency, fully parameterized query execution over dimensional Gold marts and staging tables, with thread-safe read-only connection pooling, keyset cursor pagination, and enterprise-grade observability.

---

## 1. Startup & Deployment

### Local Development
To launch the FastAPI service backed by DuckDB warehouse:

```bash
# Point to local DuckDB database
export CTI_WAREHOUSE_PATH="data/warehouse/clinical_trials.duckdb"

# Start Uvicorn server with application factory
uv run uvicorn src.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

Importing `src.api.app` or `src.api.warehouse` has zero side-effects and never opens database connections or caches at module import time.

### Container Target
The project `Dockerfile` provides a dedicated `api` multi-stage build target:

```bash
# Build the API container image
docker build --target api -t cti-api:latest .

# Run the container with mounted warehouse volume
docker run -d \
  -p 8000:8000 \
  -e CTI_WAREHOUSE_PATH="/app/data/warehouse/clinical_trials.duckdb" \
  -v "$(pwd)/data:/app/data:ro" \
  cti-api:latest
```

The service can also be invoked via `entrypoint.sh api`:

```bash
./entrypoint.sh api --host 0.0.0.0 --port 8000
```

---

## 2. Serving Architecture & Concurrency

```
                +---------------------------------------+
                |           HTTP Client / UI            |
                +---------------------------------------+
                                    |
                    [X-Request-ID, ETag, CORS]
                                    v
                +---------------------------------------+
                |        FastAPI Router & Lifespan      |
                |   - Request Correlation Middleware    |
                |   - Structured JSON Access Logging    |
                |   - Caching / ETag / 304 Handling     |
                +---------------------------------------+
                                    |
                            [ContainerDep]
                                    v
                +---------------------------------------+
                |        DuckDBWarehouseManager         |
                +---------------------------------------+
                                    |
                    [lease_connection() / Context]
                                    v
                +---------------------------------------+
                |         DuckDBConnectionPool          |
                |   - Thread-safe Bounded Queue         |
                |   - read_only = True Connections      |
                |   - Dynamic Schema Search Path        |
                +---------------------------------------+
                                    |
                    [Parameterized SQL Execution]
                                    v
+-----------------------------------------------------------------------+
|                       DuckDB / Parquet Warehouse                      |
|  - dim_trial                (Core study dimensions & eligibility)     |
|  - fct_trial_snapshot       (Point-in-time trial observations)        |
|  - fct_trial_site           (Geographic recruitment facilities)       |
|  - stg_trials               (Ingestion snapshot & study metadata)     |
+-----------------------------------------------------------------------+
```

### Connection Pool Lifecycle
- **Read-Only Enforced**: All pooled connections are opened with `read_only=True`. Any write attempt (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`) is blocked at the database engine level.
- **Bounded Capacity**: The pool maintains a bounded queue (default `max_connections=10`, configurable) guarded by a thread-safe semaphore and queue timeout (5.0s). If the pool is saturated and times out, a clean HTTP 503 `WAREHOUSE_BUSY` error is emitted.
- **Clean Architectural Isolation**: The warehouse engine is completely decoupled from Streamlit runtime state. It never imports `dashboard.components.data` or mutates global UI caches.
- **Dynamic Search Path**: Upon opening, each connection discovers available schemas (`main_marts`, `main_staging`, `marts`, `staging`, `main`) from `information_schema.schemata` and configures the DuckDB search path automatically.

---

## 3. Parameterized SQL & Keyset Pagination

### Strict Parameter Binding
All SQL queries executed by the warehouse service are stored in external static files:
- `src/api/sql/search.sql`: Interventional trial search, multi-faceted filtering, and geographic proximity scoring.
- `src/api/sql/detail.sql`: Complete protocol detail, study design, sponsors, interventions, conditions, and facilities.

No query string interpolation, formatted strings, or dynamic SQL concatenation is permitted. Parameter values are safely bound via DuckDB's native parameter dictionary interface.

### Keyset Cursor Pagination
Keyset cursor pagination replaces offset pagination for constant-time $O(1)$ page traversal across millions of trial records:
- **Primary Sort Keys Supported**:
  - `-updated` (latest first, default) / `updated` (oldest first)
  - `-start_date` (latest start first) / `start_date` (earliest start first)
  - `distance` (nearest site first, requires `lat` and `lon`)
- **Deterministic Tie-Breaker**: Every query appends `nct_id` as the secondary sort key to eliminate tie ambiguity.
- **Opaque URL-Safe Cursors**: Cursors are encoded as base64 JSON objects: `{"sort": <str>, "primary_val": <val>, "tie_breaker": <nct_id>}`. Invalid or corrupted cursors produce a clean HTTP 400 `INVALID_CURSOR` response.

---

## 4. ADR 0065 Contact Privacy Guardrails

Per [ADR 0065](../decisions/0065-contact-policy.md):
- **Zero Contact PII**: Personal investigator names, study coordinator contact details, telephone numbers, and email addresses are omitted from warehouse queries and scrubbed before response serialization.
- **Official Registry Deep Links**: All trial summaries and detail payloads supply official `registry_url` and `registry_contact_url` pointing directly to `https://clinicaltrials.gov/study/{nct_id}#contacts-locations`.

---

## 5. Observability & Diagnostics

### Health & Readiness Probes
The service implements standard Kubernetes-style liveness and readiness probes:

1. **`GET /healthz` (Liveness)**
   - Returns immediate HTTP 200 `{"status": "ok", "timestamp": "..."}`.
   - Fast, non-blocking check that the process event loop and server are responsive.

2. **`GET /readyz` (Readiness)**
   - Verifies DuckDB file existence and connection acquisition.
   - Validates that required dimensional tables (`dim_trial`, `fct_trial_snapshot`, `fct_trial_site`, `stg_trials`) exist in the catalog.
   - Retrieves snapshot metadata (`snapshot_id`, `snapshot_date`, `snapshot_timestamp_utc`, and active `trials_count`).
   - If database connection fails or tables are missing, returns HTTP 503 `WAREHOUSE_NOT_READY`.

### Response Shape (`/readyz`)
```json
{
  "status": "ready",
  "backend": "warehouse",
  "database_accessible": true,
  "snapshot_id": "snap_20261020",
  "snapshot_date": "2026-10-20",
  "snapshot_timestamp_utc": "2026-10-20T12:00:00Z",
  "trials_count": 482150,
  "timestamp": "2026-10-25T07:00:00.000000Z"
}
```

### Correlation & Structured JSON Logging
- Every request is tagged with an `X-Request-ID`. Clients may supply their own; otherwise, a UUIDv4 is generated.
- All access logs are emitted to standard output as single-line JSON objects:
  ```json
  {"timestamp": "2026-10-25T07:15:02.123456Z", "request_id": "8b9e6c43-...", "method": "GET", "path": "/v1/trials", "status_code": 200, "duration_ms": 12.45}
  ```

### HTTP Caching & ETag Semantics
- Responses emit standard headers:
  - `Data-Snapshot`: Warehouse snapshot ID of current data release.
  - `Data-Updated`: ISO 8601 timestamp of data snapshot generation.
  - `ETag`: Weak SHA-256 validator computed over serialized response payload.
  - `Cache-Control`: `public, max-age=300`.
- Clients sending conditional `If-None-Match: <etag>` receive an immediate HTTP 304 Not Modified response with empty body.
