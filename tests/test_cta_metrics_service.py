import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from ai_crypto_index.accounts import models as account_models
from ai_crypto_index.accounts.db import ensure_schema, get_sessionmaker
from ai_crypto_index.api import dependencies as api_dependencies
from ai_crypto_index.shared import cta_analytics_store
from ai_crypto_index.shared.cta_metrics_service import CtaMetricsQuery, CtaMetricsService


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _run_with_session(settings, coro_fn):
    async def _runner():
        session_factory = await get_sessionmaker(settings)
        return await coro_fn(session_factory)

    return _run_async(_runner())


def _dt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _persist_click(
    settings,
    *,
    event_id: str,
    received_at: datetime,
    cta_id: str,
    location: str,
    unique_actor_id: str,
    event_type: str = "cta_click",
    cta_format: str = "unknown",
    metadata: dict[str, object] | None = None,
) -> None:
    cta_analytics_store.persist_cta_analytics_record(
        settings,
        {
            "event_id": event_id,
            "received_at": received_at.isoformat(),
            "event_type": event_type,
            "cta_id": cta_id,
            "cta_format": cta_format,
            "location": location,
            "location_raw": location,
            "href": "/pricing",
            "referer": "https://example.test/",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "unique_actor_id": unique_actor_id,
            "metadata": metadata or {},
        },
    )


@pytest.fixture
def metrics_settings(tmp_path, monkeypatch):
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
            "expected_files": ["weights.csv", "perf.json", "equity_curve.csv"],
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

    monkeypatch.setenv("AI_CRYPTO_CONFIG", str(config_path))
    monkeypatch.setenv("AICI_AUTH_DATABASE_URL", auth_db_url)
    monkeypatch.setenv("AICI_PERFORMANCE_AUTO_ENABLED", "0")
    monkeypatch.setenv("AICI_DAILY_SNAPSHOT_ENABLED", "0")
    monkeypatch.setenv("AICI_ENABLE_PIPELINE", "0")
    monkeypatch.setenv("AICI_EMAIL_ENABLED", "0")
    monkeypatch.setenv("AICI_CTA_FACT_RETENTION_DAYS", "9999")
    api_dependencies.get_settings.cache_clear()

    settings = api_dependencies.get_settings()
    _run_async(ensure_schema(settings))
    cta_analytics_store.ensure_cta_analytics_schema(settings)
    yield settings
    api_dependencies.get_settings.cache_clear()


def test_cta_metrics_service_dashboard(metrics_settings):
    settings = metrics_settings
    account_one = uuid4()
    account_two = uuid4()

    click_one = _dt(2026, 2, 1, 10, 0)
    click_one_repeat = _dt(2026, 2, 1, 10, 15)
    click_session = _dt(2026, 2, 1, 10, 30)
    click_fingerprint = _dt(2026, 2, 1, 10, 45)
    click_two = _dt(2026, 2, 2, 9, 0)
    signup_one = _dt(2026, 2, 1, 11, 0)
    confirmed_one = _dt(2026, 2, 1, 12, 0)
    signup_two = _dt(2026, 2, 2, 12, 0)
    confirmed_two = _dt(2026, 2, 2, 13, 0)
    paid_one = _dt(2026, 2, 2, 15, 0)

    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=click_one,
        cta_id="landing_start_free_plan_hero",
        location="hero",
        unique_actor_id=f"account:{account_one}",
        metadata={"account_id": str(account_one)},
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=click_one_repeat,
        cta_id="landing_start_free_plan_hero",
        location="hero",
        unique_actor_id=f"account:{account_one}",
        metadata={"account_id": str(account_one)},
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=click_session,
        cta_id="landing_start_free_plan_hero",
        location="hero",
        unique_actor_id="session:sess_1",
        metadata={"session_id": "sess_1"},
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=click_fingerprint,
        cta_id="landing_start_free_plan_hero",
        location="hero",
        unique_actor_id="fingerprint:abc123",
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=click_two,
        cta_id="pricing_choose_pro_plan_card",
        location="pricing",
        unique_actor_id=f"account:{account_two}",
        metadata={"account_id": str(account_two)},
    )

    async def _seed(session_factory):
        async with session_factory() as session:
            session.add_all(
                [
                    account_models.Account(
                        id=account_one,
                        email="account_one@example.com",
                        full_name="Account One",
                        hashed_password=None,
                        status=account_models.AccountStatus.ACTIVE,
                        newsletter_opt_in=False,
                        created_at=signup_one,
                        email_verified_at=confirmed_one,
                        updated_at=signup_one,
                    ),
                    account_models.Account(
                        id=account_two,
                        email="account_two@example.com",
                        full_name="Account Two",
                        hashed_password=None,
                        status=account_models.AccountStatus.ACTIVE,
                        newsletter_opt_in=False,
                        created_at=signup_two,
                        email_verified_at=confirmed_two,
                        updated_at=signup_two,
                    ),
                    account_models.BillingEvent(
                        provider_event_id=f"paid-{uuid4().hex}",
                        provider=account_models.BillingProvider.STRIPE,
                        event_type="checkout.session.completed",
                        account_id=account_one,
                        payload={"source": "test"},
                        processed_at=paid_one,
                        created_at=paid_one,
                        updated_at=paid_one,
                    ),
                ]
            )
            await session.commit()

    _run_with_session(settings, _seed)

    async def _collect(session_factory):
        service = CtaMetricsService(settings)
        async with session_factory() as session:
            return await service.build_dashboard(
                session,
                CtaMetricsQuery(
                    start_at=_dt(2026, 2, 1, 0, 0),
                    end_at=_dt(2026, 2, 4, 0, 0),
                ),
                interval="day",
                breakdown_limit=10,
            )

    dashboard = _run_with_session(settings, _collect)
    kpi = dashboard["kpi"]

    assert kpi["total_clicks"] == 5
    assert kpi["unique_clicks"] == 4
    assert kpi["unique_users"] == 2
    assert kpi["unique_sessions"] == 1
    assert kpi["unique_anonymous"] == 1
    assert kpi["attribution_coverage"] == 0.5
    assert kpi["rates"] == {
        "ctr": 0.8,
        "signup_cr": 1.0,
        "confirm_cr": 1.0,
        "paid_cr": 0.5,
    }
    assert kpi["attribution"] == {
        "model": "last_click",
        "lookback_days": 7,
        "identity_priority": ["account_id", "session_id", "fingerprint"],
    }
    observability = kpi["observability"]
    assert observability["expected_slots"] == 72
    assert observability["active_slots"] == 2
    assert observability["missing_slots"] == 70
    assert observability["missing_ratio"] == round(70 / 72, 4)
    assert observability["total_events"] == 0
    assert observability["invalid_events"] == 0
    assert observability["duplicate_events"] == 0
    assert observability["invalid_ratio"] == 0.0
    assert isinstance(observability["aggregation_lag_seconds"], int) or observability["aggregation_lag_seconds"] is None

    service_state = kpi["service_state"]
    last_accepted = service_state["last_accepted_event"]
    assert last_accepted["cta_id"] == "pricing_choose_pro_plan_card"
    assert last_accepted["location"] == "pricing"
    assert last_accepted["received_at"].startswith("2026-02-02T09:00:00")
    last_aggregated = service_state["last_aggregated_slot"]
    assert last_aggregated["event_hour"].startswith("2026-02-02T09:00:00")
    assert last_aggregated["event_date"] == "2026-02-02"

    conversion = kpi["conversion"]
    assert conversion["click_users"] == 2
    assert conversion["signup_users"] == 2
    assert conversion["confirmed_users"] == 2
    assert conversion["paid_users"] == 1
    assert conversion["click_to_signup"] == 1.0
    assert conversion["click_to_confirmed"] == 1.0
    assert conversion["signup_to_confirmed"] == 1.0
    assert conversion["confirmed_to_paid"] == 0.5
    assert conversion["signup_to_paid"] == 0.5
    assert conversion["click_to_paid"] == 0.5

    points = {point["bucket"]: point for point in dashboard["timeseries"]}
    assert set(points) == {"2026-02-01", "2026-02-02"}
    assert points["2026-02-01"]["total_clicks"] == 4
    assert points["2026-02-01"]["unique_clicks"] == 3
    assert points["2026-02-01"]["unique_users"] == 1
    assert points["2026-02-01"]["unique_sessions"] == 1
    assert points["2026-02-01"]["conversion"]["confirmed_users"] == 1
    assert points["2026-02-01"]["conversion"]["paid_users"] == 1
    assert points["2026-02-01"]["rates"] == {
        "ctr": 0.75,
        "signup_cr": 1.0,
        "confirm_cr": 1.0,
        "paid_cr": 1.0,
    }

    assert points["2026-02-02"]["total_clicks"] == 1
    assert points["2026-02-02"]["unique_clicks"] == 1
    assert points["2026-02-02"]["unique_users"] == 1
    assert points["2026-02-02"]["unique_sessions"] == 0
    assert points["2026-02-02"]["conversion"]["confirmed_users"] == 1
    assert points["2026-02-02"]["conversion"]["paid_users"] == 0
    assert points["2026-02-02"]["rates"] == {
        "ctr": 1.0,
        "signup_cr": 1.0,
        "confirm_cr": 1.0,
        "paid_cr": 0.0,
    }

    breakdown = {
        (item["cta_id"], item["cta_format"], item["location"], item["page_path"], item["utm_source"]): item
        for item in dashboard["breakdown"]
    }
    hero_row = breakdown[("landing_start_free_plan_hero", "unknown", "hero", "/pricing", "")]
    pricing_row = breakdown[("pricing_choose_pro_plan_card", "unknown", "pricing", "/pricing", "")]

    assert hero_row["total_clicks"] == 4
    assert hero_row["unique_clicks"] == 3
    assert hero_row["unique_users"] == 1
    assert hero_row["unique_sessions"] == 1
    assert hero_row["conversion"]["click_users"] == 1
    assert hero_row["conversion"]["signup_users"] == 1
    assert hero_row["conversion"]["confirmed_users"] == 1
    assert hero_row["conversion"]["paid_users"] == 1
    assert hero_row["rates"] == {
        "ctr": 0.75,
        "signup_cr": 1.0,
        "confirm_cr": 1.0,
        "paid_cr": 1.0,
    }

    assert pricing_row["total_clicks"] == 1
    assert pricing_row["unique_clicks"] == 1
    assert pricing_row["unique_users"] == 1
    assert pricing_row["unique_sessions"] == 0
    assert pricing_row["conversion"]["click_users"] == 1
    assert pricing_row["conversion"]["signup_users"] == 1
    assert pricing_row["conversion"]["confirmed_users"] == 1
    assert pricing_row["conversion"]["paid_users"] == 0
    assert pricing_row["rates"] == {
        "ctr": 1.0,
        "signup_cr": 1.0,
        "confirm_cr": 1.0,
        "paid_cr": 0.0,
    }


def test_cta_metrics_service_filters(metrics_settings):
    settings = metrics_settings
    account_id = uuid4()

    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 3, 9, 0),
        cta_id="landing_start_free_plan_hero",
        location="hero",
        unique_actor_id=f"account:{account_id}",
        metadata={"account_id": str(account_id), "utm_source": "ads"},
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 3, 9, 5),
        cta_id="pricing_choose_pro_plan_card",
        location="pricing",
        unique_actor_id="session:sess_filter",
        metadata={"session_id": "sess_filter", "utm_source": "organic"},
    )

    async def _seed(session_factory):
        async with session_factory() as session:
            session.add(
                account_models.Account(
                    id=account_id,
                    email="filter_account@example.com",
                    full_name="Filter Account",
                    hashed_password=None,
                    status=account_models.AccountStatus.ACTIVE,
                    newsletter_opt_in=False,
                    created_at=_dt(2026, 2, 3, 10, 0),
                    updated_at=_dt(2026, 2, 3, 10, 0),
                )
            )
            await session.commit()

    _run_with_session(settings, _seed)

    async def _collect_filtered(session_factory):
        service = CtaMetricsService(settings)
        async with session_factory() as session:
            return await service.build_dashboard(
                session,
                CtaMetricsQuery(
                    start_at=_dt(2026, 2, 3, 0, 0),
                    end_at=_dt(2026, 2, 4, 0, 0),
                    cta_ids=("landing_start_free_plan_hero",),
                    cta_types=("start_free_plan",),
                    locations=("hero",),
                    traffic_sources=("ads",),
                ),
                interval="day",
                breakdown_limit=10,
            )

    filtered = _run_with_session(settings, _collect_filtered)
    kpi = filtered["kpi"]
    assert kpi["total_clicks"] == 1
    assert kpi["unique_clicks"] == 1
    assert kpi["unique_users"] == 1
    assert kpi["unique_sessions"] == 0
    assert len(filtered["breakdown"]) == 1
    assert filtered["breakdown"][0]["cta_id"] == "landing_start_free_plan_hero"
    assert filtered["breakdown"][0]["location"] == "hero"
    assert filtered["breakdown"][0]["cta_format"] == "unknown"
    assert filtered["breakdown"][0]["page_path"] == "/pricing"
    assert filtered["breakdown"][0]["utm_source"] == "ads"


def test_cta_metrics_service_attributes_session_clicks_via_signup_bridge(metrics_settings):
    settings = metrics_settings
    account_id = uuid4()
    click_at = _dt(2026, 2, 4, 9, 0)
    signup_at = _dt(2026, 2, 4, 9, 10)
    confirmed_at = _dt(2026, 2, 4, 9, 20)

    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=click_at,
        cta_id="pricing_start_free_plan_topbar",
        location="pricing",
        unique_actor_id="session:sess_bridge",
        metadata={"session_id": "sess_bridge", "page_path": "/pricing"},
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=signup_at,
        event_type="signup_started",
        cta_id="pricing_start_free_plan_topbar",
        location="signup_modal",
        unique_actor_id=f"account:{account_id}",
        metadata={
            "account_id": str(account_id),
            "session_id": "sess_bridge",
            "source_cta_id": "pricing_start_free_plan_topbar",
            "page_path": "/pricing",
        },
    )

    async def _seed(session_factory):
        async with session_factory() as session:
            session.add(
                account_models.Account(
                    id=account_id,
                    email="bridge_account@example.com",
                    full_name="Bridge Account",
                    hashed_password=None,
                    status=account_models.AccountStatus.ACTIVE,
                    newsletter_opt_in=False,
                    created_at=signup_at,
                    email_verified_at=confirmed_at,
                    updated_at=confirmed_at,
                )
            )
            await session.commit()

    _run_with_session(settings, _seed)

    async def _collect(session_factory):
        service = CtaMetricsService(settings)
        async with session_factory() as session:
            return await service.build_dashboard(
                session,
                CtaMetricsQuery(
                    start_at=_dt(2026, 2, 4, 0, 0),
                    end_at=_dt(2026, 2, 5, 0, 0),
                    page_paths=("/pricing",),
                ),
                interval="day",
                breakdown_limit=10,
            )

    dashboard = _run_with_session(settings, _collect)
    kpi = dashboard["kpi"]

    assert kpi["total_clicks"] == 1
    assert kpi["unique_clicks"] == 1
    assert kpi["unique_users"] == 0
    assert kpi["unique_sessions"] == 1
    assert kpi["attribution_coverage"] == 1.0
    assert kpi["conversion"]["click_users"] == 1
    assert kpi["conversion"]["signup_users"] == 1
    assert kpi["conversion"]["confirmed_users"] == 1
    assert kpi["conversion"]["paid_users"] == 0


def test_weekly_cta_format_optimization_applies_statuses_and_logs(metrics_settings):
    settings = metrics_settings
    decision_time = _dt(2026, 2, 8, 12, 0)

    # button: 4 clicks / 3 unique (ctr=0.75), 2 signups (signup_cr=0.6667)
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 7, 10, 0),
        cta_id="landing_button_1",
        cta_format="button",
        location="hero",
        unique_actor_id="session:btn_1",
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 7, 10, 5),
        cta_id="landing_button_2",
        cta_format="button",
        location="hero",
        unique_actor_id="session:btn_1",
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 7, 10, 10),
        cta_id="landing_button_3",
        cta_format="button",
        location="hero",
        unique_actor_id="session:btn_2",
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 7, 10, 15),
        cta_id="landing_button_4",
        cta_format="button",
        location="hero",
        unique_actor_id="session:btn_3",
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 7, 10, 20),
        event_type="signup_started",
        cta_id="landing_button_signup_1",
        cta_format="button",
        location="hero",
        unique_actor_id="session:btn_1",
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 7, 10, 25),
        event_type="signup_started",
        cta_id="landing_button_signup_2",
        cta_format="button",
        location="hero",
        unique_actor_id="session:btn_2",
    )

    # card: 2 clicks / 1 unique (ctr=0.5), 1 signup (signup_cr=1.0)
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 7, 11, 0),
        cta_id="pricing_card_1",
        cta_format="card",
        location="pricing",
        unique_actor_id="session:card_1",
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 7, 11, 5),
        cta_id="pricing_card_2",
        cta_format="card",
        location="pricing",
        unique_actor_id="session:card_1",
    )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 7, 11, 10),
        event_type="signup_started",
        cta_id="pricing_card_signup_1",
        cta_format="card",
        location="pricing",
        unique_actor_id="session:card_1",
    )

    # banner: 3 clicks / 3 unique (ctr=1.0), 1 signup (signup_cr=0.3333)
    for index in range(3):
        _persist_click(
            settings,
            event_id=f"evt-{uuid4().hex}",
            received_at=_dt(2026, 2, 7, 12, index * 5),
            cta_id=f"docs_banner_{index}",
            cta_format="banner",
            location="docs",
            unique_actor_id=f"session:banner_{index}",
        )
    _persist_click(
        settings,
        event_id=f"evt-{uuid4().hex}",
        received_at=_dt(2026, 2, 7, 12, 20),
        event_type="signup_started",
        cta_id="docs_banner_signup",
        cta_format="banner",
        location="docs",
        unique_actor_id="session:banner_0",
    )

    # modal: 5 clicks / 5 unique (ctr=1.0), 0 signups initially
    for index in range(5):
        _persist_click(
            settings,
            event_id=f"evt-{uuid4().hex}",
            received_at=_dt(2026, 2, 7, 13, index * 3),
            cta_id=f"app_modal_{index}",
            cta_format="modal",
            location="app",
            unique_actor_id=f"session:modal_{index}",
        )

    first_decision = cta_analytics_store.run_weekly_cta_format_optimization(
        settings,
        now=decision_time,
        window_days=7,
        top_n=3,
    )
    assert first_decision["top_formats"] == ["card", "button", "banner"]
    assert first_decision["changed_formats"] == 3

    status_map = {
        item["cta_format"]: item
        for item in cta_analytics_store.list_cta_format_statuses(settings)
    }
    assert status_map["card"]["status"] == "active"
    assert status_map["button"]["status"] == "active"
    assert status_map["banner"]["status"] == "active"
    assert status_map["modal"]["status"] == "paused"

    # Raise modal signup_cr to move it into top-3 and push banner out.
    for index in range(3):
        _persist_click(
            settings,
            event_id=f"evt-{uuid4().hex}",
            received_at=_dt(2026, 2, 8, 10, index * 4),
            event_type="signup_started",
            cta_id=f"app_modal_signup_{index}",
            cta_format="modal",
            location="app",
            unique_actor_id=f"session:modal_{index}",
        )

    second_decision = cta_analytics_store.run_weekly_cta_format_optimization(
        settings,
        now=_dt(2026, 2, 8, 13, 0),
        window_days=7,
        top_n=3,
    )
    assert second_decision["top_formats"] == ["card", "button", "modal"]

    changes_map = {
        item["cta_format"]: item
        for item in second_decision["status_changes"]
    }
    assert changes_map["modal"]["previous_status"] == "paused"
    assert changes_map["modal"]["new_status"] == "active"
    assert changes_map["banner"]["previous_status"] == "active"
    assert changes_map["banner"]["new_status"] == "paused"

    decisions = cta_analytics_store.list_cta_format_optimization_decisions(
        settings,
        since=_dt(2026, 2, 1, 0, 0),
        limit=10,
    )
    assert len(decisions) == 2
    assert decisions[0]["top_formats"] == ["card", "button", "modal"]
    assert decisions[1]["top_formats"] == ["card", "button", "banner"]
