import asyncio
import hashlib
import hmac
import json
import os
import sys
import types
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

try:
    import tqdm as _tqdm  # noqa: F401
except ImportError:
    tqdm_stub = types.ModuleType("tqdm")

    def _noop_tqdm(iterable=None, *args, **kwargs):
        return iterable if iterable is not None else []

    tqdm_stub.tqdm = _noop_tqdm
    sys.modules["tqdm"] = tqdm_stub

os.environ.setdefault("AICI_PERFORMANCE_AUTO_ENABLED", "0")
os.environ.setdefault("AICI_ENABLE_PIPELINE", "0")
os.environ.setdefault("AICI_EMAIL_ENABLED", "0")
os.environ.setdefault("AICI_BILLING_REMINDERS_ENABLED", "0")
os.environ.setdefault("AICI_LOG_LEVEL", "WARNING")
os.environ.setdefault("AICI_ADMIN_ENABLED", "0")
os.environ.setdefault("AICI_PREFER_SRC_TEMPLATES", "1")

from ai_crypto_index.api import dependencies as api_dependencies
from ai_crypto_index.api.app import API_BASE_PATH, create_app
from ai_crypto_index.accounts import models as account_models
from ai_crypto_index.accounts.db import get_sessionmaker
from ai_crypto_index.billing.service import BillingService

API_BASE = API_BASE_PATH
SESSION_COOKIE_NAME = "test_session"

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


def _get_event_loop() -> asyncio.AbstractEventLoop:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop

    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


def _build_crypto_config(runs_root: Path, data_root: Path) -> dict[str, object]:
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
            "expected_files": ["weights.csv", "perf.json", "equity_curve.csv", "log.txt"],
        },
        "auth": {
            "database_url": f"sqlite+aiosqlite:///{(runs_root / 'auth.db').as_posix()}",
            "jwt_secret_key": "test-secret",
            "jwt_algorithm": "HS256",
            "access_token_ttl_seconds": 3600,
            "refresh_token_ttl_seconds": 86400,
            "email_token_ttl_seconds": 86400,
            "password_reset_ttl_seconds": 3600,
            "session_cookie_name": SESSION_COOKIE_NAME,
            "session_cookie_secure": False,
            "session_cookie_domain": None,
            "public_app_url": "https://app.test",
            "expose_tokens_in_responses": True,
            "echo_sql": False,
        },
        "billing": {
            "provider": "crypto",
            "currency": "usd",
            "trial_days": 0,
            "plans": {
                "pro": {
                    "code": "pro",
                    "name": "Pro",
                    "unit_amount_cents": 1200,
                    "currency": "usd",
                    "interval": "month",
                    "trial_days": 0,
                    "self_serve": True,
                    "features": ["Priority API access", "Crypto invoicing"],
                },
            },
            "crypto": {
                "provider": "nowpayments",
                "default_network": "usdt_trc20",
                "networks": {
                    "usdt_trc20": {
                        "code": "usdt_trc20",
                        "currency": "usdt",
                        "chain": "trc20",
                        "confirmations_required": 1,
                        "fee_percent": 0.5,
                    },
                },
                "usd_to_crypto_rate": 1.0,
                "service_fee_percent": 0.5,
            },
        },
    }


@pytest.fixture
def crypto_client(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    data_root = tmp_path / "data"
    runs_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    config = _build_crypto_config(runs_root, data_root)
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    webhook_secret = "crypto-test-secret"
    invoice_id = f"inv_{uuid4().hex[:10]}"
    hosted_url = f"https://pay.test/{invoice_id}"

    monkeypatch.setenv("AI_CRYPTO_CONFIG", str(config_path))
    monkeypatch.setenv("AICI_AUTH_DATABASE_URL", config["auth"]["database_url"])
    monkeypatch.setenv("AICI_AUTH_DEBUG_TOKENS", "1")
    monkeypatch.setenv("AICI_AUTH_SESSION_COOKIE", SESSION_COOKIE_NAME)
    monkeypatch.setenv("AICI_AUTH_SESSION_DOMAIN", "")
    monkeypatch.setenv("AICI_AUTH_SESSION_SECURE", "0")
    monkeypatch.setenv("AICI_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("AICI_PERFORMANCE_AUTO_ENABLED", "0")
    monkeypatch.setenv("AICI_ENABLE_PIPELINE", "0")
    monkeypatch.setenv("AICI_EMAIL_ENABLED", "0")
    monkeypatch.setenv("AICI_ADMIN_ENABLED", "0")
    monkeypatch.setenv("AICI_BILLING_REMINDERS_ENABLED", "0")
    monkeypatch.setenv("AICI_CRYPTO_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setenv("AICI_CRYPTO_API_KEY", "stub-api-key")
    monkeypatch.setenv("AICI_CRYPTO_API_SECRET", "stub-api-secret")

    async def fake_nowpayments(self, method: str, path: str, payload: dict, *, api_key: str) -> dict:
        assert method == "POST"
        assert path == "/invoice"
        return {
            "id": invoice_id,
            "invoice_id": invoice_id,
            "invoice_url": hosted_url,
            "pay_currency": payload.get("pay_currency", "usdt"),
            "price_amount": payload.get("price_amount"),
        }

    monkeypatch.setattr("ai_crypto_index.billing.service.BillingService._call_nowpayments", fake_nowpayments)

    api_dependencies.get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        yield client, {
            "webhook_secret": webhook_secret,
            "invoice_id": invoice_id,
            "hosted_url": hosted_url,
        }
    api_dependencies.get_settings.cache_clear()


def _signup_and_confirm(client: TestClient) -> dict[str, str]:
    email = f"crypto_{uuid4().hex[:6]}@example.com"
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
    confirm_data = confirm_response.json()
    return {
        "access_token": confirm_data["access_token"],
        "email": email,
    }


def test_crypto_checkout_webhook_updates_billing_status(crypto_client):
    client, billing = crypto_client
    auth_bundle = _signup_and_confirm(client)
    headers = {"Authorization": f"Bearer {auth_bundle['access_token']}"}

    initial_status = client.get(f"{API_BASE}/billing/status", headers=headers)
    assert initial_status.status_code == 200
    assert initial_status.json()["subscription"] is None

    checkout_response = client.post(
        f"{API_BASE}/billing/checkout/crypto",
        headers=headers,
        json={"plan_code": "pro"},
    )
    assert checkout_response.status_code == 200
    checkout_payload = checkout_response.json()
    assert checkout_payload["invoice_id"] == billing["invoice_id"]
    assert checkout_payload["hosted_url"] == billing["hosted_url"]

    webhook_payload = {
        "payment_id": billing["invoice_id"],
        "invoice_id": billing["invoice_id"],
        "invoice_url": billing["hosted_url"],
        "checkout_url": billing["hosted_url"],
        "payment_status": "confirmed",
        "pay_amount": "10.5",
        "payin_hash": "0xabc123",
        "payin_confirmations": 2,
    }
    payload_text = json.dumps(webhook_payload)
    signature = hmac.new(
        billing["webhook_secret"].encode("utf-8"),
        payload_text.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()

    webhook_response = client.post(
        f"{API_BASE}/billing/webhook/crypto",
        data=payload_text,
        headers={"x-nowpayments-sig": signature},
    )
    assert webhook_response.status_code == 204

    status_response = client.get(f"{API_BASE}/billing/status", headers=headers)
    assert status_response.status_code == 200
    status_payload = status_response.json()
    subscription = status_payload["subscription"]
    assert subscription is not None
    assert subscription["plan_code"] == "pro"
    assert subscription["status"] == "active"
    assert subscription["latest_invoice_id"] == billing["invoice_id"]
    assert subscription["hosted_checkout_url"] == billing["hosted_url"]
    assert subscription["current_period_end"]
    assert status_payload["account_status"] == "active"
    assert status_payload["email_verified"] is True


def test_crypto_activation_cancels_other_active_subscriptions(crypto_client):
    client, billing = crypto_client
    auth_bundle = _signup_and_confirm(client)
    headers = {"Authorization": f"Bearer {auth_bundle['access_token']}"}
    loop = _get_event_loop()
    settings = api_dependencies.get_settings()
    session_factory = loop.run_until_complete(get_sessionmaker(settings))

    async def _seed_active_subscription() -> UUID:
        async with session_factory() as session:
            account = await session.scalar(
                select(account_models.Account).where(account_models.Account.email == auth_bundle["email"])
            )
            assert account is not None
            subscription = account_models.BillingSubscription(
                account_id=account.id,
                provider_subscription_id=f"legacy_{uuid4().hex[:6]}",
                status=account_models.BillingSubscriptionStatus.ACTIVE,
                plan_code="legacy",
                currency="usd",
                unit_amount_cents=800,
                interval="month",
                current_period_start=datetime.now(timezone.utc) - timedelta(days=3),
                current_period_end=datetime.now(timezone.utc) + timedelta(days=27),
            )
            session.add(subscription)
            await session.commit()
            return subscription.id

    legacy_subscription_id = loop.run_until_complete(_seed_active_subscription())

    checkout_response = client.post(
        f"{API_BASE}/billing/checkout/crypto",
        headers=headers,
        json={"plan_code": "pro"},
    )
    assert checkout_response.status_code == 200

    webhook_payload = {
        "payment_id": billing["invoice_id"],
        "invoice_id": billing["invoice_id"],
        "invoice_url": billing["hosted_url"],
        "checkout_url": billing["hosted_url"],
        "payment_status": "confirmed",
        "pay_amount": "10.5",
        "payin_hash": "0xabc123",
        "payin_confirmations": 2,
    }
    payload_text = json.dumps(webhook_payload)
    signature = hmac.new(
        billing["webhook_secret"].encode("utf-8"),
        payload_text.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()

    webhook_response = client.post(
        f"{API_BASE}/billing/webhook/crypto",
        data=payload_text,
        headers={"x-nowpayments-sig": signature},
    )
    assert webhook_response.status_code == 204

    status_response = client.get(f"{API_BASE}/billing/status", headers=headers)
    assert status_response.status_code == 200
    status_payload = status_response.json()
    subscription_payload = status_payload["subscription"]
    assert subscription_payload is not None
    assert subscription_payload["plan_code"] == "pro"

    async def _fetch_subscriptions():
        async with session_factory() as session:
            account = await session.scalar(
                select(account_models.Account).where(account_models.Account.email == auth_bundle["email"])
            )
            assert account is not None
            return (
                await session.scalars(
                    select(account_models.BillingSubscription).where(
                        account_models.BillingSubscription.account_id == account.id
                    )
                )
            ).all()

    subscriptions = loop.run_until_complete(_fetch_subscriptions())
    legacy_subscription = next(sub for sub in subscriptions if sub.id == legacy_subscription_id)
    assert legacy_subscription.status == account_models.BillingSubscriptionStatus.CANCELED
    assert legacy_subscription.cancel_at_period_end is True
    active_statuses = {
        account_models.BillingSubscriptionStatus.TRIALING,
        account_models.BillingSubscriptionStatus.ACTIVE,
        account_models.BillingSubscriptionStatus.PAST_DUE,
    }
    active_subscriptions = [sub for sub in subscriptions if sub.status in active_statuses]
    assert len(active_subscriptions) == 1
    assert active_subscriptions[0].plan_code == "pro"


def test_crypto_cancel_marks_subscription_and_expires_future_invoice(crypto_client):
    client, billing = crypto_client
    auth_bundle = _signup_and_confirm(client)
    headers = {"Authorization": f"Bearer {auth_bundle['access_token']}"}
    loop = _get_event_loop()
    settings = api_dependencies.get_settings()
    session_factory = loop.run_until_complete(get_sessionmaker(settings))

    checkout_response = client.post(
        f"{API_BASE}/billing/checkout/crypto",
        headers=headers,
        json={"plan_code": "pro"},
    )
    assert checkout_response.status_code == 200

    webhook_payload = {
        "payment_id": billing["invoice_id"],
        "invoice_id": billing["invoice_id"],
        "invoice_url": billing["hosted_url"],
        "checkout_url": billing["hosted_url"],
        "payment_status": "confirmed",
        "pay_amount": "10.5",
        "payin_hash": "0xabc123",
        "payin_confirmations": 2,
    }
    payload_text = json.dumps(webhook_payload)
    signature = hmac.new(
        billing["webhook_secret"].encode("utf-8"),
        payload_text.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()

    webhook_response = client.post(
        f"{API_BASE}/billing/webhook/crypto",
        data=payload_text,
        headers={"x-nowpayments-sig": signature},
    )
    assert webhook_response.status_code == 204

    future_invoice_id = f"renew_{uuid4().hex[:8]}"

    async def _seed_future_invoice():
        async with session_factory() as session:
            account = await session.scalar(
                select(account_models.Account).where(account_models.Account.email == auth_bundle["email"])
            )
            assert account is not None
            subscription = await session.scalar(
                select(account_models.BillingSubscription).where(
                    account_models.BillingSubscription.account_id == account.id
                )
            )
            assert subscription is not None
            future_end = (subscription.current_period_end or datetime.now(timezone.utc)) + timedelta(days=30)
            payment = account_models.BillingCryptoPayment(
                account_id=account.id,
                plan_code=subscription.plan_code,
                invoice_id=future_invoice_id,
                chain=account_models.BillingCryptoChain.TRC20,
                expected_amount=Decimal("12"),
                paid_amount=Decimal("0"),
                confirmations=0,
                status=account_models.BillingCryptoPaymentStatus.PENDING,
                period_end_at=future_end,
                raw_payload={"invoice_url": f"https://pay.test/{future_invoice_id}"},
            )
            session.add(payment)
            await session.commit()

    loop.run_until_complete(_seed_future_invoice())

    cancel_response = client.post(
        f"{API_BASE}/billing/cancel/crypto",
        headers=headers,
        json={"plan_code": "pro"},
    )
    assert cancel_response.status_code == 200
    cancel_payload = cancel_response.json()
    assert cancel_payload["subscription"]["cancel_at_period_end"] is True
    assert future_invoice_id in cancel_payload["expired_invoice_ids"]

    async def _fetch_state():
        async with session_factory() as session:
            account = await session.scalar(
                select(account_models.Account)
                .where(account_models.Account.email == auth_bundle["email"])
                .options(
                    selectinload(account_models.Account.billing_subscriptions),
                    selectinload(account_models.Account.billing_crypto_payments),
                )
            )
            assert account is not None
            subscription = (account.billing_subscriptions or [None])[0]
            return subscription, list(account.billing_crypto_payments or [])

    subscription_state, payments = loop.run_until_complete(_fetch_state())
    assert subscription_state.cancel_at_period_end is True
    expired_payment = next(p for p in payments if p.invoice_id == future_invoice_id)
    assert expired_payment.status == account_models.BillingCryptoPaymentStatus.EXPIRED

    billing_service = BillingService(settings)

    async def _send_reminders():
        async with session_factory() as session:
            account = await session.scalar(
                select(account_models.Account)
                .where(account_models.Account.email == auth_bundle["email"])
                .options(selectinload(account_models.Account.billing_subscriptions))
            )
            assert account is not None
            return await billing_service.send_crypto_renewal_notifications(session)

    sent_count = loop.run_until_complete(_send_reminders())
    assert sent_count == 0


def test_billing_status_prunes_duplicate_active_subscriptions(crypto_client):
    client, _ = crypto_client
    auth_bundle = _signup_and_confirm(client)
    headers = {"Authorization": f"Bearer {auth_bundle['access_token']}"}
    loop = _get_event_loop()
    settings = api_dependencies.get_settings()
    session_factory = loop.run_until_complete(get_sessionmaker(settings))

    async def _seed_multiple_subscriptions():
        async with session_factory() as session:
            account = await session.scalar(
                select(account_models.Account).where(account_models.Account.email == auth_bundle["email"])
            )
            assert account is not None
            older = account_models.BillingSubscription(
                account_id=account.id,
                provider_subscription_id=f"legacy_{uuid4().hex[:6]}",
                status=account_models.BillingSubscriptionStatus.ACTIVE,
                plan_code="legacy",
                currency="usd",
                unit_amount_cents=800,
                interval="month",
                current_period_start=datetime.now(timezone.utc) - timedelta(days=15),
                current_period_end=datetime.now(timezone.utc) + timedelta(days=15),
                latest_invoice_id="legacy_inv",
            )
            newer = account_models.BillingSubscription(
                account_id=account.id,
                provider_subscription_id=f"crypto_{uuid4().hex[:6]}",
                status=account_models.BillingSubscriptionStatus.ACTIVE,
                plan_code="pro",
                currency="usd",
                unit_amount_cents=1200,
                interval="month",
                current_period_start=datetime.now(timezone.utc) - timedelta(days=2),
                current_period_end=datetime.now(timezone.utc) + timedelta(days=28),
                latest_invoice_id="new_inv",
                hosted_checkout_url="https://pay.test/new",
            )
            session.add_all([older, newer])
            await session.commit()
            return older.id, newer.id

    older_subscription_id, newer_subscription_id = loop.run_until_complete(_seed_multiple_subscriptions())

    status_response = client.get(f"{API_BASE}/billing/status", headers=headers)
    assert status_response.status_code == 200
    status_payload = status_response.json()
    subscription_payload = status_payload["subscription"]
    assert subscription_payload is not None
    assert subscription_payload["plan_code"] == "pro"
    assert subscription_payload["latest_invoice_id"] == "new_inv"
    assert subscription_payload["status"] == "active"

    async def _fetch_subscriptions():
        async with session_factory() as session:
            account = await session.scalar(
                select(account_models.Account).where(account_models.Account.email == auth_bundle["email"])
            )
            assert account is not None
            return (
                await session.scalars(
                    select(account_models.BillingSubscription).where(
                        account_models.BillingSubscription.account_id == account.id
                    )
                )
            ).all()

    subscriptions = loop.run_until_complete(_fetch_subscriptions())
    active_statuses = {
        account_models.BillingSubscriptionStatus.TRIALING,
        account_models.BillingSubscriptionStatus.ACTIVE,
        account_models.BillingSubscriptionStatus.PAST_DUE,
    }
    active_subscriptions = [sub for sub in subscriptions if sub.status in active_statuses]
    assert len(active_subscriptions) == 1
    assert active_subscriptions[0].id == newer_subscription_id
    legacy_subscription = next(sub for sub in subscriptions if sub.id == older_subscription_id)
    assert legacy_subscription.status == account_models.BillingSubscriptionStatus.CANCELED
    assert legacy_subscription.cancel_at_period_end is True


def test_lapsed_subscription_reverts_to_free_plan(crypto_client):
    client, _ = crypto_client
    auth_bundle = _signup_and_confirm(client)
    headers = {"Authorization": f"Bearer {auth_bundle['access_token']}"}
    loop = _get_event_loop()
    settings = api_dependencies.get_settings()
    session_factory = loop.run_until_complete(get_sessionmaker(settings))

    async def _seed_lapsed_subscription():
        async with session_factory() as session:
            account = await session.scalar(
                select(account_models.Account).where(account_models.Account.email == auth_bundle["email"])
            )
            assert account is not None
            subscription = account_models.BillingSubscription(
                account_id=account.id,
                provider_subscription_id=f"crypto_{uuid4().hex[:6]}",
                status=account_models.BillingSubscriptionStatus.ACTIVE,
                plan_code="pro",
                currency="usd",
                unit_amount_cents=1200,
                interval="month",
                current_period_start=datetime.now(timezone.utc) - timedelta(days=40),
                current_period_end=datetime.now(timezone.utc) - timedelta(days=1),
                cancel_at_period_end=False,
                hosted_checkout_url="https://pay.test/expired",
            )
            session.add(subscription)
            await session.commit()

    loop.run_until_complete(_seed_lapsed_subscription())

    status_response = client.get(f"{API_BASE}/billing/status", headers=headers)
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["subscription"] is None

    async def _fetch_subscription():
        async with session_factory() as session:
            account = await session.scalar(
                select(account_models.Account)
                .where(account_models.Account.email == auth_bundle["email"])
                .options(selectinload(account_models.Account.billing_subscriptions))
            )
            assert account is not None
            return (account.billing_subscriptions or [None])[0]

    subscription = loop.run_until_complete(_fetch_subscription())
    assert subscription is not None
    assert subscription.status == account_models.BillingSubscriptionStatus.CANCELED
    assert subscription.cancel_at_period_end is True


def test_account_billing_crypto_checkout_redirect_ui_smoke(crypto_client):
    client, _ = crypto_client
    _signup_and_confirm(client)
    assert client.cookies.get(SESSION_COOKIE_NAME)

    billing_page = client.get("/app/billing")
    assert billing_page.status_code == 200
    body = billing_page.text
    assert "data-api-billing-checkout-crypto-url" in body
    assert 'data-plan-trigger="pro"' in body
    assert "Pay with crypto" in body

    script_response = client.get("/static/js/account.js")
    assert script_response.status_code == 200
    script_body = script_response.text
    assert "apiBillingCheckoutCryptoUrl" in script_body
    assert "window.location.href = payload.hosted_url" in script_body
