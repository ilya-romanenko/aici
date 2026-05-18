from __future__ import annotations


class ApiKeyError(Exception):
    """Base error for API key operations."""


class ApiKeyLimitReached(ApiKeyError):
    """Raised when an account exceeded the allowed number of keys."""


class ApiKeyNotFound(ApiKeyError):
    """Raised when an API key could not be located."""


class ApiKeyInactive(ApiKeyError):
    """Raised when operations target a disabled or revoked key."""


class InvalidApiKeySecret(ApiKeyError):
    """Raised when an API key secret is invalid."""


class ApiKeyQuotaExceeded(ApiKeyError):
    """Raised when the key exceeded a quota scope."""

    def __init__(self, scope: str, limit: int | None) -> None:
        super().__init__(f"{scope}_quota_exceeded")
        self.scope = scope
        self.limit = limit


class ApiKeyRestrictionError(ApiKeyError):
    """Raised when API key restrictions are invalid."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


__all__ = [
    "ApiKeyError",
    "ApiKeyInactive",
    "ApiKeyLimitReached",
    "ApiKeyNotFound",
    "InvalidApiKeySecret",
    "ApiKeyQuotaExceeded",
    "ApiKeyRestrictionError",
]
