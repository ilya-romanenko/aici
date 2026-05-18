import base64
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ai_crypto_index.api import dependencies as api_dependencies
from ai_crypto_index.api.app import API_BASE_PATH, create_app
from ai_crypto_index.shared import cta_analytics_store

os.environ["AICI_LOG_LEVEL"] = "WARNING"
os.environ["AICI_PERFORMANCE_AUTO_ENABLED"] = "0"
os.environ["AICI_DAILY_SNAPSHOT_ENABLED"] = "0"
os.environ["AICI_EMAIL_ENABLED"] = "0"
os.environ["AICI_ENABLE_PIPELINE"] = "0"

API_BASE = API_BASE_PATH


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
def _admin_test_client(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    data_root = tmp_path / "data"
    runs_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    config_path = tmp_path / "pipeline.json"
    config_path.write_text(json.dumps(_build_config(runs_root, data_root)), encoding="utf-8")

    monkeypatch.setenv("AI_CRYPTO_CONFIG", str(config_path))
    monkeypatch.setenv("AICI_AUTH_DATABASE_URL", f"sqlite+aiosqlite:///{(runs_root / 'auth.db').as_posix()}")
    monkeypatch.setenv("AICI_ADMIN_ENABLED", "1")
    monkeypatch.setenv("AICI_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("AICI_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("AICI_RATE_LIMIT", "100")
    monkeypatch.setenv("AICI_RATE_LIMIT_WINDOW", "60")
    monkeypatch.setenv("AICI_SIGNUP_RATE_LIMIT", "1000")
    monkeypatch.setenv("AICI_SIGNUP_RATE_WINDOW", "60")
    monkeypatch.setenv("AICI_RESEND_RATE_LIMIT", "1000")
    monkeypatch.setenv("AICI_RESEND_RATE_WINDOW", "60")
    monkeypatch.setenv("AICI_CTA_FORMAT_OPTIMIZATION_ENABLED", "0")

    api_dependencies.get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            yield client
    finally:
        api_dependencies.get_settings.cache_clear()


def _admin_auth_headers() -> dict[str, str]:
    encoded = base64.b64encode(b"admin:secret").decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def test_admin_cta_analytics_endpoints_support_filters_pagination_and_csv(tmp_path, monkeypatch):
    with _admin_test_client(tmp_path, monkeypatch) as client:
        ingestion_headers = {"Referer": "https://google.com/search?q=aici"}
        base_payload = {
            "cta_id": "landing_start_free_plan_hero",
            "location": "hero",
            "href": "https://example.test/pricing?utm_source=ads",
            "metadata": {
                "page_path": "/pricing",
                "auth_state": "authenticated",
                "cta_format": "button",
                "utm_campaign": "launch",
            },
        }
        first = dict(base_payload)
        first["metadata"] = {**base_payload["metadata"], "session_id": "sess_1"}
        second = dict(base_payload)
        second["metadata"] = {**base_payload["metadata"], "session_id": "sess_2"}
        duplicate = dict(base_payload)
        duplicate["metadata"] = {**base_payload["metadata"], "session_id": "sess_1"}

        third = {
            "cta_id": "pricing_choose_pro_plan_card",
            "location": "pricing",
            "href": "https://example.test/pricing?utm_source=organic",
            "metadata": {
                "page_path": "/pricing",
                "auth_state": "anonymous",
                "cta_format": "card",
                "session_id": "sess_3",
                "utm_campaign": "other",
            },
        }

        for payload in (first, second, third, duplicate):
            response = client.post(f"{API_BASE}/events/cta", json=payload, headers=ingestion_headers)
            assert response.status_code == 201

        settings = api_dependencies.get_settings()
        decision = cta_analytics_store.run_weekly_cta_format_optimization(settings, top_n=3, window_days=7)
        assert decision["id"] > 0

        now = datetime.now(timezone.utc)
        params = [
            ("start_at", (now - timedelta(days=1)).isoformat()),
            ("end_at", (now + timedelta(days=1)).isoformat()),
            ("page", "/pricing"),
            ("placement", "hero"),
            ("cta_id", "landing_start_free_plan_hero"),
            ("cta_format", "button"),
            ("utm_source", "ads"),
            ("auth_state", "authenticated"),
            ("referrer", "google"),
            ("utm", "ads"),
        ]
        auth_headers = _admin_auth_headers()

        summary_response = client.get(
            f"{API_BASE}/admin/cta-analytics/dashboard/summary",
            params=params,
            headers=auth_headers,
        )
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert summary["total_clicks"] == 2
        assert summary["unique_clicks"] == 2
        assert summary["unique_sessions"] == 2
        assert summary["period"]["lookback_days"] == 7
        assert summary["rates"]["ctr"] == 1.0
        assert summary["rates"]["signup_cr"] == 0.0
        assert summary["rates"]["confirm_cr"] == 0.0
        assert summary["rates"]["paid_cr"] == 0.0
        assert summary["attribution"]["model"] == "last_click"
        assert summary["attribution"]["lookback_days"] == 7
        assert summary["attribution"]["identity_priority"] == ["account_id", "session_id", "fingerprint"]
        assert "observability" in summary
        assert "service_state" in summary
        assert summary["observability"]["total_events"] == 4
        assert summary["observability"]["invalid_events"] == 0
        assert summary["observability"]["duplicate_events"] == 1
        assert summary["observability"]["expected_slots"] >= 48
        assert summary["service_state"]["last_accepted_event"]["event_id"]
        assert summary["service_state"]["last_aggregated_slot"]["event_hour"] is not None

        timeseries_response = client.get(
            f"{API_BASE}/admin/cta-analytics/timeseries",
            params=[*params, ("interval", "hour"), ("page_number", 1), ("page_size", 1)],
            headers=auth_headers,
        )
        assert timeseries_response.status_code == 200
        timeseries = timeseries_response.json()
        assert timeseries["interval"] == "hour"
        assert timeseries["pagination"]["page"] == 1
        assert timeseries["pagination"]["page_size"] == 1
        assert timeseries["pagination"]["total_items"] >= 1
        assert len(timeseries["items"]) == 1

        top_response = client.get(
            f"{API_BASE}/admin/cta-analytics/top-cta",
            params=[*params, ("page_number", 1), ("page_size", 1)],
            headers=auth_headers,
        )
        assert top_response.status_code == 200
        top_payload = top_response.json()
        assert top_payload["pagination"]["total_items"] == 1
        assert top_payload["items"][0]["cta_id"] == "landing_start_free_plan_hero"

        breakdown_response = client.get(
            f"{API_BASE}/admin/cta-analytics/breakdown",
            params=[*params, ("page_number", 1), ("page_size", 1)],
            headers=auth_headers,
        )
        assert breakdown_response.status_code == 200
        breakdown_payload = breakdown_response.json()
        assert breakdown_payload["pagination"]["total_items"] == 1
        assert breakdown_payload["items"][0]["location"] == "hero"
        assert breakdown_payload["items"][0]["cta_format"] == "button"
        assert breakdown_payload["items"][0]["page_path"] == "/pricing"
        assert breakdown_payload["items"][0]["utm_source"] == "ads"

        funnel_response = client.get(
            f"{API_BASE}/admin/cta-analytics/funnel",
            params=params,
            headers=auth_headers,
        )
        assert funnel_response.status_code == 200
        funnel_payload = funnel_response.json()
        assert funnel_payload["total_clicks"] == 2
        assert set(funnel_payload["conversion"].keys()) == {
            "click_users",
            "signup_users",
            "confirmed_users",
            "paid_users",
            "click_to_signup",
            "click_to_confirmed",
            "signup_to_confirmed",
            "confirmed_to_paid",
            "signup_to_paid",
            "click_to_paid",
        }
        assert set(funnel_payload["rates"].keys()) == {"ctr", "signup_cr", "confirm_cr", "paid_cr"}
        assert funnel_payload["attribution"]["lookback_days"] == 7

        decisions_response = client.get(
            f"{API_BASE}/admin/cta-analytics/format-decisions",
            params={"days": 7, "limit": 10},
            headers=auth_headers,
        )
        assert decisions_response.status_code == 200
        decisions_payload = decisions_response.json()
        assert decisions_payload["period_days"] == 7
        assert len(decisions_payload["items"]) >= 1
        assert len(decisions_payload["current_statuses"]) >= 1
        assert decisions_payload["items"][0]["top_formats"]
        assert "status_changes" in decisions_payload["items"][0]

        for dataset in ("summary", "funnel", "timeseries", "breakdown"):
            export_response = client.get(
                f"{API_BASE}/admin/cta-analytics/export",
                params=[*params, ("dataset", dataset), ("interval", "hour")],
                headers=auth_headers,
            )
            assert export_response.status_code == 200
            assert export_response.headers["content-type"].startswith("text/csv")
            if dataset == "breakdown":
                csv_body = export_response.text
                assert "cta_id,cta_format,location,page_path,utm_source,total_clicks" in csv_body
                assert "landing_start_free_plan_hero,button,hero,/pricing,ads,2" in csv_body


def test_admin_cta_analytics_requires_basic_auth(tmp_path, monkeypatch):
    with _admin_test_client(tmp_path, monkeypatch) as client:
        response = client.get(f"{API_BASE}/admin/cta-analytics/dashboard/summary")
        assert response.status_code == 401


def test_admin_cta_analytics_page_renders_with_navigation(tmp_path, monkeypatch):
    with _admin_test_client(tmp_path, monkeypatch) as client:
        response = client.get("/admin/cta-analytics", headers=_admin_auth_headers())
        assert response.status_code == 200
        assert "CTA Analytics" in response.text
        assert "href=\"/admin/moderation\"" in response.text
        assert "data-filter-placement" in response.text
        assert "data-filter-page" in response.text
        assert "data-filter-utm-source" in response.text
        assert "data-filter-cta-format" in response.text
        assert "data-filter-cta-id" in response.text
        assert "data-export-dataset=\"summary\"" in response.text
        assert "data-export-dataset=\"funnel\"" in response.text
        assert "data-export-dataset=\"timeseries\"" in response.text
        assert "data-export-dataset=\"breakdown\"" in response.text
        assert "data-format-decisions-body" in response.text
        assert "data-format-statuses-note" in response.text
        assert "Решения за 7 дней" in response.text
        assert "data-page-slice=\"/pricing\"" in response.text
        assert "data-page-slice=\"/docs\"" in response.text
        assert "data-page-slice=\"/\"" in response.text
        assert "Landing/Home" in response.text
