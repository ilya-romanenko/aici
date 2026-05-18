from __future__ import annotations

from fastapi import Depends

from ai_crypto_index.shared.settings import ServiceSettings, get_settings

from .service import ApiKeyService


def get_api_key_service(
    settings: ServiceSettings = Depends(get_settings),
) -> ApiKeyService:
    return ApiKeyService(settings)


__all__ = ["get_api_key_service", "ApiKeyService"]
