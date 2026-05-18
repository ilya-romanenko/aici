import asyncio
import json
import os
import re
import sqlite3
import time
import warnings
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ai_crypto_index.accounts import models as account_models
from ai_crypto_index.accounts.db import get_sessionmaker
from ai_crypto_index.api import dependencies as api_dependencies
from ai_crypto_index.api.app import API_BASE_PATH, _INDEX_AUTO_PREFIX, create_app
from ai_crypto_index.shared import cta_analytics_store, intake_store

API_BASE = API_BASE_PATH
warnings.simplefilter("ignore", DeprecationWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"ai_crypto_index\.api\.app")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"fastapi\..*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"starlette\.templating")
warnings.filterwarnings("ignore", message=r".*on_event is deprecated.*", category=DeprecationWarning)
pytestmark = [
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning:ai_crypto_index.api.app"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning:fastapi"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning:starlette.templating"),
    pytest.mark.filterwarnings("ignore:.*on_event is deprecated.*:DeprecationWarning"),
]


@pytest.fixture
def frontend_client(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    data_root = tmp_path / "data"
    runs_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    auth_db_url = f"sqlite+aiosqlite:///{(runs_root / 'auth.db').as_posix()}"
    config = {
        "data": {
            "root": str(data_root),
            "min_history_days": 365,
            "allow_internal_gaps": False,
            "include_delisted": False,
            "dropna_all": True,
        },
        "market_data": {
            "provider": "stub",
            "top_n": 10,
            "start_date": "2024-01-01",
            "fresh_download": False,
        },
        "runs": {
            "root": str(runs_root),
            "expected_files": ["weights.csv", "perf.json", "equity_curve.csv", "log.txt"],
        },
        "auth": {
            "database_url": auth_db_url,
            "jwt_secret_key": "test-secret",
            "jwt_algorithm": "HS256",
            "access_token_ttl_seconds": 3600,
            "refresh_token_ttl_seconds": 86400,
            "email_token_ttl_seconds": 86400,
            "password_reset_ttl_seconds": 3600,
            "session_cookie_name": "test_session",
            "session_cookie_secure": False,
            "session_cookie_domain": None,
            "public_app_url": "https://app.test",
            "expose_tokens_in_responses": True,
            "echo_sql": False,
        },
    }

    monkeypatch.setenv("AICI_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("AICI_PERFORMANCE_AUTO_ENABLED", "0")
    monkeypatch.setenv("AICI_DAILY_SNAPSHOT_ENABLED", "0")
    monkeypatch.setenv("AICI_ENABLE_PIPELINE", "0")
    monkeypatch.setenv("AICI_EMAIL_ENABLED", "0")
    monkeypatch.setenv("AICI_AUTH_DEBUG_TOKENS", "1")
    monkeypatch.setenv("AICI_AUTH_DATABASE_URL", auth_db_url)
    monkeypatch.setenv("AICI_AUTH_SESSION_COOKIE", "test_session")
    monkeypatch.setenv("AICI_AUTH_SESSION_DOMAIN", "")
    monkeypatch.setenv("AICI_AUTH_SESSION_SECURE", "0")
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("AI_CRYPTO_CONFIG", str(config_path))
    api_dependencies.get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        yield client, runs_root

    api_dependencies.get_settings.cache_clear()

def _run_with_session(settings, coro_fn):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        session_factory = loop.run_until_complete(get_sessionmaker(settings))
        return loop.run_until_complete(coro_fn(session_factory))
    finally:
        loop.close()


def _write_weights_artifact(run_dir: Path, *, mtime: float) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    weights_path = run_dir / "weights.csv"
    weights_path.write_text("asset,weight\nBTC,0.6\nETH,0.4\n", encoding="utf-8")
    os.utime(weights_path, (mtime, mtime))
    os.utime(run_dir, (mtime, mtime))


def _create_account_with_key(client: TestClient) -> dict[str, str]:
    email = f"playground_{uuid4().hex[:6]}@example.com"
    signup_payload = {
        "email": email,
        "password": "StrongPassword!123",
        "newsletter_opt_in": False,
        "terms_version": "2024-10",
    }
    signup_response = client.post(f"{API_BASE}/auth/signup", json=signup_payload)
    assert signup_response.status_code == 201
    confirmation_token = signup_response.json()["debug_confirmation_token"]

    confirm_response = client.post(f"{API_BASE}/auth/confirm", json={"token": confirmation_token})
    assert confirm_response.status_code == 200
    access_token = confirm_response.json()["access_token"]

    key_response = client.post(
        f"{API_BASE}/keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"label": "Playground key"},
    )
    assert key_response.status_code == 201
    key_payload = key_response.json()
    return {
        "email": email,
        "secret": key_payload["secret"],
        "access_token": access_token,
        "key_id": key_payload["key"]["id"],
    }


def _extract_playground_config(body: str) -> dict:
    match = re.search(r"data-playground-config>\s*({.*?})\s*</script>", body, flags=re.S)
    assert match, "playground config not found in HTML"
    return json.loads(match.group(1))


def test_landing_page_contains_seo_and_accessibility(frontend_client):
    client, _ = frontend_client
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert '<meta property="og:title"' in body
    assert '<meta name="twitter:card" content="summary_large_image">' in body
    assert '<link rel="canonical" href="http://testserver/">' in body
    assert '"@type": "Organization"' in body
    assert f'data-cta-endpoint="{API_BASE}/events/cta"' in body


def test_landing_live_backtest_flow_is_sequential(frontend_client):
    client, _ = frontend_client
    response = client.get("/")
    assert response.status_code == 200
    body = response.text

    required_markers = [
        "data-performance-live-since",
        "data-performance-backtest-window",
        "data-performance-cost-assumptions",
        "data-performance-chart",
        "data-performance-composition-root",
        "data-performance-composition-strategy",
    ]
    marker_positions = []
    for marker in required_markers:
        position = body.find(marker)
        assert position >= 0, f"Missing marker in landing page: {marker}"
        marker_positions.append(position)
    assert marker_positions == sorted(marker_positions)

    assert "Live since" in body
    assert "Backtest window" in body
    assert "Fees &amp; slippage assumptions" in body
    assert "Equity curve" in body
    assert "Index composition by snapshot month" in body


def test_pricing_page_contains_public_plan_matrix(frontend_client):
    client, _ = frontend_client
    response = client.get("/pricing")
    assert response.status_code == 200
    body = response.text
    assert "Choose a plan on this page" in body
    assert "Token model:" in body
    assert "data reads (weights/perf/export) cost 5 tokens per call" in body
    assert "Free" in body
    assert "Pro" in body
    assert "Ultra" in body
    assert "Enterprise" in body
    assert "Pricing matrix" in body


def test_pricing_page_guest_ctas_open_registration_modal(frontend_client):
    client, _ = frontend_client
    response = client.get("/pricing")
    assert response.status_code == 200
    body = response.text

    assert 'data-modal="registration"' in body
    assert re.search(
        r'<a[^>]*data-cta-id="pricing_start_free_plan_topbar"(?=[^>]*data-modal-trigger="registration")[^>]*>',
        body,
    )
    assert re.search(
        r'<a[^>]*data-cta-id="pricing_start_free_plan_footer"(?=[^>]*data-modal-trigger="registration")[^>]*>',
        body,
    )
    assert re.search(
        r'<a[^>]*(?=[^>]*data-cta-id="pricing_start_free_plan_topbar")(?=[^>]*href="#registration-modal")[^>]*>',
        body,
    )
    assert re.search(
        r'<a[^>]*(?=[^>]*data-cta-id="pricing_start_free_plan_footer")(?=[^>]*href="#registration-modal")[^>]*>',
        body,
    )

    for cta_id in (
        "pricing_start_free_plan_card",
        "pricing_choose_pro_plan_card",
        "pricing_choose_ultra_plan_card",
        "pricing_contact_sales_card",
    ):
        assert re.search(
            rf'<a[^>]*(?=[^>]*data-cta-id="{cta_id}")(?=[^>]*data-modal-trigger="registration")[^>]*>',
            body,
        )
        assert re.search(
            rf'<a[^>]*(?=[^>]*data-cta-id="{cta_id}")(?=[^>]*href="#registration-modal")[^>]*>',
            body,
        )


def test_pricing_page_authenticated_ctas_open_billing_and_profile(frontend_client):
    client, _ = frontend_client
    _create_account_with_key(client)

    response = client.get("/pricing")
    assert response.status_code == 200
    body = response.text

    assert 'data-cta-id="pricing_go_to_profile"' in body
    assert 'data-cta-id="pricing_go_to_profile_footer"' in body
    assert "Go to profile" in body
    assert 'data-modal="registration"' not in body
    assert 'data-cta-id="pricing_start_free_plan_footer"' not in body

    for cta_id in (
        "pricing_start_free_plan_card",
        "pricing_choose_pro_plan_card",
        "pricing_choose_ultra_plan_card",
        "pricing_contact_sales_card",
    ):
        assert re.search(
            rf'<a[^>]*(?=[^>]*data-cta-id="{cta_id}")(?=[^>]*href="http://testserver/app/billing")[^>]*>',
            body,
        )


def test_demo_request_intake(frontend_client):
    client, runs_root = frontend_client
    payload = {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "company": "Analytical Engines",
        "role": "Lead Researcher",
        "team_size": "6-15",
        "use_case": "We need to evaluate AI-managed crypto indices for quarterly reporting.",
        "newsletter_opt_in": True,
        "terms_accepted": True,
    }

    response = client.post(f"{API_BASE}/demo-request", json=payload)
    assert response.status_code == 201
    response_payload = response.json()
    assert "request_id" in response_payload
    assert "received_at" in response_payload

    intake_path = runs_root / intake_store.INTAKE_DIR_NAME / intake_store.DEMO_REQUESTS_FILE
    assert intake_path.exists()
    last_entry = json.loads(intake_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last_entry["email"] == payload["email"]
    assert last_entry["newsletter_opt_in"] is True


def test_cta_event_logging(frontend_client):
    client, runs_root = frontend_client

    response = client.post(
        f"{API_BASE}/events/cta",
        json={
            "cta_id": "landing_start_free_plan_hero",
            "event_type": "cta_click",
            "cta_format": " Button_Primary ",
            "page_path": " /Pricing ",
            "utm_source": " ADS ",
            "location": "hero-section",
            "href": "#contacts",
        },
        headers={"referer": "https://example.com/landing"},
    )
    assert response.status_code == 201
    event_payload = response.json()
    assert "event_id" in event_payload
    assert "received_at" in event_payload

    events_path = runs_root / intake_store.INTAKE_DIR_NAME / intake_store.CTA_EVENTS_FILE
    assert events_path.exists()
    stored_event = json.loads(events_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert stored_event["cta_id"] == "landing_start_free_plan_hero"
    assert stored_event["location"] == "hero-section"
    assert stored_event["href"] == "#contacts"
    assert stored_event["referer"] == "https://example.com/landing"

    analytics_path = runs_root / intake_store.INTAKE_DIR_NAME / intake_store.CTA_ANALYTICS_EVENTS_FILE
    assert analytics_path.exists()
    analytics_event = json.loads(analytics_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert analytics_event["event_id"] == event_payload["event_id"]
    assert analytics_event["event_type"] == "cta_click"
    assert analytics_event["cta_id"] == "landing_start_free_plan_hero"
    assert analytics_event["cta_format"] == "button_primary"
    assert analytics_event["location"] == "hero"
    assert analytics_event["page_path"] == "/pricing"
    assert analytics_event["utm_source"] == "ads"
    assert analytics_event["metadata"] == {}
    assert analytics_event["is_duplicate"] is False

    db_path = cta_analytics_store.resolve_cta_analytics_db_path_from_runs_root(runs_root)
    assert db_path.exists()
    with sqlite3.connect(db_path) as connection:
        fact = connection.execute(
            """
            SELECT cta_id, event_type, cta_format, location_norm, page_path, utm_source, unique_actor_id
            FROM cta_events_fact
            WHERE event_id = ?
            """,
            (event_payload["event_id"],),
        ).fetchone()
        assert fact is not None
        assert fact[0] == "landing_start_free_plan_hero"
        assert fact[1] == "cta_click"
        assert fact[2] == "button_primary"
        assert fact[3] == "hero"
        assert fact[4] == "/pricing"
        assert fact[5] == "ads"
        assert fact[6]

        hourly = connection.execute(
            "SELECT total_clicks, unique_clicks FROM cta_metrics_hourly WHERE cta_id = ?",
            ("landing_start_free_plan_hero",),
        ).fetchone()
        assert hourly == (1, 1)

        daily = connection.execute(
            "SELECT total_clicks, unique_clicks FROM cta_metrics_daily WHERE cta_id = ?",
            ("landing_start_free_plan_hero",),
        ).fetchone()
        assert daily == (1, 1)

        event_hourly = connection.execute(
            """
            SELECT total_events, unique_actors
            FROM cta_event_metrics_hourly
            WHERE cta_id = ? AND event_type = ? AND cta_format = ?
            """,
            ("landing_start_free_plan_hero", "cta_click", "button_primary"),
        ).fetchone()
        assert event_hourly == (1, 1)


def test_cta_event_analytics_dedup_and_metadata_normalization(frontend_client):
    client, runs_root = frontend_client
    payload = {
        "cta_id": "landing_start_free_plan_api",
        "event_type": "cta_click",
        "cta_format": " Banner ",
        "page_path": " /Pricing ",
        "utm_source": " ADS ",
        "location": "/pricing",
        "href": "https://example.com/pricing?utm_source=ads#plan",
        "metadata": {
            "session_id": "sess_123",
            "Auth State": " anonymous ",
            "Unsafe Key<>": " value ",
            "items": [1, " two ", True, {"drop": "nested"}],
            "nested": {"plan": "pro", "deep": {"ignore": "me"}},
        },
    }
    headers = {"referer": "https://example.com/landing"}

    first = client.post(f"{API_BASE}/events/cta", json=payload, headers=headers)
    second = client.post(f"{API_BASE}/events/cta", json=payload, headers=headers)
    third = client.post(
        f"{API_BASE}/events/cta",
        json={**payload, "event_type": "signup_started"},
        headers=headers,
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert third.status_code == 201
    assert first.json()["event_id"] != second.json()["event_id"]

    raw_path = runs_root / intake_store.INTAKE_DIR_NAME / intake_store.CTA_EVENTS_FILE
    raw_entries = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(raw_entries) == 3

    analytics_path = runs_root / intake_store.INTAKE_DIR_NAME / intake_store.CTA_ANALYTICS_EVENTS_FILE
    analytics_entries = [
        json.loads(line) for line in analytics_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(analytics_entries) == 2
    analytics_event = next(item for item in analytics_entries if item["event_type"] == "cta_click")
    assert analytics_event["location"] == "pricing"
    assert analytics_event["unique_actor_id"] == "session:sess_123"
    assert analytics_event["href"] == "https://example.com/pricing?utm_source=ads"
    assert analytics_event["cta_format"] == "banner"
    assert analytics_event["page_path"] == "/pricing"
    assert analytics_event["utm_source"] == "ads"
    assert analytics_event["is_duplicate"] is False

    normalized_metadata = analytics_event["metadata"]
    assert normalized_metadata["session_id"] == "sess_123"
    assert normalized_metadata["auth_state"] == "anonymous"
    assert normalized_metadata["unsafe_key"] == "value"
    assert normalized_metadata["items"] == [1, "two", True]
    assert normalized_metadata["nested"] == {"plan": "pro"}

    db_path = cta_analytics_store.resolve_cta_analytics_db_path_from_runs_root(runs_root)
    with sqlite3.connect(db_path) as connection:
        fact_count = connection.execute("SELECT COUNT(*) FROM cta_events_fact").fetchone()
        assert fact_count is not None
        assert fact_count[0] == 2

        hourly = connection.execute(
            """
            SELECT total_clicks, unique_clicks
            FROM cta_metrics_hourly
            WHERE cta_id = ? AND location_norm = ?
            """,
            ("landing_start_free_plan_api", "pricing"),
        ).fetchone()
        assert hourly == (1, 1)

        daily = connection.execute(
            """
            SELECT total_clicks, unique_clicks
            FROM cta_metrics_daily
            WHERE cta_id = ? AND location_norm = ?
            """,
            ("landing_start_free_plan_api", "pricing"),
        ).fetchone()
        assert daily == (1, 1)

        signup_metrics = connection.execute(
            """
            SELECT total_events, unique_actors
            FROM cta_event_metrics_hourly
            WHERE cta_id = ? AND event_type = ? AND cta_format = ?
            ORDER BY event_hour DESC
            LIMIT 1
            """,
            ("landing_start_free_plan_api", "signup_started", "banner"),
        ).fetchone()
        assert signup_metrics == (1, 1)


def test_cta_event_analytics_unique_actor_counters(frontend_client):
    client, runs_root = frontend_client
    base_payload = {
        "cta_id": "landing_start_free_plan_api",
        "location": "api_section",
        "href": "/pricing?utm_campaign=retargeting",
    }

    first = client.post(
        f"{API_BASE}/events/cta",
        json={**base_payload, "metadata": {"session_id": "sess_first", "auth_state": "anonymous"}},
    )
    second = client.post(
        f"{API_BASE}/events/cta",
        json={**base_payload, "metadata": {"session_id": "sess_second", "auth_state": "anonymous"}},
    )
    assert first.status_code == 201
    assert second.status_code == 201

    db_path = cta_analytics_store.resolve_cta_analytics_db_path_from_runs_root(runs_root)
    with sqlite3.connect(db_path) as connection:
        hourly = connection.execute(
            """
            SELECT total_clicks, unique_clicks
            FROM cta_metrics_hourly
            WHERE cta_id = ? AND location_norm = ?
            ORDER BY event_hour DESC
            LIMIT 1
            """,
            ("landing_start_free_plan_api", "api_section"),
        ).fetchone()
        assert hourly == (2, 2)

        daily = connection.execute(
            """
            SELECT total_clicks, unique_clicks
            FROM cta_metrics_daily
            WHERE cta_id = ? AND location_norm = ?
            ORDER BY event_date DESC
            LIMIT 1
            """,
            ("landing_start_free_plan_api", "api_section"),
        ).fetchone()
        assert daily == (2, 2)


def test_signup_records_account_attributed_cta_bridge_events(frontend_client):
    client, runs_root = frontend_client
    email = f"bridge_{uuid4().hex[:6]}@example.com"
    signup_payload = {
        "email": email,
        "password": "StrongPassword!123",
        "newsletter_opt_in": False,
        "terms_version": "2024-10",
        "cta_session_id": "sess_bridge_signup",
        "source_cta_id": "pricing_start_free_plan_topbar",
        "source_page_path": "/pricing",
        "source_scenario": "start_free_plan",
    }

    signup_response = client.post(
        f"{API_BASE}/auth/signup",
        json=signup_payload,
        headers={"referer": "http://testserver/pricing"},
    )
    assert signup_response.status_code == 201
    signup_data = signup_response.json()
    account_id = signup_data["account_id"]

    confirm_response = client.post(
        f"{API_BASE}/auth/confirm",
        json={"token": signup_data["debug_confirmation_token"]},
        headers={"referer": "http://testserver/confirm"},
    )
    assert confirm_response.status_code == 200

    db_path = cta_analytics_store.resolve_cta_analytics_db_path_from_runs_root(runs_root)
    with sqlite3.connect(db_path) as connection:
        signup_bridge = connection.execute(
            """
            SELECT event_type, cta_id, page_path, unique_actor_id
            FROM cta_events_fact
            WHERE event_type = ? AND unique_actor_id = ?
            ORDER BY received_at DESC
            LIMIT 1
            """,
            ("signup_started", f"account:{account_id}"),
        ).fetchone()
        assert signup_bridge == (
            "signup_started",
            "pricing_start_free_plan_topbar",
            "/pricing",
            f"account:{account_id}",
        )

        confirmed_bridge = connection.execute(
            """
            SELECT event_type, cta_id, unique_actor_id
            FROM cta_events_fact
            WHERE event_type = ? AND unique_actor_id = ?
            ORDER BY received_at DESC
            LIMIT 1
            """,
            ("email_confirmed", f"account:{account_id}"),
        ).fetchone()
        assert confirmed_bridge == (
            "email_confirmed",
            "email_confirmed",
            f"account:{account_id}",
        )


def test_static_assets_use_cdn_base(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    data_root = tmp_path / "data"
    runs_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    auth_db_url = f"sqlite+aiosqlite:///{(runs_root / 'auth.db').as_posix()}"
    config = {
        "data": {
            "root": str(data_root),
            "min_history_days": 365,
            "allow_internal_gaps": False,
            "include_delisted": False,
            "dropna_all": True,
        },
        "market_data": {
            "provider": "stub",
            "top_n": 10,
            "start_date": "2024-01-01",
            "fresh_download": False,
        },
        "runs": {
            "root": str(runs_root),
            "expected_files": ["weights.csv", "perf.json", "equity_curve.csv", "log.txt"],
        },
        "auth": {
            "database_url": auth_db_url,
            "jwt_secret_key": "test-secret",
            "jwt_algorithm": "HS256",
            "access_token_ttl_seconds": 3600,
            "refresh_token_ttl_seconds": 86400,
            "email_token_ttl_seconds": 86400,
            "password_reset_ttl_seconds": 3600,
            "session_cookie_name": "test_session",
            "session_cookie_secure": False,
            "session_cookie_domain": None,
            "public_app_url": "https://app.test",
            "expose_tokens_in_responses": True,
            "echo_sql": False,
        },
    }

    config_path = tmp_path / "pipeline.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    manifest_path = Path(__file__).resolve().parents[2] / "dist" / "asset-manifest.json"

    monkeypatch.setenv("AI_CRYPTO_CONFIG", str(config_path))
    monkeypatch.setenv("AICI_AUTH_DATABASE_URL", auth_db_url)
    monkeypatch.setenv("AICI_STATIC_CDN_BASE_URL", "https://cdn.example.com")
    monkeypatch.setenv("AICI_ASSET_MANIFEST_PATH", str(manifest_path))
    api_dependencies.get_settings.cache_clear()


def _resolve_asset_path(body: str, pattern: str, default_path: str) -> str:
    match = re.search(pattern, body, flags=re.IGNORECASE)
    if not match or not match.group(1):
        return default_path
    return match.group(1)


def _client_get_asset(client: TestClient, asset_href: str):
    parsed = urlparse(asset_href)
    if parsed.scheme:
        target = asset_href
    else:
        target = asset_href if asset_href.startswith("/") else f"/{asset_href}"
    return client.get(target)


def test_playground_prefills_latest_user_run(frontend_client):
    client, runs_root = frontend_client
    account = _create_account_with_key(client)
    settings = api_dependencies.get_settings()
    latency = settings.api_keys.plans[settings.api_keys.default_plan_code].data_latency_seconds
    now_ts = time.time()

    user_run_id = "2025-02-10T00-00-00Z-user"
    auto_run_id = f"{_INDEX_AUTO_PREFIX}-2025-02-11T00-00-00Z"
    _write_weights_artifact(runs_root / user_run_id, mtime=now_ts - (latency + 1800))
    _write_weights_artifact(runs_root / auto_run_id, mtime=now_ts - (latency + 600))

    async def _seed_runs(session_factory):
        async with session_factory() as session:
            account_row = (
                await session.execute(
                    select(account_models.Account).where(account_models.Account.email == account["email"])
                )
            ).scalar_one()
            api_key_row = (
                await session.execute(
                    select(account_models.ApiKey).where(account_models.ApiKey.account_id == account_row.id)
                )
            ).scalar_one()
            session.add_all(
                [
                    account_models.IndexRun(
                        run_id=user_run_id,
                        source=account_models.IndexRunSource.USER,
                        account_id=account_row.id,
                        api_key_id=api_key_row.id,
                    ),
                    account_models.IndexRun(
                        run_id=auto_run_id,
                        source=account_models.IndexRunSource.AUTO,
                    ),
                ]
            )
            await session.commit()

    _run_with_session(settings, _seed_runs)

    page = client.get("/app/playground")
    assert page.status_code == 200
    config = _extract_playground_config(page.text)
    assert config["latest_run_id"] == user_run_id


def test_playground_prefills_auto_when_user_locked(frontend_client):
    client, runs_root = frontend_client
    account = _create_account_with_key(client)
    settings = api_dependencies.get_settings()
    default_latency = settings.api_keys.plans[settings.api_keys.default_plan_code].data_latency_seconds
    enforced_latency = max(default_latency, 60)
    now_ts = time.time()

    user_run_id = "2025-02-20T00-00-00Z-user"
    auto_run_id = f"{_INDEX_AUTO_PREFIX}-2025-02-21T00-00-00Z"
    _write_weights_artifact(runs_root / user_run_id, mtime=now_ts - (enforced_latency / 5))
    _write_weights_artifact(runs_root / auto_run_id, mtime=now_ts - (enforced_latency + 900))

    async def _seed_runs(session_factory):
        async with session_factory() as session:
            account_row = (
                await session.execute(
                    select(account_models.Account).where(account_models.Account.email == account["email"])
                )
            ).scalar_one()
            api_key_row = (
                await session.execute(
                    select(account_models.ApiKey).where(account_models.ApiKey.account_id == account_row.id)
                )
            ).scalar_one()
            api_key_row.data_latency_override = enforced_latency
            session.add_all(
                [
                    account_models.IndexRun(
                        run_id=user_run_id,
                        source=account_models.IndexRunSource.USER,
                        account_id=account_row.id,
                        api_key_id=api_key_row.id,
                    ),
                    account_models.IndexRun(
                        run_id=auto_run_id,
                        source=account_models.IndexRunSource.AUTO,
                    ),
                ]
            )
            await session.commit()

    _run_with_session(settings, _seed_runs)

    page = client.get("/app/playground")
    assert page.status_code == 200
    config = _extract_playground_config(page.text)
    assert config["latest_run_id"] == auto_run_id


def test_playground_shows_advanced_forecast_and_meta_for_all_endpoints(frontend_client):
    client, _ = frontend_client
    _create_account_with_key(client)

    page = client.get("/app/playground")
    assert page.status_code == 200
    config = _extract_playground_config(page.text)

    endpoints = config.get("endpoints", [])
    assert endpoints

    run_pipeline = next((endpoint for endpoint in endpoints if endpoint.get("id") == "run-pipeline"), None)
    assert run_pipeline is not None
    run_fields = run_pipeline.get("fields", [])
    assert any(field.get("name") == "advanced_forecast" for field in run_fields)

    for endpoint in endpoints:
        meta_items = endpoint.get("meta_items")
        assert isinstance(meta_items, list)
        assert meta_items

    priced_get_endpoints = {"weights-latest", "run-weights", "run-perf"}
    for endpoint in endpoints:
        endpoint_id = endpoint.get("id")
        if endpoint_id not in priced_get_endpoints:
            continue
        meta_items = endpoint.get("meta_items") or []
        assert any("Request price: 5 tokens per call." in str(item) for item in meta_items)


def test_performance_tooltip_hover_smoke(frontend_client, monkeypatch):
    client, _ = frontend_client

    response = client.get("/")
    assert response.status_code == 200
    body = response.text

    assert 'data-performance-chart' in body
    assert 'data-performance-overlay' in body
    assert 'data-performance-tooltip' in body

    js_href = _resolve_asset_path(body, r'<script[^>]+src="([^"]*main\\.js[^"]*)"', "/static/js/main.js")
    css_href = _resolve_asset_path(body, r'<link[^>]+href="([^"]*main\\.css[^"]*)"', "/static/css/main.css")

    js_response = _client_get_asset(client, js_href)
    assert js_response.status_code == 200
    js_body = js_response.text
    assert "data-performance-tooltip" in js_body
    assert "pointerenter" in js_body
    assert "pointerleave" in js_body
    assert "dataset.active" in js_body

    css_response = _client_get_asset(client, css_href)
    assert css_response.status_code == 200
    css_body = css_response.text
    assert "[data-performance-overlay][data-active=\"true\"] .landing-performance__tooltip" in css_body
    assert ".landing-performance__tooltip" in css_body
    assert "const buildContinuousSeries = (baseSeries, liveContinuationSeries) => {" in js_body
    assert "const applyStrategyChart = (snapshot) => {" in js_body
    assert "const setCollapsedState = (collapsed) => {" in js_body
    assert "const buildModeChartDataset = (series, mode" in js_body
    assert "data-performance-composition-toggle" in body
    assert "data-performance-composition-strategy" in body

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        body = response.text
        assert re.search(r"https://cdn\.example\.com/static/css/main\.css\?v=[^\"']+", body)
        assert re.search(r"https://cdn\.example\.com/static/js/main\.js\?v=[^\"']+", body)
        assert re.search(r"https://cdn\.example\.com/static/icons/favicon\.svg\?v=[^\"']+", body)

    monkeypatch.delenv("AICI_STATIC_CDN_BASE_URL", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
    api_dependencies.get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/docs")
        assert response.status_code == 200
        body = response.text
        assert re.search(r"https?://testserver/static/css/main\.css\?v=[^\"']+", body)
        assert re.search(r"https?://testserver/static/css/normalize\.css\?v=[^\"']+", body)

    api_dependencies.get_settings.cache_clear()
