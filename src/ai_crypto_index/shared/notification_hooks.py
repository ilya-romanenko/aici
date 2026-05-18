from __future__ import annotations

import logging
from typing import Iterable

import requests

logger = logging.getLogger("ai_crypto_index.webhooks")


def deliver_webhook_events(
    event_type: str,
    payload: dict[str, object],
    endpoints: Iterable[str] | None,
    *,
    timeout: float = 5.0,
) -> None:
    if not endpoints:
        return
    for endpoint in endpoints:
        url = (endpoint or "").strip()
        if not url:
            continue
        try:
            response = requests.post(
                url,
                json={
                    "event": event_type,
                    "payload": payload,
                },
                timeout=timeout,
            )
            response.raise_for_status()
        except Exception:
            logger.exception(
                "failed_to_deliver_webhook_event",
                extra={
                    "event_type": event_type,
                    "endpoint": url,
                },
            )


__all__ = ["deliver_webhook_events"]
