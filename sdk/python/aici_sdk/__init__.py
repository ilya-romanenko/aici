"""Public entrypoint for the AI Crypto Index Python SDK."""

from .client import AiciClient, DEFAULT_BASE_URL
from .errors import AiciApiError, AiciAuthenticationError, AiciError, AiciRateLimitError
from .models import (
    IndexComposition,
    IndexComponent,
    PerformanceSnapshot,
    RunPerformance,
    WeightEntry,
    WeightsSnapshot,
)

__all__ = [
    "AiciClient",
    "DEFAULT_BASE_URL",
    "AiciError",
    "AiciApiError",
    "AiciAuthenticationError",
    "AiciRateLimitError",
    "WeightsSnapshot",
    "WeightEntry",
    "RunPerformance",
    "PerformanceSnapshot",
    "IndexComponent",
    "IndexComposition",
]

__version__ = "0.1.0"
