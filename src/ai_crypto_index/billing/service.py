from __future__ import annotations

import asyncio
import logging
import uuid
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from decimal import Decimal
from typing import Any

import stripe
import requests
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai_crypto_index.accounts import models as account_models
from ai_crypto_index.shared import email_notifications, intake_store
from ai_crypto_index.shared.settings import BillingCryptoNetworkSettings, BillingPlanSettings, ServiceSettings

logger = logging.getLogger("ai_crypto_index.billing")
NOWPAYMENTS_API_BASE = "https://api.nowpayments.io/v1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BillingError(RuntimeError):
    """Base class for billing issues."""


class BillingPlanNotFound(BillingError):
    """Raised when a plan configuration could not be located."""


class BillingConfigurationError(BillingError):
    """Raised when Stripe secrets or plans are missing."""


class StripeWebhookError(BillingError):
    """Raised when webhook payloads fail verification."""


@dataclass(slots=True)
class CheckoutSessionResult:
    url: str
    session_id: str


class BillingService:
    def __init__(self, settings: ServiceSettings) -> None:
        self.settings = settings
        self.config = settings.billing
        self._stripe = stripe
        if self.config.stripe_secret_key:
            self._stripe.api_key = self.config.stripe_secret_key
            self._stripe.max_network_retries = 2
        self._active_statuses = {
            account_models.BillingSubscriptionStatus.TRIALING,
            account_models.BillingSubscriptionStatus.ACTIVE,
            account_models.BillingSubscriptionStatus.PAST_DUE,
        }

    async def ensure_customer(
        self,
        session: AsyncSession,
        account: account_models.Account,
    ) -> account_models.BillingCustomer:
        if self.config.provider == "crypto":
            raise BillingConfigurationError("Stripe customers are not available when provider=crypto.")
        if account.billing_customer:
            return account.billing_customer

        stmt = select(account_models.BillingCustomer).where(
            account_models.BillingCustomer.account_id == account.id
        )
        existing = await session.scalar(stmt)
        if existing:
            return existing

        customer = await self._create_stripe_customer(account)
        billing_customer = account_models.BillingCustomer(
            account_id=account.id,
            provider=account_models.BillingProvider.STRIPE,
            provider_customer_id=customer.get("id"),
            email=customer.get("email"),
            currency=customer.get("currency") or self.config.currency,
            delinquent=bool(customer.get("delinquent", False)),
            stripe_metadata=customer,
        )
        session.add(billing_customer)
        await session.commit()
        return billing_customer

    async def create_checkout_session(
        self,
        session: AsyncSession,
        account: account_models.Account,
        *,
        plan_code: str,
    ) -> CheckoutSessionResult:
        if self.config.provider == "crypto":
            return await self._create_crypto_checkout_session(session, account=account, plan_code=plan_code)

        plan = self._get_plan(plan_code)
        if not plan.self_serve:
            raise BillingConfigurationError(f"Plan {plan.code} cannot be purchased via checkout.")
        if not plan.price_id:
            raise BillingConfigurationError(f"Plan {plan.code} has no price configured.")

        customer = await self.ensure_customer(session, account)
        metadata = {
            "account_id": str(account.id),
            "plan_code": plan.code,
        }

        subscription_data: dict[str, Any] = {"metadata": metadata}
        if plan.trial_days > 0:
            subscription_data["trial_period_days"] = plan.trial_days

        checkout = await self._call_stripe(
            partial(
                self._stripe.checkout.Session.create,
                customer=customer.provider_customer_id,
                mode="subscription",
                success_url=self.config.checkout_success_url,
                cancel_url=self.config.checkout_cancel_url,
                line_items=[
                    {
                        "price": plan.price_id,
                        "quantity": 1,
                    }
                ],
                automatic_tax={"enabled": True},
                billing_address_collection="auto",
                customer_update={"address": "auto"},
                allow_promotion_codes=True,
                client_reference_id=str(account.id),
                metadata=metadata,
                subscription_data=subscription_data,
            )
        )
        await self._record_billing_event(
            session,
            provider_event_id=checkout.get("id"),
            event_type="checkout.session.created",
            account=account,
            subscription=None,
            payload=checkout,
        )
        await session.commit()
        return CheckoutSessionResult(url=checkout["url"], session_id=checkout["id"])

    async def create_customer_portal_session(
        self,
        session: AsyncSession,
        account: account_models.Account,
    ) -> str:
        if self.config.provider == "crypto":
            raise BillingConfigurationError("Customer portal is unavailable for crypto provider.")
        customer = await self.ensure_customer(session, account)
        portal_session = await self._call_stripe(
            partial(
                self._stripe.billing_portal.Session.create,
                customer=customer.provider_customer_id,
                return_url=self.config.portal_return_url,
            )
        )
        await self._record_billing_event(
            session,
            provider_event_id=portal_session.get("id"),
            event_type="billing_portal.session.created",
            account=account,
            subscription=None,
            payload=portal_session,
        )
        await session.commit()
        return portal_session["url"]

    async def _create_crypto_checkout_session(
        self,
        session: AsyncSession,
        account: account_models.Account,
        *,
        plan_code: str,
        period_end_at: datetime | None = None,
    ) -> CheckoutSessionResult:
        crypto_cfg = self.config.crypto
        if not crypto_cfg or not crypto_cfg.api_key:
            raise BillingConfigurationError("Crypto provider is not configured.")
        normalized_period_end = period_end_at
        if normalized_period_end and normalized_period_end.tzinfo is None:
            normalized_period_end = normalized_period_end.replace(tzinfo=timezone.utc)
        network = self._get_crypto_network(crypto_cfg.default_network)
        plan = self._get_plan(plan_code)
        usd_amount = Decimal(plan.unit_amount_cents) / Decimal(100)
        expected_amount = usd_amount * Decimal(crypto_cfg.usd_to_crypto_rate or 1)
        order_id = f"{account.id}:{plan.code}:{uuid.uuid4().hex[:8]}"
        payload = {
            "price_amount": float(usd_amount),
            "price_currency": "usd",
            "pay_currency": network.code.replace("_", ""),
            "order_id": order_id,
            "order_description": f"{plan.name} subscription",
            "ipn_callback_url": self.settings.billing.checkout_success_url,  # overwritten by webhook url below
        }
        payload["ipn_callback_url"] = f"{self.settings.auth.public_app_url.rstrip('/')}/api/v1/billing/webhook/crypto"
        response = await self._call_nowpayments("POST", "/invoice", payload, api_key=crypto_cfg.api_key)
        invoice_id = response.get("id") or response.get("invoice_id") or response.get("payment_id")
        hosted_url = response.get("invoice_url") or response.get("checkout_url") or response.get("pay_address")
        if not invoice_id or not hosted_url:
            raise BillingError("Failed to create crypto invoice.")

        payment = account_models.BillingCryptoPayment(
            account_id=account.id,
            plan_code=plan.code,
            invoice_id=str(invoice_id),
            chain=account_models.BillingCryptoChain(network.chain),
            expected_amount=expected_amount,
            paid_amount=Decimal("0"),
            confirmations=0,
            status=account_models.BillingCryptoPaymentStatus.PENDING,
            raw_payload=response,
            period_end_at=normalized_period_end,
        )
        session.add(payment)
        await self._record_billing_event(
            session,
            provider_event_id=str(invoice_id),
            event_type="crypto.invoice.created",
            account=account,
            subscription=None,
            payload=response,
            provider=account_models.BillingProvider.CRYPTO,
        )
        await session.commit()
        return CheckoutSessionResult(url=str(hosted_url), session_id=str(invoice_id))

    async def cancel_crypto_subscription(
        self,
        session: AsyncSession,
        *,
        account: account_models.Account,
        plan_code: str | None = None,
    ) -> tuple[account_models.BillingSubscription | None, list[str]]:
        if self.config.provider != "crypto":
            raise BillingConfigurationError("Crypto provider is not configured.")

        stmt = (
            select(account_models.BillingSubscription)
            .where(
                account_models.BillingSubscription.account_id == account.id,
                account_models.BillingSubscription.status.in_(self._active_statuses),
            )
            .order_by(account_models.BillingSubscription.current_period_end.desc())
        )
        if plan_code:
            stmt = stmt.where(account_models.BillingSubscription.plan_code == plan_code)
        subscription = await session.scalar(stmt)
        if not subscription:
            return None, []

        now_utc = _utcnow()
        subscription.cancel_at_period_end = True
        subscription.synced_at = now_utc
        session.add(subscription)

        expire_after = subscription.current_period_end or now_utc
        pending_statuses = (
            account_models.BillingCryptoPaymentStatus.PENDING,
            account_models.BillingCryptoPaymentStatus.PROCESSING,
        )
        pending_stmt = (
            select(account_models.BillingCryptoPayment)
            .where(
                account_models.BillingCryptoPayment.account_id == account.id,
                account_models.BillingCryptoPayment.plan_code == subscription.plan_code,
                account_models.BillingCryptoPayment.status.in_(pending_statuses),
                account_models.BillingCryptoPayment.period_end_at.is_not(None),
                account_models.BillingCryptoPayment.period_end_at >= expire_after,
            )
            .order_by(account_models.BillingCryptoPayment.created_at.desc())
        )
        pending_payments = (await session.execute(pending_stmt)).scalars().all()
        expired_invoices: list[str] = []

        for payment in pending_payments:
            try:
                if payment.paid_amount and Decimal(payment.paid_amount) > Decimal("0"):
                    continue
            except Exception:
                continue
            payment.status = account_models.BillingCryptoPaymentStatus.EXPIRED
            raw_payload = payment.raw_payload if isinstance(payment.raw_payload, dict) else {}
            updated_payload = dict(raw_payload or {})
            updated_payload["canceled_at"] = now_utc.isoformat()
            payment.raw_payload = updated_payload
            session.add(payment)
            expired_invoices.append(payment.invoice_id)

        if expired_invoices:
            subscription.hosted_checkout_url = None

        event_id = f"crypto.cancel.{subscription.id}.{uuid.uuid4().hex[:8]}"
        await self._record_billing_event(
            session,
            provider_event_id=event_id,
            event_type="crypto.subscription.cancel_at_period_end",
            account=account,
            subscription=subscription,
            payload={
                "plan_code": subscription.plan_code,
                "expired_invoice_ids": expired_invoices,
                "cancelled_at": now_utc.isoformat(),
            },
            provider=account_models.BillingProvider.CRYPTO,
        )
        await session.commit()
        return subscription, expired_invoices

    async def resume_crypto_subscription(
        self,
        session: AsyncSession,
        *,
        account: account_models.Account,
        plan_code: str | None = None,
    ) -> tuple[account_models.BillingSubscription | None, list[str]]:
        if self.config.provider != "crypto":
            raise BillingConfigurationError("Crypto provider is not configured.")

        stmt = (
            select(account_models.BillingSubscription)
            .where(
                account_models.BillingSubscription.account_id == account.id,
                account_models.BillingSubscription.status.in_(self._active_statuses),
            )
            .order_by(account_models.BillingSubscription.current_period_end.desc())
        )
        if plan_code:
            stmt = stmt.where(account_models.BillingSubscription.plan_code == plan_code)
        subscription = await session.scalar(stmt)
        if not subscription:
            return None, []
        if not subscription.cancel_at_period_end:
            return subscription, []

        now_utc = _utcnow()
        plan: BillingPlanSettings | None = None
        try:
            plan = self._get_plan(subscription.plan_code)
        except BillingPlanNotFound:
            logger.exception(
                "crypto_resume_missing_plan",
                extra={"account_id": str(account.id), "plan_code": subscription.plan_code},
            )

        checkout_session: CheckoutSessionResult | None = None
        if plan:
            target_period_end = (subscription.current_period_end or now_utc) + timedelta(
                days=self._plan_interval_days(plan)
            )
            try:
                checkout_session = await self._ensure_crypto_checkout_session(
                    session,
                    account=account,
                    plan_code=subscription.plan_code,
                    target_period_end=target_period_end,
                )
                subscription.hosted_checkout_url = checkout_session.url or subscription.hosted_checkout_url
            except BillingError:
                logger.exception(
                    "crypto_resume_invoice_failed",
                    extra={"account_id": str(account.id), "plan_code": subscription.plan_code},
                )

        subscription.cancel_at_period_end = False
        subscription.status = account_models.BillingSubscriptionStatus.ACTIVE
        subscription.synced_at = now_utc
        session.add(subscription)

        resume_event_id = (
            f"crypto.resume.{subscription.id}."
            f"{(subscription.current_period_end or now_utc).date().isoformat()}"
        )
        created_resume_event = await self._record_billing_event(
            session,
            provider_event_id=resume_event_id,
            event_type="crypto.subscription.resumed",
            account=account,
            subscription=subscription,
            payload={
                "plan_code": subscription.plan_code,
                "invoice_id": checkout_session.session_id if checkout_session else None,
                "hosted_checkout_url": checkout_session.url if checkout_session else subscription.hosted_checkout_url,
            },
            provider=account_models.BillingProvider.CRYPTO,
        )
        self._log_marketing_event(account, "crypto.subscription.resumed", subscription)
        if created_resume_event and plan and account.email:
            try:
                email_notifications.send_crypto_resume_email(
                    recipient=account.email,
                    full_name=account.full_name,
                    plan_name=plan.name,
                    expires_at=subscription.current_period_end or now_utc,
                    invoice_url=subscription.hosted_checkout_url,
                )
            except Exception:  # pragma: no cover - best-effort delivery
                logger.exception(
                    "failed_to_send_crypto_resume_email",
                    extra={"account_id": str(account.id), "plan_code": subscription.plan_code},
                )
        await session.commit()
        return subscription, []

    async def generate_enterprise_invoice(
        self,
        session: AsyncSession,
        account: account_models.Account,
        *,
        amount_cents: int,
        memo: str,
        due_in_days: int | None = None,
    ) -> dict[str, Any]:
        if amount_cents <= 0:
            raise ValueError("Invoice amount must be positive.")
        days_until_due = due_in_days or self.config.enterprise_invoice_terms_days
        plan = self._get_plan("enterprise")
        customer = await self.ensure_customer(session, account)
        await self._call_stripe(
            partial(
                self._stripe.InvoiceItem.create,
                customer=customer.provider_customer_id,
                amount=amount_cents,
                currency=plan.currency,
                description=memo,
            )
        )
        invoice = await self._call_stripe(
            partial(
                self._stripe.Invoice.create,
                customer=customer.provider_customer_id,
                collection_method="send_invoice",
                days_until_due=days_until_due,
                description=memo,
                metadata={"account_id": str(account.id), "plan_code": plan.code},
            )
        )
        finalized = await self._call_stripe(
            partial(
                self._stripe.Invoice.finalize_invoice,
                invoice.get("id"),
                payment_settings={"payment_method_types": ["card", "us_bank_account"]},
            )
        )
        await self._record_billing_event(
            session,
            provider_event_id=finalized.get("id"),
            event_type="invoice.generated",
            account=account,
            subscription=None,
            payload=finalized,
        )
        await session.commit()
        return {
            "invoice_id": finalized.get("id"),
            "hosted_invoice_url": finalized.get("hosted_invoice_url"),
            "due_date": finalized.get("due_date"),
        }

    async def extend_enterprise_subscription(
        self,
        session: AsyncSession,
        account: account_models.Account,
        *,
        additional_days: int,
        note: str | None = None,
    ) -> account_models.BillingSubscription:
        if additional_days <= 0:
            raise ValueError("additional_days must be greater than zero.")
        subscription = await self._find_subscription(session, account.id, plan_code="enterprise")
        if not subscription:
            raise BillingPlanNotFound("Enterprise subscription not found for this account.")

        base = subscription.current_period_end or _utcnow()
        subscription.current_period_end = base + timedelta(days=additional_days)
        subscription.cancel_at_period_end = False
        subscription.status = account_models.BillingSubscriptionStatus.ACTIVE
        raw_data = dict(subscription.raw_data or {})
        if note:
            raw_data["manual_extension_note"] = note
        subscription.raw_data = raw_data
        subscription.synced_at = _utcnow()
        session.add(subscription)
        await session.commit()
        self._log_marketing_event(
            account=account,
            event_type="enterprise_manual_extension",
            subscription=subscription,
        )
        return subscription

    async def process_stripe_webhook(
        self,
        session: AsyncSession,
        *,
        payload: str,
        signature: str | None,
    ) -> None:
        secret = self.config.stripe_webhook_secret
        if not secret:
            raise StripeWebhookError("Stripe webhook secret is not configured.")
        try:
            event = self._stripe.Webhook.construct_event(payload, signature or "", secret)
        except self._stripe.error.SignatureVerificationError as exc:  # type: ignore[attr-defined]
            raise StripeWebhookError("invalid_signature") from exc

        event_type = event.get("type")
        data_object = event.get("data", {}).get("object") or {}

        if event_type == "checkout.session.completed":
            await self._handle_checkout_completed(session, event, data_object)
        elif event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
            await self._handle_subscription_event(session, event, data_object)
        elif event_type in {"invoice.payment_succeeded", "invoice.payment_failed"}:
            await self._handle_invoice_event(session, event, data_object)
        else:
            await self._record_billing_event(
                session,
                provider_event_id=event.get("id"),
                event_type=event_type or "unknown",
                account=None,
                subscription=None,
                payload=event,
            )
            await session.commit()

    async def process_crypto_webhook(
        self,
        session: AsyncSession,
        *,
        payload: str,
        signature: str | None,
    ) -> None:
        invoice_id: str | None = None
        payload_preview = self._payload_preview(payload)
        try:
            crypto_cfg = self.config.crypto
            if not crypto_cfg or not crypto_cfg.webhook_secret:
                raise BillingError("Crypto webhook secret is not configured.")
            if not self._verify_crypto_signature(payload, signature or "", crypto_cfg.webhook_secret):
                raise BillingError("invalid_signature")
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise BillingError("invalid_payload") from exc

            invoice_id = data.get("payment_id") or data.get("id") or data.get("invoice_id") or data.get("order_id")
            if not invoice_id:
                raise BillingError("missing_invoice_id")

            stmt = select(account_models.BillingCryptoPayment).where(
                account_models.BillingCryptoPayment.invoice_id == str(invoice_id)
            )
            payment = await session.scalar(stmt)
            if not payment:
                logger.warning("Crypto webhook for unknown invoice_id %s", invoice_id)
                return

            payment.tx_hash = data.get("payin_hash") or data.get("transaction_hash") or payment.tx_hash
            payment.confirmations = int(data.get("payin_confirmations", payment.confirmations) or payment.confirmations or 0)
            paid_raw = data.get("pay_amount") or data.get("actually_paid") or data.get("paid_amount")
            if paid_raw is not None:
                try:
                    payment.paid_amount = Decimal(str(paid_raw))
                except Exception:
                    payment.paid_amount = payment.paid_amount
            status_value = str(data.get("payment_status") or data.get("status") or "pending").lower()
            payment.status = self._map_crypto_status(status_value)
            payment.raw_payload = data

            account = await session.get(account_models.Account, payment.account_id)
            subscription = None
            if account:
                subscription = await self._find_subscription(session, account.id, plan_code=payment.plan_code)
            await self._record_billing_event(
                session,
                provider_event_id=str(invoice_id),
                event_type=f"crypto.payment.{status_value or 'unknown'}",
                account=account,
                subscription=None,
                payload=data,
                provider=account_models.BillingProvider.CRYPTO,
            )

            if payment.status == account_models.BillingCryptoPaymentStatus.CONFIRMED and account:
                plan = self._get_plan(payment.plan_code)
                already_activated = (
                    payment.period_end_at is not None
                    and subscription
                    and subscription.current_period_end
                    and subscription.current_period_end >= payment.period_end_at
                )
                if not already_activated:
                    subscription = await self._activate_crypto_subscription(session, account=account, plan=plan, payment=payment)
                if subscription:
                    await self._cancel_conflicting_subscriptions(
                        session,
                        account=account,
                        keep_subscription=subscription,
                    )
                activation_event_id = f"{payment.invoice_id}:activation_notice"
                if subscription:
                    should_notify = await self._record_billing_event(
                        session,
                        provider_event_id=activation_event_id,
                        event_type="crypto.activation.notified",
                        account=account,
                        subscription=subscription,
                        payload={
                            "invoice_id": payment.invoice_id,
                            "period_end_at": payment.period_end_at.isoformat() if payment.period_end_at else None,
                        },
                        provider=account_models.BillingProvider.CRYPTO,
                    )
                    if should_notify:
                        email_notifications.send_crypto_activation_email(
                            recipient=account.email,
                            full_name=account.full_name,
                            plan_name=plan.name,
                            expires_at=subscription.current_period_end,
                            invoice_url=subscription.hosted_checkout_url,
                        )

            await session.commit()
        except BillingError as exc:
            await self._handle_crypto_webhook_error(
                session,
                invoice_id=str(invoice_id) if invoice_id else None,
                error=exc,
                payload_preview=payload_preview,
            )
            raise
        return

    async def _handle_crypto_webhook_error(
        self,
        session: AsyncSession,
        *,
        invoice_id: str | None,
        error: BillingError,
        payload_preview: str | None,
    ) -> None:
        suffix = hashlib.sha1(str(error).encode()).hexdigest()[:8]
        event_id = f"crypto.webhook.error.{invoice_id or 'unknown'}.{suffix}"
        created_event = await self._record_billing_event(
            session,
            provider_event_id=event_id,
            event_type="crypto.webhook.error",
            account=None,
            subscription=None,
            payload={
                "invoice_id": invoice_id,
                "error": str(error),
                "payload": payload_preview,
            },
            provider=account_models.BillingProvider.CRYPTO,
        )
        await session.commit()
        if created_event:
            self._alert_crypto_webhook_failure(
                invoice_id=invoice_id,
                error=str(error),
                payload_preview=payload_preview,
            )

    def _alert_crypto_webhook_failure(
        self,
        *,
        invoice_id: str | None,
        error: str,
        payload_preview: str | None,
    ) -> None:
        try:
            email_notifications.send_crypto_webhook_alert(
                invoice_id=invoice_id,
                status="error",
                error=error,
                payload=payload_preview,
            )
        except Exception:  # pragma: no cover - alert best-effort
            logger.exception(
                "crypto_webhook_alert_failed",
                extra={"invoice_id": invoice_id, "error": error},
            )

    async def _handle_checkout_completed(
        self,
        session: AsyncSession,
        event: dict[str, Any],
        session_payload: dict[str, Any],
    ) -> None:
        account = await self._account_from_metadata(session, session_payload)
        if not account:
            logger.warning("checkout.session.completed missing account metadata.")
            return
        subscription_id = session_payload.get("subscription")
        customer_id = session_payload.get("customer")
        subscription_payload = None
        if subscription_id:
            subscription_payload = await self._call_stripe(
                partial(self._stripe.Subscription.retrieve, subscription_id, expand=["latest_invoice"])
            )
        await self._record_billing_event(
            session,
            provider_event_id=event.get("id"),
            event_type="checkout.session.completed",
            account=account,
            subscription=None,
            payload=session_payload,
        )
        if subscription_payload:
            synced_subscription = await self._sync_subscription(
                session,
                account=account,
                subscription_payload=subscription_payload,
                customer_id=customer_id,
            )
            await self._cancel_previous_subscriptions(
                session,
                account=account,
                keep_provider_id=subscription_id,
            )
        await session.commit()

    async def _handle_subscription_event(
        self,
        session: AsyncSession,
        event: dict[str, Any],
        subscription_payload: dict[str, Any],
    ) -> None:
        account = await self._account_from_metadata(session, subscription_payload)
        if not account and subscription_payload.get("customer"):
            account = await self._account_from_customer_id(session, subscription_payload.get("customer"))
        if not account:
            logger.warning("subscription event missing account metadata.")
            return
        await self._record_billing_event(
            session,
            provider_event_id=event.get("id"),
            event_type=event.get("type") or "subscription_event",
            account=account,
            subscription=None,
            payload=subscription_payload,
        )
        await self._sync_subscription(
            session,
            account=account,
            subscription_payload=subscription_payload,
            customer_id=subscription_payload.get("customer"),
        )
        await session.commit()

    async def _handle_invoice_event(
        self,
        session: AsyncSession,
        event: dict[str, Any],
        invoice_payload: dict[str, Any],
    ) -> None:
        subscription_id = invoice_payload.get("subscription")
        account = await self._account_from_metadata(session, invoice_payload)
        if not account and invoice_payload.get("customer"):
            account = await self._account_from_customer_id(session, invoice_payload.get("customer"))
        subscription = None
        if subscription_id and account:
            subscription = await self._find_subscription(session, account.id, provider_id=subscription_id)
            if subscription:
                subscription.latest_invoice_id = invoice_payload.get("id")
                session.add(subscription)
        await self._record_billing_event(
            session,
            provider_event_id=event.get("id"),
            event_type=event.get("type") or "invoice_event",
            account=account,
            subscription=subscription,
            payload=invoice_payload,
        )
        await session.commit()

    async def _cancel_previous_subscriptions(
        self,
        session: AsyncSession,
        *,
        account: account_models.Account,
        keep_provider_id: str | None = None,
    ) -> None:
        stmt = select(account_models.BillingSubscription).where(
            account_models.BillingSubscription.account_id == account.id,
            account_models.BillingSubscription.status.in_(self._active_statuses),
        )
        if keep_provider_id:
            stmt = stmt.where(account_models.BillingSubscription.provider_subscription_id != keep_provider_id)
        subscriptions = (await session.scalars(stmt)).all()
        for subscription in subscriptions:
            if subscription.cancel_at_period_end:
                continue
            try:
                await self._call_stripe(
                    partial(
                        self._stripe.Subscription.modify,
                        subscription.provider_subscription_id,
                        cancel_at_period_end=True,
                    )
                )
            except Exception:  # pragma: no cover - side-effect only
                logger.exception(
                    "Failed to mark previous subscription for cancellation",
                    extra={
                        "account_id": str(account.id),
                        "subscription_id": subscription.provider_subscription_id,
                    },
                )
                continue
            subscription.cancel_at_period_end = True
            subscription.synced_at = _utcnow()
            session.add(subscription)

    async def _cancel_conflicting_subscriptions(
        self,
        session: AsyncSession,
        *,
        account: account_models.Account,
        keep_subscription: account_models.BillingSubscription,
    ) -> None:
        stmt = select(account_models.BillingSubscription).where(
            account_models.BillingSubscription.account_id == account.id,
            account_models.BillingSubscription.id != keep_subscription.id,
            account_models.BillingSubscription.status.in_(self._active_statuses),
        )
        subscriptions = (await session.scalars(stmt)).all()
        now_utc = _utcnow()
        for subscription in subscriptions:
            if self.config.stripe_secret_key and subscription.provider_subscription_id.startswith("sub_"):
                try:
                    await self._call_stripe(
                        partial(
                            self._stripe.Subscription.modify,
                            subscription.provider_subscription_id,
                            cancel_at_period_end=True,
                        )
                    )
                except Exception:  # pragma: no cover - logging only
                    logger.exception(
                        "Failed to cancel legacy subscription during crypto activation",
                        extra={
                            "account_id": str(account.id),
                            "subscription_id": subscription.provider_subscription_id,
                        },
                    )
            subscription.status = account_models.BillingSubscriptionStatus.CANCELED
            subscription.cancel_at_period_end = True
            subscription.synced_at = now_utc
            session.add(subscription)

    async def _sync_subscription(
        self,
        session: AsyncSession,
        *,
        account: account_models.Account,
        subscription_payload: dict[str, Any],
        customer_id: str | None,
    ) -> account_models.BillingSubscription | None:
        provider_id = subscription_payload.get("id")
        if not provider_id:
            return None

        plan_code = self._plan_code_from_payload(subscription_payload)
        if not plan_code:
            logger.warning("Unable to determine plan code for subscription %s", provider_id)
            return None

        subscription = await self._find_subscription(session, account.id, provider_id=provider_id)
        if not subscription:
            subscription = account_models.BillingSubscription(
                account_id=account.id,
                provider_subscription_id=provider_id,
                plan_code=plan_code,
                price_id=self._price_id_from_payload(subscription_payload),
                customer_id=None,
            )

        if customer_id:
            customer = await self._get_or_create_customer_by_provider_id(session, account, customer_id)
            subscription.customer_id = customer.id

        status_value = subscription_payload.get("status") or "trialing"
        try:
            subscription.status = account_models.BillingSubscriptionStatus(status_value)
        except ValueError:
            subscription.status = account_models.BillingSubscriptionStatus.TRIALING

        subscription.price_id = self._price_id_from_payload(subscription_payload)
        subscription.currency = subscription_payload.get("currency") or self.config.currency
        subscription.unit_amount_cents = self._unit_amount_from_payload(subscription_payload)
        subscription.interval = self._interval_from_payload(subscription_payload)
        subscription.trial_ends_at = self._as_datetime(subscription_payload.get("trial_end"))
        subscription.current_period_start = self._as_datetime(subscription_payload.get("current_period_start"))
        subscription.current_period_end = self._as_datetime(subscription_payload.get("current_period_end"))
        subscription.cancel_at_period_end = bool(subscription_payload.get("cancel_at_period_end", False))
        subscription.latest_invoice_id = subscription_payload.get("latest_invoice", {}).get("id") if isinstance(subscription_payload.get("latest_invoice"), dict) else subscription_payload.get("latest_invoice")
        subscription.raw_data = subscription_payload
        subscription.synced_at = _utcnow()

        session.add(subscription)
        await session.flush()
        self._log_marketing_event(account, "subscription_sync", subscription)
        return subscription

    async def _record_billing_event(
        self,
        session: AsyncSession,
        *,
        provider_event_id: str | None,
        event_type: str,
        account: account_models.Account | None,
        subscription: account_models.BillingSubscription | None,
        payload: dict[str, Any] | None,
        provider: account_models.BillingProvider = account_models.BillingProvider.STRIPE,
    ) -> bool:
        if not provider_event_id:
            return False
        stmt = select(account_models.BillingEvent).where(
            account_models.BillingEvent.provider_event_id == provider_event_id
        )
        existing = await session.scalar(stmt)
        if existing:
            return False
        event = account_models.BillingEvent(
            provider_event_id=provider_event_id,
            provider=provider,
            event_type=event_type,
            account_id=account.id if account else None,
            subscription_id=subscription.id if subscription else None,
            payload=payload,
            processed_at=_utcnow(),
        )
        session.add(event)
        return True

    async def _account_from_metadata(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
    ) -> account_models.Account | None:
        metadata = payload.get("metadata") or {}
        account_id = metadata.get("account_id") or payload.get("client_reference_id")
        if not account_id:
            return None
        try:
            account_uuid = uuid.UUID(str(account_id))
        except ValueError:
            return None
        stmt = select(account_models.Account).where(account_models.Account.id == account_uuid)
        return await session.scalar(stmt)

    async def _account_from_customer_id(
        self,
        session: AsyncSession,
        provider_customer_id: str | None,
    ) -> account_models.Account | None:
        if not provider_customer_id:
            return None
        stmt = select(account_models.BillingCustomer).where(
            account_models.BillingCustomer.provider_customer_id == provider_customer_id
        )
        customer = await session.scalar(stmt)
        if not customer:
            return None
        stmt_account = select(account_models.Account).where(account_models.Account.id == customer.account_id)
        return await session.scalar(stmt_account)

    async def _create_stripe_customer(self, account: account_models.Account) -> dict[str, Any]:
        if not self.config.stripe_secret_key:
            raise BillingConfigurationError("Stripe secret key is not configured.")
        return await self._call_stripe(
            partial(
                self._stripe.Customer.create,
                email=account.email,
                name=account.full_name,
                metadata={"account_id": str(account.id)},
            )
        )

    async def _get_or_create_customer_by_provider_id(
        self,
        session: AsyncSession,
        account: account_models.Account,
        provider_customer_id: str,
    ) -> account_models.BillingCustomer:
        stmt = select(account_models.BillingCustomer).where(
            account_models.BillingCustomer.provider_customer_id == provider_customer_id
        )
        existing = await session.scalar(stmt)
        if existing:
            return existing
        customer_payload = await self._call_stripe(
            partial(self._stripe.Customer.retrieve, provider_customer_id)
        )
        billing_customer = account_models.BillingCustomer(
            account_id=account.id,
            provider=account_models.BillingProvider.STRIPE,
            provider_customer_id=provider_customer_id,
            email=customer_payload.get("email"),
            currency=customer_payload.get("currency") or self.config.currency,
            delinquent=bool(customer_payload.get("delinquent", False)),
            stripe_metadata=customer_payload,
        )
        session.add(billing_customer)
        await session.flush()
        return billing_customer

    async def _find_subscription(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        *,
        provider_id: str | None = None,
        plan_code: str | None = None,
    ) -> account_models.BillingSubscription | None:
        stmt = select(account_models.BillingSubscription).where(
            account_models.BillingSubscription.account_id == account_id
        )
        if provider_id:
            stmt = stmt.where(account_models.BillingSubscription.provider_subscription_id == provider_id)
        if plan_code:
            stmt = stmt.where(account_models.BillingSubscription.plan_code == plan_code)
        stmt = stmt.order_by(account_models.BillingSubscription.updated_at.desc())
        return await session.scalar(stmt)

    def _log_marketing_event(
        self,
        account: account_models.Account,
        event_type: str,
        subscription: account_models.BillingSubscription | None,
    ) -> None:
        derived_event = event_type
        if subscription:
            if subscription.status in {
                account_models.BillingSubscriptionStatus.TRIALING,
                account_models.BillingSubscriptionStatus.ACTIVE,
            }:
                derived_event = "subscription_activation"
            elif subscription.status == account_models.BillingSubscriptionStatus.PAST_DUE:
                derived_event = "subscription_overdue"
            elif subscription.status in {
                account_models.BillingSubscriptionStatus.CANCELED,
                account_models.BillingSubscriptionStatus.UNPAID,
                account_models.BillingSubscriptionStatus.INCOMPLETE_EXPIRED,
            }:
                derived_event = "subscription_cancellation"

        payload = {
            "account_id": str(account.id),
            "email": account.email,
            "event_type": event_type,
            "derived_event": derived_event,
            "plan_code": subscription.plan_code if subscription else None,
            "subscription_status": subscription.status.value if subscription else None,
            "recorded_at": _utcnow().isoformat(),
        }
        try:
            intake_store.persist_billing_event(self.settings, payload)
        except Exception:  # pragma: no cover - logging only
            logger.exception("Failed to persist billing event payload", extra=payload)

    def _plan_code_from_payload(self, payload: dict[str, Any]) -> str | None:
        price_id = self._price_id_from_payload(payload)
        if price_id:
            for plan in self.config.plans.values():
                if plan.price_id == price_id:
                    return plan.code
        metadata = payload.get("metadata") or {}
        plan_code = metadata.get("plan_code")
        return str(plan_code) if plan_code else None

    def _plan_from_price(self, price_id: str | None) -> BillingPlanSettings | None:
        if not price_id:
            return None
        for plan in self.config.plans.values():
            if plan.price_id == price_id:
                return plan
        return None

    def _get_plan(self, plan_code: str) -> BillingPlanSettings:
        normalized = plan_code.lower()
        if normalized not in self.config.plans:
            raise BillingPlanNotFound(f"Plan '{plan_code}' is not defined.")
        return self.config.plans[normalized]

    def _plan_interval_days(self, plan: BillingPlanSettings) -> int:
        interval = (plan.interval or "month").lower()
        if interval == "week":
            return 7
        if interval == "year":
            return 365
        return 30

    @staticmethod
    def _price_id_from_payload(payload: dict[str, Any]) -> str | None:
        items = payload.get("items", {}).get("data") if isinstance(payload.get("items"), dict) else None
        if isinstance(items, list) and items:
            price = items[0].get("price") if isinstance(items[0], dict) else None
            if isinstance(price, dict):
                return price.get("id")
        if isinstance(payload.get("plan"), dict):
            return payload["plan"].get("id")
        return None

    @staticmethod
    def _unit_amount_from_payload(payload: dict[str, Any]) -> int | None:
        items = payload.get("items", {}).get("data") if isinstance(payload.get("items"), dict) else None
        if isinstance(items, list) and items:
            price = items[0].get("price") if isinstance(items[0], dict) else None
            if isinstance(price, dict):
                return price.get("unit_amount")
        return None

    @staticmethod
    def _interval_from_payload(payload: dict[str, Any]) -> str:
        items = payload.get("items", {}).get("data") if isinstance(payload.get("items"), dict) else None
        if isinstance(items, list) and items:
            price = items[0].get("price") if isinstance(items[0], dict) else None
            recurring = price.get("recurring") if isinstance(price, dict) else None
            if isinstance(recurring, dict):
                return recurring.get("interval") or "month"
        return "month"

    @staticmethod
    def _as_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (TypeError, ValueError):
            return None

    async def _call_stripe(self, func: partial) -> dict[str, Any]:
        self._ensure_stripe_available()
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, func)
            if hasattr(result, "to_dict_recursive"):
                return result.to_dict_recursive()
            if isinstance(result, dict):
                return result
            if hasattr(result, "to_dict"):
                return result.to_dict()
            return dict(result)
        except self._stripe.error.StripeError as exc:  # type: ignore[attr-defined]
            logger.exception("Stripe API call failed: %s", exc.user_message or str(exc))
            raise BillingError(str(exc)) from exc

    def _ensure_stripe_available(self) -> None:
        if not self.config.stripe_secret_key:
            raise BillingConfigurationError("Stripe secret key is not configured.")

    def _get_crypto_network(self, code: str) -> "BillingCryptoNetworkSettings":
        crypto_cfg = self.config.crypto
        if not crypto_cfg:
            raise BillingConfigurationError("Crypto provider is not configured.")
        normalized = str(code or "").lower()
        network = crypto_cfg.networks.get(normalized)
        if not network:
            raise BillingConfigurationError(f"Crypto network '{code}' is not configured.")
        return network

    def _map_crypto_status(self, raw_status: str) -> account_models.BillingCryptoPaymentStatus:
        normalized = raw_status.lower()
        if normalized in {"waiting", "pending"}:
            return account_models.BillingCryptoPaymentStatus.PENDING
        if normalized in {"confirming", "sending", "confirming_payment", "processing"}:
            return account_models.BillingCryptoPaymentStatus.PROCESSING
        if normalized in {"confirmed", "finished", "completed", "paid"}:
            return account_models.BillingCryptoPaymentStatus.CONFIRMED
        if normalized in {"failed", "refunded"}:
            return account_models.BillingCryptoPaymentStatus.FAILED
        if normalized in {"expired", "cancelled"}:
            return account_models.BillingCryptoPaymentStatus.EXPIRED
        return account_models.BillingCryptoPaymentStatus.PENDING

    async def _activate_crypto_subscription(
        self,
        session: AsyncSession,
        *,
        account: account_models.Account,
        plan: BillingPlanSettings,
        payment: account_models.BillingCryptoPayment,
    ) -> account_models.BillingSubscription:
        now_utc = _utcnow()
        subscription = await self._find_subscription(session, account.id, plan_code=plan.code)
        if not subscription:
            subscription = account_models.BillingSubscription(
                account_id=account.id,
                provider_subscription_id=payment.invoice_id,
                status=account_models.BillingSubscriptionStatus.ACTIVE,
                plan_code=plan.code,
                currency=plan.currency,
                unit_amount_cents=plan.unit_amount_cents,
                interval=plan.interval,
                hosted_checkout_url=self._extract_invoice_url(payment.raw_payload),
            )
        duration_days = self._plan_interval_days(plan)
        base_start = subscription.current_period_end if subscription.current_period_end and subscription.current_period_end > now_utc else now_utc
        target_end = payment.period_end_at or (base_start + timedelta(days=duration_days))
        if target_end.tzinfo is None:
            target_end = target_end.replace(tzinfo=timezone.utc)
        subscription.provider_subscription_id = payment.invoice_id
        subscription.status = account_models.BillingSubscriptionStatus.ACTIVE
        subscription.plan_code = plan.code
        subscription.currency = plan.currency
        subscription.unit_amount_cents = plan.unit_amount_cents
        subscription.interval = plan.interval or "month"
        subscription.current_period_start = base_start
        subscription.current_period_end = target_end
        subscription.cancel_at_period_end = False
        subscription.latest_invoice_id = payment.invoice_id
        subscription.hosted_checkout_url = self._extract_invoice_url(payment.raw_payload) or subscription.hosted_checkout_url
        subscription.synced_at = now_utc
        payment.period_end_at = target_end
        session.add(subscription)
        await session.flush()
        self._log_marketing_event(account, "subscription_activation", subscription)
        return subscription

    @staticmethod
    def _extract_invoice_url(payload: object) -> str | None:
        source = None
        if isinstance(payload, account_models.BillingCryptoPayment):
            source = payload.raw_payload
        elif isinstance(payload, dict):
            source = payload
        if not isinstance(source, dict):
            return None
        url = (
            source.get("invoice_url")
            or source.get("checkout_url")
            or source.get("pay_address")
            or source.get("hosted_url")
        )
        if not url:
            return None
        return str(url)

    @staticmethod
    def _payload_preview(payload: str | None, *, limit: int = 1200) -> str | None:
        if not payload:
            return None
        if len(payload) <= limit:
            return payload
        return f"{payload[:limit]}... (truncated)"

    def _verify_crypto_signature(self, payload: str, signature: str, secret: str) -> bool:
        if not signature:
            return False
        digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha512).hexdigest()
        return digest == signature

    def _find_network_by_chain(self, chain_code: str) -> "BillingCryptoNetworkSettings | None":
        crypto_cfg = self.config.crypto
        if not crypto_cfg:
            return None
        for network in crypto_cfg.networks.values():
            if network.chain == chain_code:
                return network
        return None

    async def _ensure_crypto_checkout_session(
        self,
        session: AsyncSession,
        *,
        account: account_models.Account,
        plan_code: str,
        target_period_end: datetime,
    ) -> CheckoutSessionResult:
        if self.config.provider != "crypto":
            raise BillingConfigurationError("Crypto provider is not configured.")
        normalized_target = target_period_end
        if normalized_target.tzinfo is None:
            normalized_target = normalized_target.replace(tzinfo=timezone.utc)

        pending_statuses = (
            account_models.BillingCryptoPaymentStatus.PENDING,
            account_models.BillingCryptoPaymentStatus.PROCESSING,
        )
        stmt = (
            select(account_models.BillingCryptoPayment)
            .where(
                account_models.BillingCryptoPayment.account_id == account.id,
                account_models.BillingCryptoPayment.plan_code == plan_code,
                account_models.BillingCryptoPayment.status.in_(pending_statuses),
                account_models.BillingCryptoPayment.period_end_at.is_not(None),
            )
            .order_by(account_models.BillingCryptoPayment.created_at.desc())
        )
        payments = await session.scalars(stmt)
        for payment in payments:
            if payment.period_end_at and payment.period_end_at >= normalized_target:
                hosted_url = self._extract_invoice_url(payment)
                if hosted_url:
                    return CheckoutSessionResult(url=hosted_url, session_id=payment.invoice_id)

        return await self._create_crypto_checkout_session(
            session,
            account=account,
            plan_code=plan_code,
            period_end_at=normalized_target,
        )

    async def send_crypto_renewal_notifications(self, session: AsyncSession) -> int:
        if self.config.provider != "crypto":
            return 0
        now_utc = _utcnow()
        reminder_days = {3, 1}
        stmt = (
            select(account_models.BillingSubscription)
            .options(selectinload(account_models.BillingSubscription.account))
            .where(
                account_models.BillingSubscription.status.in_(self._active_statuses),
                account_models.BillingSubscription.current_period_end.is_not(None),
            )
        )
        subscriptions = (await session.execute(stmt)).scalars().all()
        sent_count = 0
        for subscription in subscriptions:
            period_end = subscription.current_period_end
            if not period_end:
                continue
            days_left = (period_end.date() - now_utc.date()).days
            if days_left not in reminder_days:
                continue
            if subscription.cancel_at_period_end:
                continue
            account = subscription.account or await session.get(account_models.Account, subscription.account_id)
            if not account or not account.email:
                continue
            try:
                plan = self._get_plan(subscription.plan_code)
            except BillingPlanNotFound:
                logger.warning(
                    "crypto_renewal_missing_plan",
                    extra={"account_id": str(subscription.account_id), "plan_code": subscription.plan_code},
                )
                continue
            target_period_end = period_end + timedelta(days=self._plan_interval_days(plan))
            reminder_event_id = f"crypto.reminder.{subscription.id}.{period_end.date().isoformat()}.d{days_left}"
            existing_event = await session.scalar(
                select(account_models.BillingEvent.id).where(
                    account_models.BillingEvent.provider_event_id == reminder_event_id
                )
            )
            if existing_event:
                continue
            try:
                checkout_session = await self._ensure_crypto_checkout_session(
                    session,
                    account=account,
                    plan_code=subscription.plan_code,
                    target_period_end=target_period_end,
                )
            except BillingError:
                logger.exception(
                    "crypto_renewal_invoice_failed",
                    extra={"account_id": str(account.id), "plan_code": subscription.plan_code},
                )
                continue
            subscription.hosted_checkout_url = checkout_session.url or subscription.hosted_checkout_url
            subscription.synced_at = now_utc
            created_event = await self._record_billing_event(
                session,
                provider_event_id=reminder_event_id,
                event_type="crypto.renewal.reminder",
                account=account,
                subscription=subscription,
                payload={
                    "period_end_at": period_end.isoformat(),
                    "invoice_id": checkout_session.session_id,
                    "days_left": days_left,
                },
                provider=account_models.BillingProvider.CRYPTO,
            )
            if not created_event:
                continue
            email_notifications.send_crypto_renewal_reminder_email(
                recipient=account.email,
                full_name=account.full_name,
                plan_name=plan.name,
                expires_at=period_end,
                days_left=days_left,
                invoice_url=checkout_session.url,
            )
            sent_count += 1
        await session.commit()
        return sent_count

    async def send_crypto_stuck_payment_alerts(self, session: AsyncSession) -> int:
        if self.config.provider != "crypto":
            return 0
        now_utc = _utcnow()
        pending_minutes = int(os.getenv("AICI_CRYPTO_PENDING_ALERT_MINUTES", "30") or 30)
        processing_minutes = int(os.getenv("AICI_CRYPTO_PROCESSING_ALERT_MINUTES", "90") or 90)
        pending_cutoff = now_utc - timedelta(minutes=max(pending_minutes, 1))
        processing_cutoff = now_utc - timedelta(minutes=max(processing_minutes, 1))

        stmt = (
            select(account_models.BillingCryptoPayment)
            .options(selectinload(account_models.BillingCryptoPayment.account))
            .where(
                account_models.BillingCryptoPayment.status.in_(
                    (
                        account_models.BillingCryptoPaymentStatus.PENDING,
                        account_models.BillingCryptoPaymentStatus.PROCESSING,
                    )
                )
            )
            .order_by(account_models.BillingCryptoPayment.created_at.asc())
        )
        payments = (await session.execute(stmt)).scalars().all()
        alerted = 0
        for payment in payments:
            if payment.status == account_models.BillingCryptoPaymentStatus.PENDING and payment.created_at > pending_cutoff:
                continue
            if payment.status == account_models.BillingCryptoPaymentStatus.PROCESSING and payment.created_at > processing_cutoff:
                continue
            account = payment.account or await session.get(account_models.Account, payment.account_id)
            network = self._find_network_by_chain(payment.chain.value)
            confirmations_required = network.confirmations_required if network else None
            age_minutes = int((now_utc - payment.created_at).total_seconds() // 60)
            alert_event_id = f"crypto.alert.stuck.{payment.invoice_id}.{payment.status.value}"
            created_event = await self._record_billing_event(
                session,
                provider_event_id=alert_event_id,
                event_type="crypto.payment.stuck",
                account=account,
                subscription=None,
                payload={
                    "invoice_id": payment.invoice_id,
                    "status": payment.status.value,
                    "confirmations": payment.confirmations,
                    "confirmations_required": confirmations_required,
                    "age_minutes": age_minutes,
                },
                provider=account_models.BillingProvider.CRYPTO,
            )
            if not created_event:
                continue
            alerted += 1
            email_notifications.send_crypto_confirmation_delay_alert(
                invoice_id=payment.invoice_id,
                account_email=account.email if account else None,
                plan_code=payment.plan_code,
                status=payment.status.value,
                confirmations=payment.confirmations,
                confirmations_required=confirmations_required,
                age_minutes=age_minutes,
                hosted_url=self._extract_invoice_url(payment),
            )
        if alerted:
            await session.commit()
        return alerted

    async def _call_nowpayments(
        self,
        method: str,
        path: str,
        payload: dict[str, Any],
        *,
        api_key: str,
    ) -> dict[str, Any]:
        url = f"{NOWPAYMENTS_API_BASE}{path}"
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}

        def _request() -> dict[str, Any]:
            response = requests.request(method, url, json=payload, headers=headers, timeout=20)
            response.raise_for_status()
            return response.json()

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _request)
        except requests.RequestException as exc:
            logger.exception("NOWPayments API call failed: %s", exc)
            raise BillingError(str(exc)) from exc
