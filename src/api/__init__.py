"""Public Read API package.

Exposes FastAPI app, data models, cache, and live ClinicalTrials.gov normalizer.
"""

from src.api.app import app, create_app
from src.api.cache import CacheState, CircuitBreaker, CircuitState, SQLiteCache
from src.api.models import (
    NearestSiteSummary,
    PaginationMetadata,
    ProblemDetail,
    ProblemDetails,
    StructuredEligibilitySummary,
    TrialDetail,
    TrialsListResponse,
    TrialSummary,
)

__all__ = [
    "app",
    "create_app",
    "SQLiteCache",
    "CacheState",
    "CircuitBreaker",
    "CircuitState",
    "TrialSummary",
    "TrialsListResponse",
    "PaginationMetadata",
    "StructuredEligibilitySummary",
    "NearestSiteSummary",
    "TrialDetail",
    "ProblemDetail",
    "ProblemDetails",
]
