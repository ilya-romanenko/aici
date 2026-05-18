from __future__ import annotations

from fastapi import Depends

from ai_crypto_index.shared.settings import ServiceSettings, get_settings

from .service import BillingService


def get_billing_service(
    settings: ServiceSettings = Depends(get_settings),
) -> BillingService:
    return BillingService(settings)


__all__ = ["get_billing_service"]
