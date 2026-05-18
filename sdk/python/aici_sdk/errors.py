"""Custom exceptions that surface API failures with context."""

from __future__ import annotations

from typing import Any


class AiciError(RuntimeError):
    """Base exception for the SDK."""


class AiciApiError(AiciError):
    """Raised when the API returns a non-success status code."""

    def __init__(self, status_code: int, message: str | None = None, *, payload: Any | None = None) -> None:
        self.status_code = status_code
        self.payload = payload
        detail = message or "Unexpected API error"
        super().__init__(f"[{status_code}] {detail}")


class AiciAuthenticationError(AiciApiError):
    """Raised for 401/403 responses."""


class AiciRateLimitError(AiciApiError):
    """Raised for HTTP 429 throttling responses."""
