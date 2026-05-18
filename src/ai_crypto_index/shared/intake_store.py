from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_crypto_index.shared.settings import ServiceSettings

INTAKE_DIR_NAME = "_intake"
DEMO_REQUESTS_FILE = "demo_requests.jsonl"
REGISTRATION_REQUESTS_FILE = "registrations.jsonl"
API_KEY_REQUESTS_FILE = "api_key_requests.jsonl"
CTA_EVENTS_FILE = "cta_events.jsonl"
CTA_ANALYTICS_EVENTS_FILE = "cta_events_analytics.jsonl"
BILLING_EVENTS_FILE = "billing_events.jsonl"
SUPPORT_TICKETS_FILE = "support_tickets.jsonl"


def _intake_dir(settings: ServiceSettings) -> Path:
    target = settings.runs_root / INTAKE_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def _persist_payload(
    settings: ServiceSettings,
    payload: dict[str, Any],
    target_filename: str,
    *,
    request_id: str | None = None,
    received_at: str | None = None,
) -> tuple[str, str]:
    intake_dir = _intake_dir(settings)
    if not request_id:
        request_id = uuid.uuid4().hex
    if not received_at:
        received_at = datetime.now(timezone.utc).isoformat()

    record = dict(payload)
    record["request_id"] = request_id
    record["received_at"] = received_at

    target_file = intake_dir / target_filename
    with target_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return request_id, received_at


def persist_demo_request(
    settings: ServiceSettings,
    payload: dict[str, Any],
) -> tuple[str, str]:
    return _persist_payload(settings, payload, DEMO_REQUESTS_FILE)


def persist_registration_request(
    settings: ServiceSettings,
    payload: dict[str, Any],
) -> tuple[str, str]:
    return _persist_payload(settings, payload, REGISTRATION_REQUESTS_FILE)


def persist_api_key_request(
    settings: ServiceSettings,
    payload: dict[str, Any],
) -> tuple[str, str]:
    return _persist_payload(settings, payload, API_KEY_REQUESTS_FILE)


def persist_cta_event(
    settings: ServiceSettings,
    payload: dict[str, Any],
) -> tuple[str, str]:
    return _persist_payload(settings, payload, CTA_EVENTS_FILE)


def persist_cta_analytics_event(
    settings: ServiceSettings,
    payload: dict[str, Any],
    *,
    event_id: str | None = None,
    received_at: str | None = None,
) -> tuple[str, str]:
    return _persist_payload(
        settings,
        payload,
        CTA_ANALYTICS_EVENTS_FILE,
        request_id=event_id,
        received_at=received_at,
    )


def persist_billing_event(
    settings: ServiceSettings,
    payload: dict[str, Any],
) -> tuple[str, str]:
    return _persist_payload(settings, payload, BILLING_EVENTS_FILE)


def persist_support_ticket(
    settings: ServiceSettings,
    payload: dict[str, Any],
) -> tuple[str, str]:
    return _persist_payload(settings, payload, SUPPORT_TICKETS_FILE)
