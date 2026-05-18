from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from ipaddress import ip_network
from datetime import date
from typing import Sequence

from cryptography.fernet import Fernet
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai_crypto_index.accounts import models as account_models
from ai_crypto_index.accounts.security import utcnow
from ai_crypto_index.shared import email_notifications, notification_hooks
from ai_crypto_index.shared.settings import ApiKeyPlanSettings, ServiceSettings

from .exceptions import (
    ApiKeyInactive,
    ApiKeyLimitReached,
    ApiKeyNotFound,
    ApiKeyQuotaExceeded,
    ApiKeyRestrictionError,
    InvalidApiKeySecret,
)

ACTIVE_SUBSCRIPTION_STATUSES = {
    account_models.BillingSubscriptionStatus.TRIALING,
    account_models.BillingSubscriptionStatus.ACTIVE,
    account_models.BillingSubscriptionStatus.PAST_DUE,
}


@dataclass(slots=True)
class IssuedApiKey:
    api_key: account_models.ApiKey
    secret: str


@dataclass(slots=True)
class ApiKeyUsageSnapshot:
    daily_calls: int
    monthly_calls: int


@dataclass(slots=True)
class ApiKeyLimits:
    daily_quota: int | None
    monthly_quota: int | None
    burst_per_minute: int
    burst_per_second: int
    data_latency_seconds: int


@dataclass(slots=True)
class ApiKeyAuthContext:
    api_key: account_models.ApiKey
    account: account_models.Account
    plan: ApiKeyPlanSettings
    limits: ApiKeyLimits
    usage: ApiKeyUsageSnapshot | None = None


def _normalize_label(raw_value: str | None, fallback: str) -> str:
    if raw_value is None:
        return fallback
    trimmed = raw_value.strip()
    return trimmed or fallback


def _normalize_tags(values: Sequence[str] | None) -> list[str] | None:
    if not values:
        return None
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        trimmed = value.strip()
        if not trimmed:
            continue
        cleaned.append(trimmed[:40])
        if len(cleaned) >= 8:
            break
    return cleaned or None


def _month_start(day: date) -> date:
    return day.replace(day=1)


class ApiKeyService:
    def __init__(self, settings: ServiceSettings) -> None:
        self.settings = settings
        self._cipher = self._build_cipher(settings.api_keys.encryption_secret)

    def _build_cipher(self, secret: str) -> Fernet:
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        key_bytes = base64.urlsafe_b64encode(digest)
        return Fernet(key_bytes)

    def _hash_secret(self, secret: str) -> bytes:
        return hashlib.sha256(secret.encode("utf-8")).digest()

    def _encrypt_secret(self, secret: str) -> bytes:
        return self._cipher.encrypt(secret.encode("utf-8"))

    def _generate_secret(self) -> tuple[str, str, str]:
        prefix = self.settings.api_keys.key_prefix
        token = secrets.token_urlsafe(32)
        secret = f"{prefix}{token}"
        return secret, secret[:6], secret[-6:]

    def _resolve_plan(self, account: account_models.Account) -> ApiKeyPlanSettings:
        plan_code = self.settings.api_keys.default_plan_code
        subscriptions = account.billing_subscriptions or []
        if subscriptions:
            now = utcnow()

            def _normalize(dt):
                if dt is None:
                    return None
                return dt if dt.tzinfo else dt.replace(tzinfo=now.tzinfo)

            sorted_subs = sorted(
                subscriptions,
                key=lambda item: (
                    0 if item.status in ACTIVE_SUBSCRIPTION_STATUSES else 1,
                    item.updated_at or item.created_at,
                ),
            )
            for candidate in sorted_subs:
                if candidate.status in ACTIVE_SUBSCRIPTION_STATUSES:
                    period_end = _normalize(candidate.current_period_end)
                    if period_end and period_end <= now:
                        continue
                    plan_code = candidate.plan_code.lower()
                    break
        plan_settings = self.settings.api_keys.plans.get(plan_code)
        if plan_settings is None:
            plan_settings = self.settings.api_keys.plans[self.settings.api_keys.default_plan_code]
        return plan_settings

    def _normalize_ip_allowlist(self, values: Sequence[str] | None) -> list[str] | None:
        if not values:
            return None
        cleaned: list[str] = []
        for entry in values:
            candidate = str(entry or "").strip()
            if not candidate:
                continue
            try:
                ip_network(candidate, strict=False)
            except ValueError as exc:  # pragma: no cover - validation guard
                raise ApiKeyRestrictionError("ip_allowlist", f"invalid_ip:{candidate}") from exc
            cleaned.append(candidate)
            if len(cleaned) >= 16:
                break
        return cleaned or None

    def _normalize_label_constraints(self, values: Sequence[str] | None) -> list[str] | None:
        if not values:
            return None
        cleaned: list[str] = []
        for entry in values:
            candidate = str(entry or "").strip().lower()
            if not candidate:
                continue
            cleaned.append(candidate[:40])
            if len(cleaned) >= 8:
                break
        return cleaned or None

    def _apply_restrictions(
        self,
        api_key: account_models.ApiKey,
        *,
        ip_allowlist: Sequence[str] | None = None,
        label_constraints: Sequence[str] | None = None,
    ) -> None:
        if ip_allowlist is None and label_constraints is None:
            return
        attributes_raw = api_key.attributes if isinstance(api_key.attributes, dict) else {}
        attributes = dict(attributes_raw)
        if ip_allowlist is not None:
            attributes["ip_allowlist"] = list(ip_allowlist)
        if label_constraints is not None:
            attributes["label_constraints"] = list(label_constraints)
        cleaned = {key: value for key, value in attributes.items() if value}
        api_key.attributes = cleaned or None

    @staticmethod
    def _read_restriction_list(api_key: account_models.ApiKey, key: str) -> list[str]:
        attributes = api_key.attributes or {}
        raw = attributes.get(key)
        if not isinstance(raw, list):
            return []
        cleaned: list[str] = []
        for entry in raw:
            candidate = str(entry or "").strip()
            if candidate:
                cleaned.append(candidate)
        return cleaned

    @staticmethod
    def _is_deleted(api_key: account_models.ApiKey) -> bool:
        attributes = api_key.attributes if isinstance(api_key.attributes, dict) else {}
        return bool(attributes.get("deleted_at"))

    def get_plan_for_account(self, account: account_models.Account) -> ApiKeyPlanSettings:
        return self._resolve_plan(account)

    def _build_limits(self, api_key: account_models.ApiKey, plan: ApiKeyPlanSettings) -> ApiKeyLimits:
        daily = api_key.daily_quota_override if api_key.daily_quota_override is not None else plan.daily_quota
        monthly = api_key.monthly_quota_override if api_key.monthly_quota_override is not None else plan.monthly_quota
        burst_minute = (
            api_key.burst_limit_override if api_key.burst_limit_override is not None else plan.burst_per_minute
        )
        data_latency = (
            api_key.data_latency_override if api_key.data_latency_override is not None else plan.data_latency_seconds
        )
        return ApiKeyLimits(
            daily_quota=daily,
            monthly_quota=monthly,
            burst_per_minute=max(burst_minute, 1),
            burst_per_second=max(plan.burst_per_second, 1),
            data_latency_seconds=max(data_latency, 0),
        )

    def derive_plan_and_limits(
        self,
        api_key: account_models.ApiKey,
        account: account_models.Account | None = None,
    ) -> tuple[ApiKeyPlanSettings, ApiKeyLimits]:
        owner = account or api_key.account
        if owner is None:
            raise ValueError("API key owner is not loaded.")
        plan = self._resolve_plan(owner)
        limits = self._build_limits(api_key, plan)
        return plan, limits

    def _normalize_cost(self, cost: int | None) -> int:
        pricing = getattr(self.settings.api_keys, "token_pricing", None)
        minimum_debit = pricing.minimum_debit_tokens if pricing else 1
        try:
            normalized = int(cost) if cost is not None else 0
        except (TypeError, ValueError):
            normalized = 0
        if normalized == 0:
            return 0
        return max(normalized, minimum_debit)

    async def _count_active_keys(self, session: AsyncSession, account_id: uuid.UUID) -> int:
        stmt: Select[int] = select(func.count()).select_from(account_models.ApiKey).where(
            account_models.ApiKey.account_id == account_id,
            account_models.ApiKey.status != account_models.ApiKeyStatus.REVOKED,
        )
        return int(await session.scalar(stmt) or 0)

    async def list_keys_for_account(self, session: AsyncSession, account_id: uuid.UUID) -> list[account_models.ApiKey]:
        stmt = (
            select(account_models.ApiKey)
            .where(account_models.ApiKey.account_id == account_id)
            .order_by(account_models.ApiKey.created_at.desc())
        )
        results = await session.scalars(stmt)
        return [key for key in results if not self._is_deleted(key)]

    async def list_recent_keys(
        self,
        session: AsyncSession,
        *,
        limit: int = 50,
    ) -> list[account_models.ApiKey]:
        fetch_limit = max(limit * 3, limit + 5)
        stmt = (
            select(account_models.ApiKey)
            .options(selectinload(account_models.ApiKey.account))
            .order_by(
                account_models.ApiKey.last_used_at.desc().nullslast(),
                account_models.ApiKey.created_at.desc(),
            )
            .limit(fetch_limit)
        )
        result = await session.scalars(stmt)
        keys: list[account_models.ApiKey] = []
        for key in result:
            if self._is_deleted(key) and not key.last_used_at:
                continue
            if key.status == account_models.ApiKeyStatus.REVOKED and not key.last_used_at:
                continue
            keys.append(key)
            if len(keys) >= limit:
                break
        return keys

    async def list_audit_events(
        self,
        session: AsyncSession,
        api_key_id: uuid.UUID,
        *,
        limit: int = 100,
    ) -> list[account_models.ApiKeyAuditEvent]:
        stmt = (
            select(account_models.ApiKeyAuditEvent)
            .where(account_models.ApiKeyAuditEvent.api_key_id == api_key_id)
            .order_by(account_models.ApiKeyAuditEvent.created_at.desc())
            .limit(limit)
        )
        result = await session.scalars(stmt)
        return list(result)

    async def get_key(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        key_id: uuid.UUID,
    ) -> account_models.ApiKey:
        stmt = (
            select(account_models.ApiKey)
            .where(
                account_models.ApiKey.account_id == account_id,
                account_models.ApiKey.id == key_id,
            )
            .options(selectinload(account_models.ApiKey.account))
        )
        api_key = await session.scalar(stmt)
        if not api_key or self._is_deleted(api_key):
            raise ApiKeyNotFound("api_key_not_found")
        return api_key

    async def get_key_global(
        self,
        session: AsyncSession,
        key_id: uuid.UUID,
    ) -> account_models.ApiKey:
        stmt = (
            select(account_models.ApiKey)
            .where(account_models.ApiKey.id == key_id)
            .options(selectinload(account_models.ApiKey.account))
        )
        api_key = await session.scalar(stmt)
        if not api_key or self._is_deleted(api_key):
            raise ApiKeyNotFound("api_key_not_found")
        return api_key

    async def issue_key(
        self,
        session: AsyncSession,
        account: account_models.Account,
        *,
        label: str,
        role: str | None,
        tags: Sequence[str] | None,
        created_by: str | None,
        application_name: str | None = None,
        ip_allowlist: Sequence[str] | None = None,
        label_constraints: Sequence[str] | None = None,
        actor_ip: str | None = None,
    ) -> IssuedApiKey:
        plan = self._resolve_plan(account)
        allowed_roles = tuple(str(r).lower() for r in (plan.roles or (plan.default_role,)))
        desired_role = (role or plan.default_role).lower()
        if desired_role not in allowed_roles:
            desired_role = plan.default_role
        if desired_role not in account_models.ApiKeyRole._value2member_map_:
            desired_role = plan.default_role

        max_keys_allowed = plan.max_keys or self.settings.api_keys.max_keys_per_account
        existing = await self._count_active_keys(session, account.id)
        if existing >= max_keys_allowed:
            raise ApiKeyLimitReached(f"max_{max_keys_allowed}_keys_reached")

        secret, prefix, suffix = self._generate_secret()
        hashed = self._hash_secret(secret)
        encrypted = self._encrypt_secret(secret)

        application_field = None
        if application_name:
            application_field = application_name.strip() or None

        api_key = account_models.ApiKey(
            account_id=account.id,
            label=_normalize_label(label, f"{plan.code.title()} key"),
            application_name=application_field,
            tags=_normalize_tags(tags),
            role=account_models.ApiKeyRole(desired_role),
            status=account_models.ApiKeyStatus.ACTIVE,
            plan_code=plan.code,
            token_prefix=prefix,
            token_suffix=suffix,
            token_hash=hashed,
            token_encrypted=encrypted,
            created_by=created_by,
        )
        normalized_ip_allowlist = self._normalize_ip_allowlist(ip_allowlist)
        normalized_labels = self._normalize_label_constraints(label_constraints)
        self._apply_restrictions(
            api_key,
            ip_allowlist=normalized_ip_allowlist,
            label_constraints=normalized_labels,
        )
        session.add(api_key)
        await session.flush()
        await self._record_audit_event(
            session,
            api_key,
            event_type=account_models.ApiKeyAuditEventType.ISSUED,
            actor=created_by,
            actor_ip=actor_ip,
            description=f'Issued API key "{api_key.label}".',
            payload={
                "tags": list(api_key.tags or []),
                "ip_allowlist": normalized_ip_allowlist or [],
                "label_constraints": normalized_labels or [],
            },
        )
        await session.commit()
        await session.refresh(api_key)
        return IssuedApiKey(api_key=api_key, secret=secret)

    async def rotate_key(
        self,
        session: AsyncSession,
        api_key: account_models.ApiKey,
        *,
        rotated_by: str | None,
        actor_ip: str | None = None,
    ) -> IssuedApiKey:
        if api_key.status != account_models.ApiKeyStatus.ACTIVE:
            raise ApiKeyInactive("api_key_not_active")
        secret, prefix, suffix = self._generate_secret()
        api_key.token_hash = self._hash_secret(secret)
        api_key.token_encrypted = self._encrypt_secret(secret)
        api_key.token_prefix = prefix
        api_key.token_suffix = suffix
        api_key.updated_by = rotated_by
        await session.flush()
        await self._record_audit_event(
            session,
            api_key,
            event_type=account_models.ApiKeyAuditEventType.ROTATED,
            actor=rotated_by,
            actor_ip=actor_ip,
            description=f'Rotated API key "{api_key.label}".',
            payload={
                "ip_allowlist": self._read_restriction_list(api_key, "ip_allowlist"),
                "label_constraints": self._read_restriction_list(api_key, "label_constraints"),
            },
        )
        await session.commit()
        await session.refresh(api_key)
        self._notify_rotation(api_key, rotated_by=rotated_by, actor_ip=actor_ip)
        return IssuedApiKey(api_key=api_key, secret=secret)

    async def revoke_key(
        self,
        session: AsyncSession,
        api_key: account_models.ApiKey,
        *,
        revoked_by: str | None,
        reason: str | None = None,
    ) -> account_models.ApiKey:
        api_key.status = account_models.ApiKeyStatus.REVOKED
        api_key.revoked_at = utcnow()
        api_key.revoked_reason = reason
        api_key.updated_by = revoked_by
        await session.flush()
        await self._record_audit_event(
            session,
            api_key,
            event_type=account_models.ApiKeyAuditEventType.REVOKED,
            actor=revoked_by,
            actor_ip=None,
            description=f'Revoked API key "{api_key.label}".',
            payload={"reason": reason or ""},
        )
        await session.commit()
        await session.refresh(api_key)
        return api_key

    async def delete_key(
        self,
        session: AsyncSession,
        api_key: account_models.ApiKey,
        *,
        deleted_by: str | None,
        reason: str | None = None,
    ) -> None:
        if api_key.status != account_models.ApiKeyStatus.REVOKED:
            raise ApiKeyRestrictionError("delete_requires_revoked")
        if self._is_deleted(api_key):
            return
        attributes = dict(api_key.attributes or {})
        deleted_at = utcnow()
        attributes["deleted_at"] = deleted_at.isoformat()
        if deleted_by:
            attributes["deleted_by"] = deleted_by
        if reason:
            attributes["deleted_reason"] = reason
            api_key.notes = reason
        api_key.attributes = attributes or None
        api_key.updated_by = deleted_by
        api_key.revoked_at = api_key.revoked_at or deleted_at
        await session.flush()
        await session.commit()

    async def update_status(
        self,
        session: AsyncSession,
        api_key: account_models.ApiKey,
        *,
        status: account_models.ApiKeyStatus,
        updated_by: str | None = None,
        reason: str | None = None,
    ) -> account_models.ApiKey:
        api_key.status = status
        api_key.updated_by = updated_by
        if reason:
            api_key.notes = reason
        if status == account_models.ApiKeyStatus.REVOKED:
            api_key.revoked_at = utcnow()
        await session.flush()
        await self._record_audit_event(
            session,
            api_key,
            event_type=account_models.ApiKeyAuditEventType.UPDATED,
            actor=updated_by,
            actor_ip=None,
            description=f"Updated status to {status.value}.",
            payload={"reason": reason or ""},
        )
        await session.commit()
        await session.refresh(api_key)
        return api_key

    async def update_key(
        self,
        session: AsyncSession,
        api_key: account_models.ApiKey,
        *,
        account: account_models.Account,
        label: str | None = None,
        application_name: str | None = None,
        tags: Sequence[str] | None = None,
        role: str | None = None,
        ip_allowlist: Sequence[str] | None = None,
        label_constraints: Sequence[str] | None = None,
        updated_by: str | None = None,
        actor_ip: str | None = None,
    ) -> account_models.ApiKey:
        changes: dict[str, object] = {}
        if label is not None:
            normalized_label = _normalize_label(label, api_key.label)
            if normalized_label != api_key.label:
                api_key.label = normalized_label
                changes["label"] = normalized_label
        if application_name is not None:
            cleaned_app = application_name.strip() or None
            if cleaned_app != api_key.application_name:
                api_key.application_name = cleaned_app
                changes["application_name"] = cleaned_app or ""
        if tags is not None:
            normalized_tags = _normalize_tags(tags) or []
            if normalized_tags != (api_key.tags or []):
                api_key.tags = normalized_tags
                changes["tags"] = normalized_tags
        if role is not None:
            plan = self._resolve_plan(account)
            allowed_roles = tuple(str(r).lower() for r in (plan.roles or (plan.default_role,)))
            desired_role = str(role).strip().lower()
            if desired_role not in allowed_roles:
                raise ApiKeyRestrictionError("role", "role_not_allowed")
            api_key.role = account_models.ApiKeyRole(desired_role)
            changes["role"] = desired_role
        normalized_ip_allowlist = None
        normalized_labels = None
        if ip_allowlist is not None:
            normalized_ip_allowlist = self._normalize_ip_allowlist(ip_allowlist)
            changes["ip_allowlist"] = normalized_ip_allowlist or []
        if label_constraints is not None:
            normalized_labels = self._normalize_label_constraints(label_constraints)
            changes["label_constraints"] = normalized_labels or []
        self._apply_restrictions(
            api_key,
            ip_allowlist=normalized_ip_allowlist,
            label_constraints=normalized_labels,
        )
        if updated_by:
            api_key.updated_by = updated_by
        await session.flush()
        if changes:
            await self._record_audit_event(
                session,
                api_key,
                event_type=account_models.ApiKeyAuditEventType.UPDATED,
                actor=updated_by,
                actor_ip=actor_ip,
                description=f'Updated API key "{api_key.label}".',
                payload={"changes": changes},
            )
        await session.commit()
        await session.refresh(api_key)
        return api_key

    async def authenticate(self, session: AsyncSession, secret: str) -> ApiKeyAuthContext:
        hashed = self._hash_secret(secret)
        stmt = (
            select(account_models.ApiKey)
            .where(account_models.ApiKey.token_hash == hashed)
            .options(
                selectinload(account_models.ApiKey.account)
                .selectinload(account_models.Account.billing_subscriptions),
                selectinload(account_models.ApiKey.account).selectinload(account_models.Account.limits),
            )
        )
        api_key = await session.scalar(stmt)
        if not api_key:
            raise InvalidApiKeySecret("invalid_api_key")
        if self._is_deleted(api_key):
            raise ApiKeyInactive("api_key_not_active")
        if api_key.status != account_models.ApiKeyStatus.ACTIVE:
            raise ApiKeyInactive("api_key_not_active")
        if api_key.expires_at and api_key.expires_at < utcnow():
            raise ApiKeyInactive("api_key_expired")
        plan = self._resolve_plan(api_key.account)
        limits = self._build_limits(api_key, plan)
        return ApiKeyAuthContext(
            api_key=api_key,
            account=api_key.account,
            plan=plan,
            limits=limits,
        )

    async def fetch_usage_snapshot(
        self,
        session: AsyncSession,
        api_key: account_models.ApiKey,
    ) -> ApiKeyUsageSnapshot:
        today = utcnow().date()
        month_start = _month_start(today)
        daily_stmt = select(account_models.ApiKeyUsageDaily).where(
            account_models.ApiKeyUsageDaily.api_key_id == api_key.id,
            account_models.ApiKeyUsageDaily.usage_date == today,
        )
        monthly_stmt = select(account_models.ApiKeyUsageMonthly).where(
            account_models.ApiKeyUsageMonthly.api_key_id == api_key.id,
            account_models.ApiKeyUsageMonthly.period_start == month_start,
        )
        daily_row = await session.scalar(daily_stmt)
        monthly_row = await session.scalar(monthly_stmt)
        return ApiKeyUsageSnapshot(
            daily_calls=daily_row.call_count if daily_row else 0,
            monthly_calls=monthly_row.call_count if monthly_row else 0,
        )

    async def record_usage(
        self,
        session: AsyncSession,
        context: ApiKeyAuthContext,
        *,
        cost: int = 1,
        route_name: str | None = None,
    ) -> ApiKeyUsageSnapshot:
        token_cost = self._normalize_cost(cost)
        today = utcnow().date()
        month_start = _month_start(today)
        daily = await self._get_or_create_daily(session, context.api_key.id, today)
        monthly = await self._get_or_create_monthly(session, context.api_key.id, month_start)

        if context.limits.daily_quota and daily.call_count + token_cost > context.limits.daily_quota:
            raise ApiKeyQuotaExceeded("daily", context.limits.daily_quota)
        if context.limits.monthly_quota and monthly.call_count + token_cost > context.limits.monthly_quota:
            raise ApiKeyQuotaExceeded("monthly", context.limits.monthly_quota)

        daily.call_count += token_cost
        daily.compute_units += token_cost
        monthly.call_count += token_cost
        monthly.compute_units += token_cost
        context.api_key.last_used_at = utcnow()
        await session.flush()
        await self._maybe_notify_threshold(session, context, monthly)
        await session.commit()
        snapshot = ApiKeyUsageSnapshot(daily_calls=daily.call_count, monthly_calls=monthly.call_count)
        context.usage = snapshot
        return snapshot

    async def _get_or_create_daily(
        self,
        session: AsyncSession,
        api_key_id: uuid.UUID,
        usage_date: date,
    ) -> account_models.ApiKeyUsageDaily:
        stmt = select(account_models.ApiKeyUsageDaily).where(
            account_models.ApiKeyUsageDaily.api_key_id == api_key_id,
            account_models.ApiKeyUsageDaily.usage_date == usage_date,
        )
        row = await session.scalar(stmt)
        if row:
            return row
        row = account_models.ApiKeyUsageDaily(api_key_id=api_key_id, usage_date=usage_date, call_count=0)
        session.add(row)
        await session.flush()
        return row

    async def _get_or_create_monthly(
        self,
        session: AsyncSession,
        api_key_id: uuid.UUID,
        period_start: date,
    ) -> account_models.ApiKeyUsageMonthly:
        stmt = select(account_models.ApiKeyUsageMonthly).where(
            account_models.ApiKeyUsageMonthly.api_key_id == api_key_id,
            account_models.ApiKeyUsageMonthly.period_start == period_start,
        )
        row = await session.scalar(stmt)
        if row:
            return row
        row = account_models.ApiKeyUsageMonthly(api_key_id=api_key_id, period_start=period_start, call_count=0)
        session.add(row)
        await session.flush()
        return row

    async def _maybe_notify_threshold(
        self,
        session: AsyncSession,
        context: ApiKeyAuthContext,
        monthly_row: account_models.ApiKeyUsageMonthly,
    ) -> None:
        threshold_limits = self.settings.api_keys.notification_thresholds
        monthly_limit = context.limits.monthly_quota
        if not threshold_limits or not monthly_limit:
            return
        usage_ratio = monthly_row.call_count / monthly_limit
        last_notified = (monthly_row.last_notified_threshold or 0) / 100
        for threshold in threshold_limits:
            if usage_ratio >= threshold > last_notified:
                monthly_row.last_notified_threshold = int(threshold * 100)
                email_notifications.send_api_usage_alert(
                    account=context.account,
                    api_key=context.api_key,
                    threshold=threshold,
                    usage_ratio=usage_ratio,
                    monthly_usage_tokens=monthly_row.call_count,
                    monthly_quota_tokens=monthly_limit,
                )
                notification_hooks.deliver_webhook_events(
                    "api_key.usage_threshold",
                    {
                        "api_key_id": str(context.api_key.id),
                        "account_id": str(context.account.id),
                        "threshold": threshold,
                        "usage_ratio": usage_ratio,
                        "plan_code": context.plan.code,
                        "monthly_usage_tokens": monthly_row.call_count,
                        "monthly_quota_tokens": monthly_limit,
                    },
                    self.settings.api_keys.usage_alert_webhook_urls,
                )
                await self._record_audit_event(
                    session,
                    context.api_key,
                    event_type=account_models.ApiKeyAuditEventType.LIMIT_ALERT,
                    actor=None,
                    actor_ip=None,
                    description=f"Usage reached {int(threshold * 100)}% of the monthly token quota.",
                    payload={
                        "threshold": threshold,
                        "usage_ratio": usage_ratio,
                        "monthly_limit": monthly_limit,
                        "monthly_usage_tokens": monthly_row.call_count,
                    },
                )
        custom_rules_stmt = (
            select(account_models.UsageAlertRule)
            .where(
                account_models.UsageAlertRule.account_id == context.account.id,
                account_models.UsageAlertRule.enabled.is_(True),
            )
        )
        custom_rules = await session.scalars(custom_rules_stmt)
        for rule in custom_rules:
            rule_threshold = max(rule.threshold_percent, 1) / 100
            last_notified = (rule.last_triggered_percent or 0) / 100
            if usage_ratio >= rule_threshold > last_notified:
                if rule.channel_type == account_models.ChannelType.EMAIL:
                    email_notifications.send_api_usage_alert(
                        account=context.account,
                        api_key=context.api_key,
                        threshold=rule_threshold,
                        usage_ratio=usage_ratio,
                        recipient=rule.destination,
                        recipient_name=rule.label,
                        monthly_usage_tokens=monthly_row.call_count,
                        monthly_quota_tokens=monthly_limit,
                    )
                else:
                    notification_hooks.deliver_webhook_events(
                        "api_key.usage_threshold",
                        {
                            "api_key_id": str(context.api_key.id),
                            "account_id": str(context.account.id),
                            "threshold": rule_threshold,
                            "usage_ratio": usage_ratio,
                            "plan_code": context.plan.code,
                            "monthly_usage_tokens": monthly_row.call_count,
                            "monthly_quota_tokens": monthly_limit,
                        },
                        (rule.destination,),
                    )
                rule.last_triggered_percent = int(usage_ratio * 100)
                rule.last_triggered_at = utcnow()
        await session.flush()


    async def _record_audit_event(
        self,
        session: AsyncSession,
        api_key: account_models.ApiKey,
        *,
        event_type: account_models.ApiKeyAuditEventType,
        actor: str | None,
        actor_ip: str | None,
        description: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        if not api_key.id:
            await session.flush()
        event = account_models.ApiKeyAuditEvent(
            api_key_id=api_key.id,
            account_id=api_key.account_id,
            event_type=event_type,
            actor=actor,
            actor_ip=actor_ip,
            description=description,
            payload=payload,
        )
        session.add(event)
        await session.flush()

    def _notify_rotation(
        self,
        api_key: account_models.ApiKey,
        *,
        rotated_by: str | None,
        actor_ip: str | None,
    ) -> None:
        account = api_key.account
        if account:
            email_notifications.send_api_key_rotated_email(
                account=account,
                api_key=api_key,
                rotated_by=rotated_by,
                actor_ip=actor_ip,
            )
        notification_hooks.deliver_webhook_events(
            "api_key.rotated",
            {
                "api_key_id": str(api_key.id),
                "account_id": str(api_key.account_id),
                "label": api_key.label,
                "rotated_by": rotated_by or "",
                "actor_ip": actor_ip or "",
                "rotated_at": utcnow().isoformat(),
            },
            self.settings.api_keys.rotation_webhook_urls,
        )


__all__ = [
    "ApiKeyAuthContext",
    "ApiKeyLimits",
    "ApiKeyService",
    "ApiKeyUsageSnapshot",
    "IssuedApiKey",
]
