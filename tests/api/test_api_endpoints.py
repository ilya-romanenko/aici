import asyncio
import base64
import json
import os
import time
import warnings
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from ai_crypto_index.accounts import models as account_models
from ai_crypto_index.accounts.db import get_sessionmaker

os.environ["AICI_LOG_LEVEL"] = "WARNING"
os.environ["AICI_PERFORMANCE_AUTO_ENABLED"] = "0"
os.environ["AICI_DAILY_SNAPSHOT_ENABLED"] = "0"
os.environ["AICI_EMAIL_ENABLED"] = "0"
os.environ["AICI_ENABLE_PIPELINE"] = "0"

warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"ai_crypto_index\.api\.app")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"fastapi\..*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"starlette\.templating")


def _load_api_dependencies():
    from ai_crypto_index.api import dependencies as api_dependencies
    from ai_crypto_index.api.app import API_BASE_PATH, _INDEX_AUTO_PREFIX, create_app

    return api_dependencies, API_BASE_PATH, create_app, _INDEX_AUTO_PREFIX
api_dependencies, API_BASE_PATH, create_app, INDEX_AUTO_PREFIX = _load_api_dependencies()

API_BASE = API_BASE_PATH
pytestmark = [
    pytest.mark.filterwarnings("ignore::DeprecationWarning:ai_crypto_index.api.app"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning:fastapi"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning:starlette.templating"),
]

warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"ai_crypto_index\.api\.app")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"fastapi\..*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"starlette\.templating")


def _build_config(runs_root: Path, data_root: Path) -> dict:
    return {
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
            "expected_files": ["weights.csv", "perf.json", "equity_curve.csv"],
        },
        "auth": {
            "database_url": f"sqlite+aiosqlite:///{(runs_root / 'auth.db').as_posix()}",
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


@contextmanager
def _test_client(tmp_path, monkeypatch, extra_env: dict[str, str] | None = None):
    runs_root = tmp_path / "runs"
    data_root = tmp_path / "data"
    runs_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    config_path = tmp_path / "pipeline.json"
    config = _build_config(runs_root, data_root)
    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setenv("AI_CRYPTO_CONFIG", str(config_path))
    monkeypatch.setenv("AICI_AUTH_DATABASE_URL", config["auth"]["database_url"])
    monkeypatch.setenv("AICI_AUTH_DEBUG_TOKENS", "1")
    monkeypatch.setenv("AICI_AUTH_SESSION_COOKIE", config["auth"]["session_cookie_name"])
    monkeypatch.setenv("AICI_AUTH_SESSION_DOMAIN", "")
    monkeypatch.setenv("AICI_AUTH_SESSION_SECURE", "0")
    monkeypatch.setenv("AICI_ADMIN_ENABLED", "0")
    monkeypatch.setenv("AICI_RATE_LIMIT", "3")
    monkeypatch.setenv("AICI_RATE_LIMIT_WINDOW", "60")
    monkeypatch.setenv("AICI_SIGNUP_RATE_LIMIT", "1000")
    monkeypatch.setenv("AICI_SIGNUP_RATE_WINDOW", "60")
    monkeypatch.setenv("AICI_RESEND_RATE_LIMIT", "1000")
    monkeypatch.setenv("AICI_RESEND_RATE_WINDOW", "60")
    if extra_env:
        for key, value in extra_env.items():
            monkeypatch.setenv(key, value)

    api_dependencies.get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            yield client, runs_root
    finally:
        api_dependencies.get_settings.cache_clear()


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    with _test_client(tmp_path, monkeypatch) as client:
        yield client


def _write_weights_fixture(run_dir: Path, *, mtime: float | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    weights_path = run_dir / "weights.csv"
    weights_path.write_text("asset,weight\nBTC,0.6\nETH,0.4\n", encoding="utf-8")
    ts = mtime if mtime is not None else time.time() - 90_000
    os.utime(weights_path, (ts, ts))
    os.utime(run_dir, (ts, ts))


def _provision_api_key(client, create_payload: dict[str, object] | None = None) -> dict[str, str]:
    unique_suffix = uuid4().hex[:6]
    email = f"tester_{unique_suffix}@example.com"
    signup_payload = {
        "email": email,
        "password": "StrongPassword!123",
        "newsletter_opt_in": False,
        "terms_version": "2024-10",
    }
    signup_response = client.post(f"{API_BASE}/auth/signup", json=signup_payload)
    assert signup_response.status_code == 201
    confirmation_token = signup_response.json()["debug_confirmation_token"]
    confirm_response = client.post(
        f"{API_BASE}/auth/confirm",
        json={"token": confirmation_token},
    )
    assert confirm_response.status_code == 200
    access_token = confirm_response.json()["access_token"]
    key_request = {"label": f"CI key {unique_suffix}"}
    if create_payload:
        key_request.update(create_payload)
    key_response = client.post(
        f"{API_BASE}/keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json=key_request,
    )
    assert key_response.status_code == 201
    payload = key_response.json()
    return {"secret": payload["secret"], "access_token": access_token, "key": payload["key"], "email": email}


def _run_in_session(settings, coro_fn):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        session_factory = loop.run_until_complete(get_sessionmaker(settings))
        return loop.run_until_complete(coro_fn(session_factory))
    finally:
        loop.close()


def test_health_endpoint_reports_status(api_client):
    client, runs_root = api_client

    response = client.get(f"{API_BASE}/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["rate_limit"]["limit"] == 3
    assert Path(payload["data_paths"]["runs"]) == runs_root


def test_performance_endpoint_returns_snapshots(api_client):
    client, _ = api_client
    api_key = _provision_api_key(client)

    response = client.get(f"{API_BASE}/performance", headers={"X-API-Key": api_key["secret"]})
    assert response.status_code == 200

    payload = response.json()
    assert payload["default_key"]
    assert payload["snapshots"]
    sample_key, sample_snapshot = next(iter(payload["snapshots"].items()))
    assert "chart_paths" in sample_snapshot
    assert "metric_cards" in sample_snapshot
    assert sample_snapshot["strategy_key"] == sample_key
    assert "live_backtest" in payload
    assert "live_backtest_by_strategy" in payload
    assert isinstance(payload["live_backtest_by_strategy"], dict)
    if payload["live_backtest"] is not None:
        assert "live_series" in payload["live_backtest"]
        assert "backtest_series" in payload["live_backtest"]
        assert "fees_included" in payload["live_backtest"]
        assert "slippage_included" in payload["live_backtest"]
    for strategy_key in payload["snapshots"].keys():
        assert strategy_key in payload["live_backtest_by_strategy"]


def test_index_composition_endpoint(api_client):
    client, runs_root = api_client
    _write_weights_fixture(runs_root / "2024-01-01-test")

    api_key = _provision_api_key(client)

    response = client.get(f"{API_BASE}/index-composition", headers={"X-API-Key": api_key["secret"]})
    assert response.status_code == 200

    payload = response.json()
    assert payload["run_id"]
    assert payload["assets"]
    top_asset = payload["assets"][0]
    assert top_asset["asset"] == "BTC"
    assert payload["summary"]["count"] == 2
    assert "live_backtest" in payload
    assert "live_backtest_by_strategy" in payload
    assert "monthly_snapshots" in payload
    assert "monthly_live_snapshots" in payload
    assert "monthly_backtest_snapshots" in payload
    assert "monthly_snapshots_updated_at" in payload
    assert "monthly_snapshots_current_month" in payload
    assert "monthly_snapshots_default_strategy" in payload
    assert "monthly_snapshots_by_strategy" in payload
    assert "monthly_live_snapshots_by_strategy" in payload
    assert "monthly_backtest_snapshots_by_strategy" in payload
    assert "monthly_snapshots_updated_at_by_strategy" in payload
    assert "monthly_snapshots_current_month_by_strategy" in payload
    assert isinstance(payload["monthly_snapshots_by_strategy"], dict)


def test_index_composition_missing_returns_404(api_client):
    client, _ = api_client
    api_key = _provision_api_key(client)

    response = client.get(f"{API_BASE}/index-composition", headers={"X-API-Key": api_key["secret"]})
    assert response.status_code == 404
    assert response.json()["detail"]


def test_weights_latest_prefers_user_run_over_auto(api_client):
    client, runs_root = api_client
    api_key = _provision_api_key(client)
    settings = api_dependencies.get_settings()
    latency = settings.api_keys.plans[settings.api_keys.default_plan_code].data_latency_seconds
    now_ts = time.time()

    user_run_id = "2025-01-02T00-00-00Z-user"
    auto_run_id = f"{INDEX_AUTO_PREFIX}-2025-01-03T00-00-00Z"
    _write_weights_fixture(runs_root / user_run_id, mtime=now_ts - (latency + 3000))
    _write_weights_fixture(runs_root / auto_run_id, mtime=now_ts - (latency + 1000))

    async def _seed_runs(session_factory):
        async with session_factory() as session:
            account = (
                await session.execute(
                    select(account_models.Account).where(account_models.Account.email == api_key["email"])
                )
            ).scalar_one()
            api_key_row = (
                await session.execute(
                    select(account_models.ApiKey).where(account_models.ApiKey.account_id == account.id)
                )
            ).scalar_one()
            session.add_all(
                [
                    account_models.IndexRun(
                        run_id=user_run_id,
                        source=account_models.IndexRunSource.USER,
                        account_id=account.id,
                        api_key_id=api_key_row.id,
                    ),
                    account_models.IndexRun(
                        run_id=auto_run_id,
                        source=account_models.IndexRunSource.AUTO,
                    ),
                ]
            )
            await session.commit()

    _run_in_session(settings, _seed_runs)

    response = client.get(f"{API_BASE}/weights/latest", headers={"X-API-Key": api_key["secret"]})
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == user_run_id
    assert payload["items"][0]["asset"] == "BTC"


def test_weights_latest_falls_back_to_auto_when_user_locked(api_client):
    client, runs_root = api_client
    api_key = _provision_api_key(client)
    settings = api_dependencies.get_settings()
    default_latency = settings.api_keys.plans[settings.api_keys.default_plan_code].data_latency_seconds
    enforced_latency = max(default_latency, 60)
    now_ts = time.time()

    user_run_id = "2025-01-06T00-00-00Z-user"
    auto_run_id = f"{INDEX_AUTO_PREFIX}-2025-01-07T00-00-00Z"
    _write_weights_fixture(runs_root / user_run_id, mtime=now_ts - (enforced_latency / 4))
    _write_weights_fixture(runs_root / auto_run_id, mtime=now_ts - (enforced_latency + 600))

    async def _seed_runs(session_factory):
        async with session_factory() as session:
            account = (
                await session.execute(
                    select(account_models.Account).where(account_models.Account.email == api_key["email"])
                )
            ).scalar_one()
            api_key_row = (
                await session.execute(
                    select(account_models.ApiKey).where(account_models.ApiKey.account_id == account.id)
                )
            ).scalar_one()
            api_key_row.data_latency_override = enforced_latency
            session.add_all(
                [
                    account_models.IndexRun(
                        run_id=user_run_id,
                        source=account_models.IndexRunSource.USER,
                        account_id=account.id,
                        api_key_id=api_key_row.id,
                    ),
                    account_models.IndexRun(
                        run_id=auto_run_id,
                        source=account_models.IndexRunSource.AUTO,
                    ),
                ]
            )
            await session.commit()

    _run_in_session(settings, _seed_runs)

    response = client.get(f"{API_BASE}/weights/latest", headers={"X-API-Key": api_key["secret"]})
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == auto_run_id
    assert payload["items"][0]["asset"] == "BTC"


def test_rate_limit_is_enforced(api_client):
    client, _ = api_client

    for _ in range(3):
        ok_response = client.get(f"{API_BASE}/health")
        assert ok_response.status_code == 200

    limited = client.get(f"{API_BASE}/health")
    assert limited.status_code == 429
    assert limited.json()["detail"] == "rate_limit_exceeded"


def test_swagger_docs_require_basic_auth(tmp_path, monkeypatch):
    extra_env = {
        "AICI_SWAGGER_ENABLED": "1",
        "AICI_SWAGGER_USERNAME": "docs-user",
        "AICI_SWAGGER_PASSWORD": "docs-pass",
    }
    with _test_client(tmp_path, monkeypatch, extra_env=extra_env) as (client, _):
        unauthorized = client.get("/api/docs")
        assert unauthorized.status_code == 401
        assert unauthorized.headers.get("www-authenticate", "").lower().startswith("basic")

        credentials = base64.b64encode(b"docs-user:docs-pass").decode("ascii")
        headers = {"Authorization": f"Basic {credentials}"}

        docs_response = client.get("/api/docs", headers=headers)
        assert docs_response.status_code == 200
        assert "Swagger UI" in docs_response.text

        schema_response = client.get("/api/openapi.json", headers=headers)
        assert schema_response.status_code == 200
        assert schema_response.json()["openapi"].startswith("3.")


def test_legacy_version_path_redirects_to_api_namespace(api_client):
    client, _ = api_client
    api_key = _provision_api_key(client)

    response = client.get("/v1/performance", headers={"X-API-Key": api_key["secret"]}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/api/v1/performance")

    followed = client.get("/v1/performance", headers={"X-API-Key": api_key["secret"]})
    assert followed.status_code == 200


def test_legacy_version_path_preserves_query_params(api_client):
    client, runs_root = api_client
    _write_weights_fixture(runs_root / "2024-01-01-test")
    api_key = _provision_api_key(client)

    response = client.get(
        "/v1/index-composition?limit=5",
        headers={"X-API-Key": api_key["secret"]},
        follow_redirects=False,
    )
    assert response.status_code == 307
    assert response.headers["location"].endswith("/api/v1/index-composition?limit=5")


def test_auth_signup_confirm_and_profile(api_client):
    client, _ = api_client
    signup_payload = {
        "email": "evelyn@example.com",
        "password": "VerySecure!123",
        "newsletter_opt_in": True,
        "terms_version": "2024-10",
    }
    signup_response = client.post(f"{API_BASE}/auth/signup", json=signup_payload)
    assert signup_response.status_code == 201
    signup_data = signup_response.json()
    assert signup_data["next_step"] == "confirm_email"
    confirmation_token = signup_data["debug_confirmation_token"]
    assert confirmation_token

    confirm_response = client.post(
        f"{API_BASE}/auth/confirm",
        json={"token": confirmation_token},
    )
    assert confirm_response.status_code == 200
    confirm_data = confirm_response.json()
    assert confirm_data["profile"]["email"] == signup_payload["email"]
    access_token = confirm_data["access_token"]
    assert access_token
    assert client.cookies.get("test_session")

    profile_update = {
        "full_name": "Evelyn Ops",
        "job_title": "Head of Ops",
        "organization_name": "Ops Labs",
        "organization_size": "11-50",
        "use_case": "Need automated index construction for investor portal.",
    }
    update_response = client.patch(
        f"{API_BASE}/auth/profile",
        headers={"Authorization": f"Bearer {access_token}"},
        json=profile_update,
    )
    assert update_response.status_code == 200
    update_payload = update_response.json()
    assert update_payload["full_name"] == profile_update["full_name"]
    assert update_payload["organization"]["name"] == profile_update["organization_name"]

    profile_response = client.get(
        f"{API_BASE}/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert profile_response.status_code == 200
    profile_payload = profile_response.json()
    assert profile_payload["email"] == signup_payload["email"]


def test_api_key_lifecycle_and_revocation(api_client):
    client, _ = api_client
    bundle = _provision_api_key(client)
    token = bundle["access_token"]

    list_response = client.get(
        f"{API_BASE}/keys",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_response.status_code == 200
    key_payload = list_response.json()
    assert key_payload["keys"]
    key_id = key_payload["keys"][0]["id"]

    update_response = client.patch(
        f"{API_BASE}/keys/{key_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "label": "Updated key label",
            "application_name": "Risk engine",
            "tags": ["alpha", "beta"],
            "ip_allowlist": ["198.51.100.0/24"],
            "label_constraints": ["risk"],
        },
    )
    assert update_response.status_code == 200
    updated_key = update_response.json()
    assert updated_key["label"] == "Updated key label"
    assert updated_key["ip_allowlist"] == ["198.51.100.0/24"]
    assert updated_key["label_constraints"] == ["risk"]

    rotate_response = client.post(
        f"{API_BASE}/keys/{key_id}/rotate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rotate_response.status_code == 200
    rotated_secret = rotate_response.json()["secret"]

    revoke_response = client.post(
        f"{API_BASE}/keys/{key_id}/revoke",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "test cleanup"},
    )
    assert revoke_response.status_code == 200
    assert revoke_response.json()["status"] == "revoked"

    activity_response = client.get(
        f"{API_BASE}/keys/{key_id}/activity",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert activity_response.status_code == 200
    events = activity_response.json()["events"]
    assert events, "audit log should contain events"
    assert any(event["event_type"] == "issued" for event in events)

    forbidden = client.get(
        f"{API_BASE}/performance",
        headers={"X-API-Key": rotated_secret},
    )
    assert forbidden.status_code == 403


def test_api_key_restrictions_enforced(api_client):
    client, _ = api_client
    restriction_payload = {
        "ip_allowlist": ["203.0.113.0/24"],
        "label_constraints": ["prod"],
    }
    bundle = _provision_api_key(client, create_payload=restriction_payload)
    secret = bundle["secret"]

    allowed_headers = {
        "X-API-Key": secret,
        "X-Forwarded-For": "203.0.113.10",
        "X-AICI-Label": "prod",
    }
    allowed = client.get(f"{API_BASE}/performance", headers=allowed_headers)
    assert allowed.status_code != 403

    blocked_ip = client.get(
        f"{API_BASE}/performance",
        headers={
            "X-API-Key": secret,
            "X-Forwarded-For": "198.51.100.5",
            "X-AICI-Label": "prod",
        },
    )
    assert blocked_ip.status_code == 403

    blocked_label = client.get(
        f"{API_BASE}/performance",
        headers={
            "X-API-Key": secret,
            "X-Forwarded-For": "203.0.113.20",
            "X-AICI-Label": "staging",
        },
    )
    assert blocked_label.status_code == 403


def test_login_requires_confirmation(api_client):
    client, _ = api_client
    signup_payload = {
        "email": "pending@example.com",
        "password": "Password!12345",
        "newsletter_opt_in": False,
        "terms_version": "2024-10",
    }
    signup_response = client.post(f"{API_BASE}/auth/signup", json=signup_payload)
    assert signup_response.status_code == 201

    login_response = client.post(
        f"{API_BASE}/auth/login",
        json={"email": signup_payload["email"], "password": signup_payload["password"]},
    )
    assert login_response.status_code == 403
    assert login_response.json()["detail"] == "account_pending_activation"


def test_login_succeeds_for_active_account(api_client):
    client, _ = api_client
    signup_payload = {
        "email": "active@example.com",
        "password": "Password!12345",
        "newsletter_opt_in": False,
        "terms_version": "2024-10",
    }
    signup_response = client.post(f"{API_BASE}/auth/signup", json=signup_payload)
    assert signup_response.status_code == 201
    confirmation_token = signup_response.json()["debug_confirmation_token"]
    confirm_response = client.post(
        f"{API_BASE}/auth/confirm",
        json={"token": confirmation_token},
    )
    assert confirm_response.status_code == 200

    client.cookies.clear()
    login_response = client.post(
        f"{API_BASE}/auth/login",
        json={"email": signup_payload["email"], "password": signup_payload["password"]},
    )
    assert login_response.status_code == 200
    login_payload = login_response.json()
    assert login_payload["access_token"]
    assert login_payload["profile"]["email"] == signup_payload["email"]


def test_usage_summary_and_alerts(api_client):
    client, _ = api_client
    bundle = _provision_api_key(client)
    token = bundle["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    summary_response = client.get(f"{API_BASE}/usage/summary", headers=headers)
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert summary_payload["points"], "usage summary should include points"

    errors_response = client.get(f"{API_BASE}/usage/errors", headers=headers)
    assert errors_response.status_code == 200

    export_response = client.get(f"{API_BASE}/usage/export", headers=headers)
    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith("text/csv")

    alerts_payload = {
        "alerts": [
            {
                "channel_type": "email",
                "destination": "alerts@example.com",
                "label": "Ops",
                "threshold_percent": 80,
                "enabled": True,
            }
        ]
    }
    upsert_response = client.put(f"{API_BASE}/usage/alerts", headers=headers, json=alerts_payload)
    assert upsert_response.status_code == 200
    alerts_list = upsert_response.json()["alerts"]
    assert alerts_list and alerts_list[0]["destination"] == "alerts@example.com"

    fetch_alerts = client.get(f"{API_BASE}/usage/alerts", headers=headers)
    assert fetch_alerts.status_code == 200
    fetch_payload = fetch_alerts.json()
    assert len(fetch_payload["alerts"]) == 1


def test_token_usage_headers_and_pipeline_cost(tmp_path, monkeypatch):
    captured_calls: list[dict[str, object]] = []

    def _stub_run_monthly_update(*args, **kwargs):
        captured_calls.append(dict(kwargs))
        return {"BTC": 0.6, "ETH": 0.4}, {"return": 0.12}

    monkeypatch.setattr("ai_crypto_index.api.app.run_monthly_update", _stub_run_monthly_update)
    extra_env = {
        "AICI_ENABLE_PIPELINE": "1",
        "AICI_RATE_LIMIT": "10",
        "AICI_RATE_LIMIT_WINDOW": "60",
    }
    with _test_client(tmp_path, monkeypatch, extra_env=extra_env) as (client, _):
        bundle = _provision_api_key(client)
        secret = bundle["secret"]
        bearer = bundle["access_token"]

        perf_response = client.get(f"{API_BASE}/performance", headers={"X-API-Key": secret})
        assert perf_response.status_code == 200
        assert perf_response.headers.get("X-API-Request-Tokens") == "1"
        assert int(perf_response.headers["X-API-Usage-Daily"]) == 1
        assert int(perf_response.headers["X-API-Usage-Monthly"]) == 1
        assert int(perf_response.headers["X-API-Quota-Monthly"]) >= 1

        run_response = client.post(
            f"{API_BASE}/run",
            headers={"X-API-Key": secret},
            json={},
        )
        assert run_response.status_code == 201
        run_id = run_response.json()["run_id"]
        assert run_id
        assert run_response.headers.get("X-API-Request-Tokens") == "152"
        assert int(run_response.headers["X-API-Usage-Daily"]) == 153
        assert int(run_response.headers["X-API-Usage-Monthly"]) == 153

        summary_response = client.get(f"{API_BASE}/usage/summary", headers={"Authorization": f"Bearer {bearer}"})
        assert summary_response.status_code == 200
        summary_payload = summary_response.json()
        totals = summary_payload["totals"]
        assert totals["unit"] == "tokens"
        assert totals["monthly_usage"] == 153
        assert totals["monthly_quota"] is None or totals["monthly_quota"] >= 153
        assert summary_payload["points"], "usage summary should return data points"
        last_point = summary_payload["points"][-1]
        assert last_point["call_count"] == 153
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            settings = api_dependencies.get_settings()
            run_call = next(call for call in captured_calls if str(call.get("run_id")) == run_id)
            assert Path(str(run_call["config_path"])) == settings.config_path
            session_factory = loop.run_until_complete(get_sessionmaker(settings))

            async def _fetch_usage_events():
                async with session_factory() as session:
                    rows = await session.execute(
                        select(
                            account_models.ApiUsageEvent.route_name,
                            account_models.ApiUsageEvent.request_cost,
                            account_models.ApiUsageEvent.status_code,
                        ).order_by(account_models.ApiUsageEvent.created_at)
                    )
                    return rows.all()

            events = loop.run_until_complete(_fetch_usage_events())
        finally:
            loop.close()

        assert any(name == "api_get_performance" and cost == 1 for name, cost, status_code in events)
        assert any(name == "api_trigger_run" and cost == 152 for name, cost, status_code in events)
        assert all(status_code is not None for _, _, status_code in events)


def test_run_async_rejects_start_date_before_2021(tmp_path, monkeypatch):
    extra_env = {
        "AICI_ENABLE_PIPELINE": "1",
        "AICI_RATE_LIMIT": "10",
        "AICI_RATE_LIMIT_WINDOW": "60",
    }
    with _test_client(tmp_path, monkeypatch, extra_env=extra_env) as (client, _):
        bundle = _provision_api_key(client)
        response = client.post(
            f"{API_BASE}/run/async?start_date=2020-12-31",
            headers={"X-API-Key": bundle["secret"]},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "start_date cannot be earlier than 2021-01-01"


def test_resend_confirmation_endpoint(api_client):
    client, _ = api_client
    signup_payload = {
        "email": "resend@example.com",
        "password": "Password!12345",
        "newsletter_opt_in": False,
        "terms_version": "2024-10",
    }
    signup_response = client.post(f"{API_BASE}/auth/signup", json=signup_payload)
    assert signup_response.status_code == 201

    resend_response = client.post(
        f"{API_BASE}/auth/confirm/resend",
        json={"email": signup_payload["email"]},
    )
    assert resend_response.status_code == 200
    assert "confirmation" in resend_response.json()["message"].lower()

def test_admin_endpoints_require_auth(api_client):
    client, _ = api_client
    response = client.get(f"{API_BASE}/admin/accounts")
    assert response.status_code == 403

