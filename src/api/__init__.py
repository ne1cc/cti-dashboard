"""Public Read API package.

Exposes FastAPI app, data models, cache, live normalizer, and DuckDB warehouse backend.
"""

from src.api.app import create_app
from src.api.cache import CacheState, CircuitBreaker, CircuitState, SQLiteCache
from src.api.models import (
    NearestSiteSummary,
    PaginationMetadata,
    ProblemDetail,
    ReadinessStatus,
    StructuredEligibilitySummary,
    TrialDetail,
    TrialsListResponse,
    TrialSummary,
)
from src.api.warehouse import (
    DuckDBWarehouseManager,
    WarehouseNotReadyError,
    WarehousePoolTimeoutError,
)

__all__ = [
    "create_app",
    "SQLiteCache",
    "CacheState",
    "CircuitBreaker",
    "CircuitState",
    "DuckDBWarehouseManager",
    "WarehouseNotReadyError",
    "WarehousePoolTimeoutError",
    "ReadinessStatus",
    "TrialSummary",
    "TrialsListResponse",
    "PaginationMetadata",
    "StructuredEligibilitySummary",
    "NearestSiteSummary",
    "TrialDetail",
    "ProblemDetail",
]
