from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt
from passlib.context import CryptContext

from ai_crypto_index.shared.settings import AuthSettings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, hashed_password: str | None) -> bool:
    if not hashed_password:
        return False
    return _pwd_context.verify(password, hashed_password)


def generate_token(length: int = 48) -> str:
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_expiry(seconds: int) -> datetime:
    return utcnow() + timedelta(seconds=seconds)


def create_access_token(
    *,
    subject: str,
    roles: list[str],
    settings: AuthSettings,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    expires_at = build_expiry(settings.access_token_ttl_seconds)
    payload: dict[str, Any] = {
        "sub": subject,
        "roles": roles,
        "iat": int(utcnow().timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_at
