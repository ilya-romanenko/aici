import os
import time
import uuid

import httpx
import pytest

try:
    from ai_crypto_index.api.app import API_BASE_PATH
except ModuleNotFoundError as exc:
    if getattr(exc, "name", None) == "fastapi":
        API_BASE_PATH = "/api/v1"
    else:
        raise

pytestmark = pytest.mark.container

_base_url = os.getenv("AICI_SMOKE_BASE_URL")
if not _base_url:
    pytest.skip(
        "AICI_SMOKE_BASE_URL env var must be set for container smoke tests",
        allow_module_level=True,
    )

BASE_URL = _base_url.rstrip("/")
POLL_TIMEOUT = float(os.getenv("AICI_SMOKE_TIMEOUT", "60"))
POLL_INTERVAL = max(0.5, float(os.getenv("AICI_SMOKE_POLL_INTERVAL", "2")))
API_BASE = API_BASE_PATH


@pytest.fixture(scope="session")
def http_client():
    timeout = httpx.Timeout(10.0, connect=5.0)
    transport = httpx.HTTPTransport(retries=3)
    with httpx.Client(base_url=BASE_URL, timeout=timeout, transport=transport) as client:
        yield client


@pytest.fixture(scope="session", autouse=True)
def wait_for_service(http_client):
    deadline = time.monotonic() + POLL_TIMEOUT
    last_error = None
    while time.monotonic() < deadline:
        try:
            response = http_client.get(f"{API_BASE}/health")
            if response.status_code == 200:
                return
            last_error = AssertionError(f"Unexpected status {response.status_code}")
        except (httpx.HTTPError, AssertionError) as exc:
            last_error = exc
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"API was not ready within {POLL_TIMEOUT:.0f}s: {last_error}")


def test_health_endpoint_reports_ready(http_client):
    response = http_client.get(f"{API_BASE}/health")
    response.raise_for_status()
    payload = response.json()
    assert payload.get("status") == "ok"
    assert "timestamp" in payload


def test_landing_page_contains_meta(http_client):
    response = http_client.get("/")
    response.raise_for_status()
    body = response.text
    assert '<meta property="og:title"' in body
    assert '<meta name="twitter:card" content="summary_large_image">' in body
    assert '"@type": "Organization"' in body


def test_demo_request_submission(http_client):
    payload = {
        "name": "CI Smoke",
        "email": f"ci-smoke-{uuid.uuid4().hex[:8]}@example.com",
        "company": "CI Runner",
        "role": "QA",
        "team_size": "1-5",
        "use_case": "Continuous verification of container deployments.",
        "newsletter_opt_in": False,
        "terms_accepted": True,
    }
    response = http_client.post(f"{API_BASE}/demo-request", json=payload)
    response.raise_for_status()
    result = response.json()
    assert result.get("request_id")
    assert result.get("received_at")
