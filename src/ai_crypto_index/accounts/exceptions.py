from __future__ import annotations


class AccountError(Exception):
    """Base class for account-related errors."""


class AccountAlreadyExists(AccountError):
    pass


class AccountNotFound(AccountError):
    pass


class InvalidCredentials(AccountError):
    pass


class AccountInactive(AccountError):
    pass


class TokenExpired(AccountError):
    pass


class TokenInvalid(AccountError):
    pass


class SessionInvalid(AccountError):
    pass


class ConfirmationResendRateLimited(AccountError):
    pass
