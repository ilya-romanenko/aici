from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ai_crypto_index.shared.settings import ServiceSettings
from . import models
from .exceptions import (
    AccountAlreadyExists,
    AccountInactive,
    AccountNotFound,
    ConfirmationResendRateLimited,
    InvalidCredentials,
    SessionInvalid,
    TokenExpired,
    TokenInvalid,
)
from .security import (
    build_expiry,
    create_access_token,
    generate_token,
    hash_password,
    hash_token,
    utcnow,
    verify_password,
)


@dataclass(slots=True)
class RequestContext:
    ip_address: str | None
    user_agent: str | None


@dataclass(slots=True)
class SignupResult:
    account: models.Account
    confirmation_token: str
    confirmation_expires_at: datetime


@dataclass(slots=True)
class SessionResult:
    account: models.Account
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    session_id: uuid.UUID


ACTIVE_SUBSCRIPTION_STATUSES = {
    models.BillingSubscriptionStatus.TRIALING,
    models.BillingSubscriptionStatus.ACTIVE,
    models.BillingSubscriptionStatus.PAST_DUE,
}


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class AccountService:
    def __init__(self, settings: ServiceSettings) -> None:
        self.settings = settings

    async def signup(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        newsletter_opt_in: bool,
        terms_version: str,
        context: RequestContext,
    ) -> SignupResult:
        normalized_email = email.strip().lower()
        existing_stmt = select(models.Account).where(
            func.lower(models.Account.email) == normalized_email
        )
        existing = await session.scalar(existing_stmt)
        if existing:
            raise AccountAlreadyExists(f"Account with {normalized_email} already exists.")

        hashed_password = hash_password(password)
        fallback_name = normalized_email.split("@")[0] if "@" in normalized_email else normalized_email
        display_name = fallback_name or normalized_email

        default_role = await self._get_default_role(session)
        account = models.Account(
            email=normalized_email,
            full_name=display_name,
            hashed_password=hashed_password,
            status=models.AccountStatus.PENDING,
            newsletter_opt_in=newsletter_opt_in,
            use_case=None,
        )
        account.roles.append(default_role)
        session.add(account)

        email_channel = models.CommunicationChannel(
            account=account,
            channel_type=models.ChannelType.EMAIL,
            value=normalized_email,
            status=models.ChannelStatus.PENDING,
            is_primary=True,
        )
        session.add(email_channel)

        await session.flush()

        consents = [
            models.AccountConsent(
                account=account,
                consent_type=models.ConsentType.TERMS,
                version=terms_version,
                granted=True,
                source="signup",
                ip_address=context.ip_address,
                user_agent=context.user_agent,
            ),
            models.AccountConsent(
                account=account,
                consent_type=models.ConsentType.PRIVACY,
                version=terms_version,
                granted=True,
                source="signup",
                ip_address=context.ip_address,
                user_agent=context.user_agent,
            ),
        ]
        if newsletter_opt_in:
            consents.append(
                models.AccountConsent(
                    account=account,
                    consent_type=models.ConsentType.MARKETING,
                    version=terms_version,
                    granted=True,
                    source="signup",
                    ip_address=context.ip_address,
                    user_agent=context.user_agent,
                )
            )
        session.add_all(consents)

        confirmation_token, confirmation_expires = await self._issue_email_token(
            session=session,
            account=account,
            email=normalized_email,
        )

        await session.commit()
        await self._reload_account(session, account)
        return SignupResult(
            account=account,
            confirmation_token=confirmation_token,
            confirmation_expires_at=confirmation_expires,
        )

    async def confirm_email(
        self,
        session: AsyncSession,
        *,
        token: str,
        context: RequestContext,
    ) -> SessionResult:
        token_row = await self._fetch_email_token(session, token)
        account_stmt = (
            select(models.Account)
            .where(models.Account.id == token_row.account_id)
            .options(
                selectinload(models.Account.roles),
                selectinload(models.Account.organization),
                selectinload(models.Account.limits),
            )
        )
        account = await session.scalar(account_stmt)
        if not account:
            raise AccountNotFound("Account not found for token.")

        if not account.hashed_password:
            raise AccountNotFound("Account is missing credentials.")

        account.status = models.AccountStatus.ACTIVE
        account.email_verified_at = utcnow()
        account.last_login_at = utcnow()

        await session.execute(
            update(models.CommunicationChannel)
            .where(
                models.CommunicationChannel.account_id == account.id,
                models.CommunicationChannel.channel_type == models.ChannelType.EMAIL,
            )
            .values(
                status=models.ChannelStatus.VERIFIED,
                verified_at=utcnow(),
            )
        )

        token_row.consumed_at = utcnow()
        session.add(token_row)
        await session.flush()

        session_result = await self._issue_session(session, account, context)
        await session.commit()
        return session_result

    async def resend_confirmation_email(
        self,
        session: AsyncSession,
        *,
        email: str,
        min_interval_seconds: int = 60,
        hourly_limit: int = 5,
    ) -> tuple[models.Account | None, str | None, datetime | None]:
        normalized_email = email.strip().lower()
        try:
            account = await self._fetch_account_by_email(session, normalized_email)
        except AccountNotFound:
            return None, None, None

        if account.email_verified_at:
            return account, None, None

        now = utcnow()
        cooldown_cutoff = now - timedelta(seconds=min_interval_seconds)
        hourly_cutoff = now - timedelta(hours=1)

        hourly_count_stmt = (
            select(func.count(models.EmailVerificationToken.id))
            .where(
                models.EmailVerificationToken.account_id == account.id,
                models.EmailVerificationToken.created_at >= hourly_cutoff,
            )
        )
        recent_count = await session.scalar(hourly_count_stmt) or 0
        if recent_count >= hourly_limit:
            raise ConfirmationResendRateLimited("Hourly resend limit reached.")

        latest_stmt = (
            select(models.EmailVerificationToken.created_at)
            .where(models.EmailVerificationToken.account_id == account.id)
            .order_by(models.EmailVerificationToken.created_at.desc())
            .limit(1)
        )
        latest_sent = await session.scalar(latest_stmt)
        if recent_count >= 2 and latest_sent and _ensure_aware(latest_sent) >= cooldown_cutoff:
            raise ConfirmationResendRateLimited("Please wait before requesting another email.")

        token, expires = await self._issue_email_token(
            session=session,
            account=account,
            email=normalized_email,
        )
        await session.commit()
        await self._reload_account(session, account)
        return account, token, expires

    async def login(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        context: RequestContext,
    ) -> SessionResult:
        account = await self._fetch_account_by_email(session, email)
        if account.status != models.AccountStatus.ACTIVE:
            raise AccountInactive("Account is not activated yet.")
        if not verify_password(password, account.hashed_password):
            raise InvalidCredentials("Invalid credentials.")

        account.last_login_at = utcnow()
        session.add(account)
        await session.flush()

        result = await self._issue_session(session, account, context)
        await session.commit()
        return result

    async def refresh_session(
        self,
        session: AsyncSession,
        *,
        refresh_token: str,
        context: RequestContext,
    ) -> SessionResult:
        session_row = await self._fetch_session_by_token(session, refresh_token)
        account = session_row.account
        await self._reload_account(session, account)

        new_refresh_token = generate_token(48)
        session_row.refresh_token_hash = hash_token(new_refresh_token)
        session_row.issued_at = utcnow()
        session_row.expires_at = build_expiry(self.settings.auth.refresh_token_ttl_seconds)
        session_row.user_agent = context.user_agent
        session_row.ip_address = context.ip_address
        session_row.extra = {"rotated": True}

        access_token, access_expires = create_access_token(
            subject=str(account.id),
            roles=self._extract_roles(account),
            settings=self.settings.auth,
        )

        await session.commit()
        return SessionResult(
            account=account,
            access_token=access_token,
            access_expires_at=access_expires,
            refresh_token=new_refresh_token,
            refresh_expires_at=session_row.expires_at,
            session_id=session_row.id,
        )

    async def get_account_by_refresh_token(
        self,
        session: AsyncSession,
        *,
        refresh_token: str,
    ) -> models.Account:
        session_row = await self._fetch_session_by_token(session, refresh_token)
        account = session_row.account
        await self._reload_account(session, account)
        return account

    async def logout(self, session: AsyncSession, *, refresh_token: str) -> None:
        session_row = await self._fetch_session_by_token(session, refresh_token)
        session_row.revoked_at = utcnow()
        session.add(session_row)
        await session.commit()

    async def update_profile(
        self,
        session: AsyncSession,
        *,
        account: models.Account,
        email: str | None = None,
        full_name: str | None = None,
        job_title: str | None = None,
        organization_name: str | None = None,
        organization_size: str | None = None,
        use_case: str | None = None,
        no_company: bool = False,
    ) -> models.Account:
        account_record = await self.get_account_profile(session, account_id=account.id)

        if email is not None:
            normalized_email = email.strip().lower()
            if normalized_email and normalized_email != account_record.email:
                try:
                    existing = await self._fetch_account_by_email(session, normalized_email)
                except AccountNotFound:
                    existing = None
                if existing and existing.id != account_record.id:
                    raise AccountAlreadyExists(f"Account with {normalized_email} already exists.")
                account_record.email = normalized_email
                account_record.email_verified_at = None
                channel = await session.scalar(
                    select(models.CommunicationChannel).where(
                        models.CommunicationChannel.account_id == account_record.id,
                        models.CommunicationChannel.channel_type == models.ChannelType.EMAIL,
                        func.lower(models.CommunicationChannel.value) == normalized_email,
                    )
                )
                if channel:
                    channel.value = normalized_email
                    channel.status = models.ChannelStatus.PENDING
                    channel.verified_at = None
                    channel.is_primary = True
                else:
                    channel = models.CommunicationChannel(
                        account=account_record,
                        channel_type=models.ChannelType.EMAIL,
                        value=normalized_email,
                        status=models.ChannelStatus.PENDING,
                        is_primary=True,
                    )
                    session.add(channel)
                    await session.flush()
                await session.execute(
                    update(models.CommunicationChannel)
                    .where(
                        models.CommunicationChannel.account_id == account_record.id,
                        models.CommunicationChannel.channel_type == models.ChannelType.EMAIL,
                        models.CommunicationChannel.id != channel.id,
                    )
                    .values(is_primary=False)
                )

        if full_name is not None:
            normalized_name = full_name.strip()
            if normalized_name:
                account_record.full_name = normalized_name

        if job_title is not None:
            normalized_role = job_title.strip()
            account_record.job_title = normalized_role or None

        if use_case is not None:
            normalized_use_case = use_case.strip()
            account_record.use_case = normalized_use_case or None

        normalized_size = None
        if organization_size is not None:
            normalized_size = organization_size.strip() or None

        if no_company:
            account_record.organization = None
        elif organization_name:
            normalized_org = organization_name.strip()
            if normalized_org:
                if account_record.organization:
                    account_record.organization.name = normalized_org
                    account_record.organization.size_label = normalized_size
                    if account_record.use_case:
                        account_record.organization.primary_use_case = account_record.use_case
                else:
                    organization = models.Organization(
                        name=normalized_org,
                        size_label=normalized_size,
                        primary_use_case=account_record.use_case,
                    )
                    account_record.organization = organization
                    session.add(organization)
        elif normalized_size is not None and account_record.organization:
            account_record.organization.size_label = normalized_size

        await session.flush()
        await session.commit()
        await self._reload_account(session, account_record)
        return account_record

    async def request_password_reset(
        self,
        session: AsyncSession,
        *,
        email: str,
        context: RequestContext,
    ) -> tuple[models.Account | None, str | None, datetime | None]:
        try:
            account = await self._fetch_account_by_email(session, email)
        except AccountNotFound:
            # Deliberately hide whether the email exists.
            return None, None, None

        token_value, expires = await self._issue_reset_token(session, account, context)
        await session.commit()
        return account, token_value, expires

    async def reset_password(
        self,
        session: AsyncSession,
        *,
        token: str,
        new_password: str,
        context: RequestContext,
    ) -> SessionResult:
        reset_row = await self._fetch_reset_token(session, token)
        account_stmt = (
            select(models.Account)
            .where(models.Account.id == reset_row.account_id)
            .options(
                selectinload(models.Account.roles),
                selectinload(models.Account.organization),
                selectinload(models.Account.limits),
            )
        )
        account = await session.scalar(account_stmt)
        if not account:
            raise AccountNotFound("Account not found for reset token.")

        account.hashed_password = hash_password(new_password)
        account.status = models.AccountStatus.ACTIVE
        account.last_login_at = utcnow()

        reset_row.consumed_at = utcnow()
        session.add(account)
        session.add(reset_row)

        await session.execute(
            update(models.AuthSession)
            .where(
                models.AuthSession.account_id == account.id,
                models.AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )

        result = await self._issue_session(session, account, context)
        await session.commit()
        return result

    async def get_account_profile(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
    ) -> models.Account:
        stmt = (
            select(models.Account)
            .where(models.Account.id == account_id)
            .options(
                selectinload(models.Account.roles),
                selectinload(models.Account.organization),
                selectinload(models.Account.limits),
                selectinload(models.Account.billing_customer),
                selectinload(models.Account.billing_subscriptions),
            )
        )
        account = await session.scalar(stmt)
        if not account:
            raise AccountNotFound("Account not found.")
        return account

    async def list_accounts(
        self,
        session: AsyncSession,
        *,
        limit: int = 50,
    ) -> list[models.Account]:
        stmt = (
            select(models.Account)
            .options(
                selectinload(models.Account.roles),
                selectinload(models.Account.organization),
                selectinload(models.Account.limits),
                selectinload(models.Account.billing_customer),
                selectinload(models.Account.billing_subscriptions),
            )
            .order_by(models.Account.created_at.desc())
            .limit(limit)
        )
        result = await session.scalars(stmt)
        return list(result)

    async def update_account_status(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        status_value: str,
    ) -> models.Account:
        account = await self.get_account_profile(session, account_id=account_id)
        try:
            account.status = models.AccountStatus(status_value)
        except ValueError as exc:
            raise ValueError(f"Unknown status '{status_value}'.") from exc
        session.add(account)
        await session.commit()
        await self._reload_account(session, account)
        return account

    async def set_account_limits(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        daily_limit: int | None,
        monthly_limit: int | None,
        notes: str | None,
        granted_by: str | None,
    ) -> models.Account:
        account = await self.get_account_profile(session, account_id=account_id)
        if account.limits is None:
            account.limits = models.AccountLimit(
                account_id=account.id,
                daily_call_limit=daily_limit,
                monthly_call_limit=monthly_limit,
                notes=notes,
                granted_by=granted_by,
            )
        else:
            account.limits.daily_call_limit = daily_limit
            account.limits.monthly_call_limit = monthly_limit
            account.limits.notes = notes
            account.limits.granted_by = granted_by
        session.add(account)
        await session.commit()
        await self._reload_account(session, account)
        return account

    async def set_account_roles(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        roles: Sequence[str],
    ) -> models.Account:
        account = await self.get_account_profile(session, account_id=account_id)
        normalized_roles: list[str] = []
        for role in roles:
            candidate = str(role or "").strip().lower()
            if candidate:
                normalized_roles.append(candidate)

        if not normalized_roles:
            account.roles = [await self._get_default_role(session)]
        else:
            stmt = select(models.Role).where(models.Role.slug.in_(normalized_roles)).order_by(models.Role.priority.asc())
            fetched_roles = (await session.scalars(stmt)).all()
            fetched_slugs = {role.slug for role in fetched_roles}
            missing = sorted(set(normalized_roles) - fetched_slugs)
            if missing:
                raise ValueError(f"Unknown roles: {', '.join(missing)}")
            account.roles = list(fetched_roles)

        session.add(account)
        await session.commit()
        await self._reload_account(session, account)
        return account

    async def delete_account(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
    ) -> None:
        account = await self.get_account_profile(session, account_id=account_id)
        await session.delete(account)
        await session.commit()

    async def list_roles(self, session: AsyncSession) -> list[models.Role]:
        stmt = select(models.Role).order_by(models.Role.priority.asc())
        result = await session.scalars(stmt)
        return list(result)

    async def _get_default_role(self, session: AsyncSession) -> models.Role:
        stmt = (
            select(models.Role)
            .where(models.Role.is_default.is_(True))
            .order_by(models.Role.priority.asc())
        )
        role = await session.scalar(stmt)
        if not role:
            raise RuntimeError("Default role is not configured.")
        return role

    async def _issue_email_token(
        self,
        session: AsyncSession,
        *,
        account: models.Account,
        email: str,
    ) -> tuple[str, datetime]:
        token_value = generate_token(32)
        expires = build_expiry(self.settings.auth.email_token_ttl_seconds)
        token_row = models.EmailVerificationToken(
            account_id=account.id,
            token_hash=hash_token(token_value),
            sent_to=email,
            expires_at=expires,
        )
        session.add(token_row)
        await session.flush()
        return token_value, expires

    async def _issue_session(
        self,
        session: AsyncSession,
        account: models.Account,
        context: RequestContext,
    ) -> SessionResult:
        refresh_token = generate_token(48)
        refresh_hash = hash_token(refresh_token)
        refresh_expires = build_expiry(self.settings.auth.refresh_token_ttl_seconds)
        session_row = models.AuthSession(
            account_id=account.id,
            refresh_token_hash=refresh_hash,
            issued_at=utcnow(),
            expires_at=refresh_expires,
            user_agent=context.user_agent,
            ip_address=context.ip_address,
        )
        session.add(session_row)
        await session.flush()
        await self._reload_account(session, account)

        access_token, access_expires = create_access_token(
            subject=str(account.id),
            roles=self._extract_roles(account),
            settings=self.settings.auth,
        )

        return SessionResult(
            account=account,
            access_token=access_token,
            access_expires_at=access_expires,
            refresh_token=refresh_token,
            refresh_expires_at=refresh_expires,
            session_id=session_row.id,
        )

    async def _fetch_account_by_email(self, session: AsyncSession, email: str) -> models.Account:
        normalized = email.strip().lower()
        stmt = (
            select(models.Account)
            .where(func.lower(models.Account.email) == normalized)
            .options(
                selectinload(models.Account.roles),
                selectinload(models.Account.organization),
                selectinload(models.Account.limits),
            )
        )
        account = await session.scalar(stmt)
        if not account:
            raise AccountNotFound("Account not found.")
        if account.status == models.AccountStatus.LOCKED:
            raise InvalidCredentials("Account locked.")
        return account

    async def _fetch_email_token(
        self,
        session: AsyncSession,
        token: str,
    ) -> models.EmailVerificationToken:
        hashed = hash_token(token)
        stmt = (
            select(models.EmailVerificationToken)
            .where(
                models.EmailVerificationToken.token_hash == hashed,
                models.EmailVerificationToken.consumed_at.is_(None),
                models.EmailVerificationToken.expires_at > utcnow(),
            )
            .order_by(models.EmailVerificationToken.created_at.desc())
        )
        token_row = await session.scalar(stmt)
        if not token_row:
            raise TokenInvalid("Confirmation token is invalid or expired.")
        return token_row

    async def _fetch_session_by_token(
        self,
        session: AsyncSession,
        refresh_token: str,
    ) -> models.AuthSession:
        hashed = hash_token(refresh_token)
        stmt = (
            select(models.AuthSession)
            .where(
                models.AuthSession.refresh_token_hash == hashed,
                models.AuthSession.revoked_at.is_(None),
                models.AuthSession.expires_at > utcnow(),
            )
            .options(
                selectinload(models.AuthSession.account).selectinload(models.Account.roles),
                selectinload(models.AuthSession.account).selectinload(models.Account.organization),
                selectinload(models.AuthSession.account).selectinload(models.Account.limits),
            )
        )
        session_row = await session.scalar(stmt)
        if not session_row:
            raise SessionInvalid("Refresh token is invalid.")
        if session_row.account.status == models.AccountStatus.LOCKED:
            raise SessionInvalid("Account is locked.")
        return session_row

    async def _issue_reset_token(
        self,
        session: AsyncSession,
        account: models.Account,
        context: RequestContext,
    ) -> tuple[str, datetime]:
        raw_token = generate_token(48)
        expires = build_expiry(self.settings.auth.password_reset_ttl_seconds)
        reset_row = models.PasswordResetToken(
            account_id=account.id,
            token_hash=hash_token(raw_token),
            expires_at=expires,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
        )
        session.add(reset_row)
        await session.flush()
        return raw_token, expires

    async def _fetch_reset_token(
        self,
        session: AsyncSession,
        token: str,
    ) -> models.PasswordResetToken:
        hashed = hash_token(token)
        stmt = (
            select(models.PasswordResetToken)
            .where(
                models.PasswordResetToken.token_hash == hashed,
                models.PasswordResetToken.consumed_at.is_(None),
                models.PasswordResetToken.expires_at > utcnow(),
            )
        )
        reset_row = await session.scalar(stmt)
        if not reset_row:
            raise TokenExpired("Reset token is invalid or expired.")
        return reset_row

    async def _reload_account(self, session: AsyncSession, account: models.Account) -> None:
        await session.refresh(
            account,
            attribute_names=[
                "roles",
                "organization",
                "limits",
                "email_verified_at",
                "last_login_at",
                "billing_customer",
                "billing_subscriptions",
            ],
        )

    def _extract_roles(self, account: models.Account) -> list[str]:
        return sorted({role.slug for role in account.roles})

    def build_profile(self, account: models.Account) -> dict[str, object]:
        organization = None
        if account.organization:
            organization = {
                "id": str(account.organization.id),
                "name": account.organization.name,
                "size_label": account.organization.size_label,
                "primary_use_case": account.organization.primary_use_case,
                "country": account.organization.country,
            }

        limits = None
        if account.limits:
            limits = {
                "daily_call_limit": account.limits.daily_call_limit,
                "monthly_call_limit": account.limits.monthly_call_limit,
                "notes": account.limits.notes,
            }

        subscription = None
        primary_subscription = self._select_primary_subscription(account)
        if primary_subscription:
            status_value = primary_subscription.status
            if (
                status_value == models.BillingSubscriptionStatus.TRIALING
                and primary_subscription.trial_ends_at
                and primary_subscription.trial_ends_at <= utcnow()
            ):
                status_value = models.BillingSubscriptionStatus.ACTIVE
            subscription = {
                "plan_code": primary_subscription.plan_code,
                "status": status_value.value,
                "currency": primary_subscription.currency,
                "unit_amount_cents": primary_subscription.unit_amount_cents,
                "interval": primary_subscription.interval,
                "price_id": primary_subscription.price_id,
                "current_period_start": primary_subscription.current_period_start.isoformat()
                if primary_subscription.current_period_start
                else None,
                "trial_ends_at": primary_subscription.trial_ends_at.isoformat()
                if primary_subscription.trial_ends_at
                else None,
                "current_period_end": primary_subscription.current_period_end.isoformat()
                if primary_subscription.current_period_end
                else None,
                "cancel_at_period_end": primary_subscription.cancel_at_period_end,
                "latest_invoice_id": primary_subscription.latest_invoice_id,
                "hosted_checkout_url": primary_subscription.hosted_checkout_url,
            }

        return {
            "id": str(account.id),
            "email": account.email,
            "full_name": account.full_name,
            "job_title": account.job_title,
            "status": account.status.value,
            "newsletter_opt_in": account.newsletter_opt_in,
            "roles": self._extract_roles(account),
            "use_case": account.use_case,
            "organization": organization,
            "limits": limits,
            "email_verified_at": account.email_verified_at.isoformat() if account.email_verified_at else None,
            "last_login_at": account.last_login_at.isoformat() if account.last_login_at else None,
            "subscription": subscription,
        }

    def _select_primary_subscription(
        self,
        account: models.Account,
    ) -> models.BillingSubscription | None:
        subscriptions = account.billing_subscriptions or []
        if not subscriptions:
            return None

        now = utcnow()
        active_subscriptions = []
        for subscription in subscriptions:
            if subscription.status not in ACTIVE_SUBSCRIPTION_STATUSES:
                continue
            if subscription.current_period_end and _ensure_aware(subscription.current_period_end) <= now:
                continue
            active_subscriptions.append(subscription)

        if not active_subscriptions:
            return None

        sorted_candidates = sorted(
            active_subscriptions,
            key=lambda item: (
                0 if item.status in ACTIVE_SUBSCRIPTION_STATUSES else 1,
                -self._as_timestamp(item.current_period_end),
                -self._as_timestamp(item.updated_at or item.created_at),
            ),
        )
        return sorted_candidates[0]

    def _as_timestamp(self, value: datetime | None) -> float:
        if value is None:
            return float("-inf")
        return _ensure_aware(value).timestamp()

    async def _has_future_payment(
        self,
        session: AsyncSession,
        account_id: uuid.UUID,
        plan_code: str,
        cutoff: datetime,
    ) -> bool:
        stmt = (
            select(func.count())
            .select_from(models.BillingCryptoPayment)
            .where(
                models.BillingCryptoPayment.account_id == account_id,
                models.BillingCryptoPayment.plan_code == plan_code,
                models.BillingCryptoPayment.status.in_(
                    [
                        models.BillingCryptoPaymentStatus.PENDING,
                        models.BillingCryptoPaymentStatus.PROCESSING,
                        models.BillingCryptoPaymentStatus.CONFIRMED,
                    ]
                ),
                models.BillingCryptoPayment.period_end_at.is_not(None),
                models.BillingCryptoPayment.period_end_at >= cutoff,
            )
        )
        return bool(await session.scalar(stmt))

    async def expire_lapsed_subscriptions(
        self,
        session: AsyncSession,
        account: models.Account,
    ) -> bool:
        subscriptions = account.billing_subscriptions or []
        if not subscriptions:
            return False

        now = utcnow()
        updated = False
        for subscription in subscriptions:
            if subscription.status not in ACTIVE_SUBSCRIPTION_STATUSES:
                continue
            if not subscription.current_period_end:
                continue
            period_end = _ensure_aware(subscription.current_period_end)
            if period_end > now:
                continue
            has_future_payment = await self._has_future_payment(
                session, account.id, subscription.plan_code, now
            )
            if has_future_payment:
                continue
            subscription.status = models.BillingSubscriptionStatus.CANCELED
            subscription.cancel_at_period_end = True
            subscription.synced_at = now
            session.add(subscription)
            updated = True

        if updated:
            await session.commit()
            await self._reload_account(session, account)
        return updated

    async def enforce_single_active_subscription(
        self,
        session: AsyncSession,
        account: models.Account,
    ) -> models.BillingSubscription | None:
        await self.expire_lapsed_subscriptions(session, account)
        primary = self._select_primary_subscription(account)
        now = utcnow()
        active_subscriptions = [
            subscription
            for subscription in account.billing_subscriptions or []
            if subscription.status in ACTIVE_SUBSCRIPTION_STATUSES
            and (not subscription.current_period_end or _ensure_aware(subscription.current_period_end) > now)
        ]
        if not primary or len(active_subscriptions) <= 1:
            return primary

        now = utcnow()
        for subscription in active_subscriptions:
            if subscription.id == primary.id:
                continue
            subscription.status = models.BillingSubscriptionStatus.CANCELED
            subscription.cancel_at_period_end = True
            subscription.synced_at = now
            session.add(subscription)
        await session.commit()
        await self._reload_account(session, account)
        return self._select_primary_subscription(account)

    async def oauth_login_or_signup(
        self,
        session: AsyncSession,
        *,
        provider: models.OAuthProvider,
        provider_user_id: str,
        email: str,
        full_name: str,
        access_token: str,
        refresh_token: str | None,
        expires_at: "datetime | None",
        context: RequestContext,
    ) -> SessionResult:
        normalized_email = email.strip().lower()

        # 1. Look up existing OAuth connection
        conn_stmt = (
            select(models.OAuthConnection)
            .where(
                models.OAuthConnection.provider == provider,
                models.OAuthConnection.provider_user_id == provider_user_id,
            )
            .options(
                selectinload(models.OAuthConnection.account).selectinload(models.Account.roles),
                selectinload(models.OAuthConnection.account).selectinload(models.Account.organization),
                selectinload(models.OAuthConnection.account).selectinload(models.Account.limits),
            )
        )
        conn = await session.scalar(conn_stmt)

        if conn:
            account = conn.account
            if account.status == models.AccountStatus.LOCKED:
                raise InvalidCredentials("Account is locked.")
            conn.access_token = access_token
            conn.refresh_token = refresh_token
            conn.expires_at = expires_at
            account.last_login_at = utcnow()
            session.add(conn)
            session.add(account)
            await session.flush()
            result = await self._issue_session(session, account, context)
            await session.commit()
            return result

        # 2. Look up account by email
        try:
            account = await self._fetch_account_by_email(session, normalized_email)
        except AccountNotFound:
            account = None

        if account is not None:
            if account.status == models.AccountStatus.LOCKED:
                raise InvalidCredentials("Account is locked.")
            # Link OAuth to existing account (covers password-only and OAuth-migrated accounts)
            if account.status == models.AccountStatus.PENDING:
                account.status = models.AccountStatus.ACTIVE
            if not account.email_verified_at:
                account.email_verified_at = utcnow()
                await session.execute(
                    update(models.CommunicationChannel)
                    .where(
                        models.CommunicationChannel.account_id == account.id,
                        models.CommunicationChannel.channel_type == models.ChannelType.EMAIL,
                    )
                    .values(status=models.ChannelStatus.VERIFIED, verified_at=utcnow())
                )
            account.last_login_at = utcnow()
            # Guard against duplicate (account_id, provider) – uq_auth_oauth_connections_account_id
            existing_conn_stmt = select(models.OAuthConnection).where(
                models.OAuthConnection.account_id == account.id,
                models.OAuthConnection.provider == provider,
            )
            existing_conn = await session.scalar(existing_conn_stmt)
            if existing_conn:
                existing_conn.provider_user_id = provider_user_id
                existing_conn.access_token = access_token
                existing_conn.refresh_token = refresh_token
                existing_conn.expires_at = expires_at
                session.add(existing_conn)
            else:
                new_conn = models.OAuthConnection(
                    account_id=account.id,
                    provider=provider,
                    provider_user_id=provider_user_id,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=expires_at,
                )
                session.add(new_conn)
            session.add(account)
            await session.flush()
            result = await self._issue_session(session, account, context)
            await session.commit()
            return result

        # 3. Create new account
        default_role = await self._get_default_role(session)
        display_name = full_name.strip() or normalized_email.split("@")[0]
        account = models.Account(
            email=normalized_email,
            full_name=display_name,
            hashed_password=None,
            status=models.AccountStatus.ACTIVE,
            email_verified_at=utcnow(),
            last_login_at=utcnow(),
        )
        account.roles.append(default_role)
        session.add(account)

        email_channel = models.CommunicationChannel(
            account=account,
            channel_type=models.ChannelType.EMAIL,
            value=normalized_email,
            status=models.ChannelStatus.VERIFIED,
            is_primary=True,
            verified_at=utcnow(),
        )
        session.add(email_channel)
        await session.flush()

        new_conn = models.OAuthConnection(
            account_id=account.id,
            provider=provider,
            provider_user_id=provider_user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        session.add(new_conn)
        await session.flush()

        result = await self._issue_session(session, account, context)
        await session.commit()
        return result
