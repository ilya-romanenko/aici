from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .db import Base

JSON_FIELD = JSON().with_variant(JSONB(astext_type=Text()), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AccountStatus(str, enum.Enum):
    PENDING = "pending_activation"
    ACTIVE = "active"
    LOCKED = "locked"
    INVITED = "invited"


class RoleSlug(str, enum.Enum):
    ADMIN = "admin"
    MODERATOR = "moderator"
    MEMBER = "member"


class ConsentType(str, enum.Enum):
    TERMS = "terms_of_service"
    PRIVACY = "privacy_policy"
    MARKETING = "marketing"
    PRODUCT = "product_updates"


class ChannelType(str, enum.Enum):
    EMAIL = "email"
    SLACK = "slack"
    TELEGRAM = "telegram"


class ChannelStatus(str, enum.Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    BOUNCED = "bounced"
    UNSUBSCRIBED = "unsubscribed"


class ApiKeyStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


class ApiKeyRole(str, enum.Enum):
    READER = "reader"
    STANDARD = "standard"
    AUTOMATION = "automation"
    ADMIN = "admin"


class ApiKeyAuditEventType(str, enum.Enum):
    ISSUED = "issued"
    ROTATED = "rotated"
    UPDATED = "updated"
    REVOKED = "revoked"
    LIMIT_ALERT = "limit_alert"


class OAuthProvider(str, enum.Enum):
    GOOGLE = "google"
    GITHUB = "github"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )


class Organization(TimestampMixin, Base):
    __tablename__ = "auth_organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    website: Mapped[str | None] = mapped_column(String(255))
    size_label: Mapped[str | None] = mapped_column(String(40))
    primary_use_case: Mapped[str | None] = mapped_column(Text())
    country: Mapped[str | None] = mapped_column(String(80))

    accounts: Mapped[list["Account"]] = relationship(back_populates="organization")


class Role(TimestampMixin, Base):
    __tablename__ = "auth_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    slug: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text())
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(SmallInteger, default=100, nullable=False)

    accounts: Mapped[list["Account"]] = relationship(
        secondary="auth_account_roles",
        back_populates="roles",
    )


class AccountRole(Base):
    __tablename__ = "auth_account_roles"
    __table_args__ = (
        UniqueConstraint("account_id", "role_id", name="uq_account_role"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class Account(TimestampMixin, Base):
    __tablename__ = "auth_accounts"
    __table_args__ = (
        UniqueConstraint("email", name="uq_auth_accounts_email"),
        Index("ix_auth_accounts_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(String(160), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(120))
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[AccountStatus] = mapped_column(
        SqlEnum(AccountStatus, name="auth_account_status"),
        default=AccountStatus.PENDING,
        server_default=AccountStatus.PENDING.name,
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newsletter_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    use_case: Mapped[str | None] = mapped_column(Text())
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    organization: Mapped[Organization | None] = relationship(back_populates="accounts")
    roles: Mapped[list[Role]] = relationship(
        secondary="auth_account_roles",
        back_populates="accounts",
    )
    communication_channels: Mapped[list["CommunicationChannel"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    consents: Mapped[list["AccountConsent"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    limits: Mapped[Optional["AccountLimit"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        uselist=False,
    )
    billing_customer: Mapped[Optional["BillingCustomer"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        uselist=False,
    )
    billing_subscriptions: Mapped[list["BillingSubscription"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    billing_crypto_payments: Mapped[list["BillingCryptoPayment"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    usage_alert_rules: Mapped[list["UsageAlertRule"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    oauth_connections: Mapped[list["OAuthConnection"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )


class AccountConsent(TimestampMixin, Base):
    __tablename__ = "auth_account_consents"
    __table_args__ = (Index("ix_auth_consents_account_type", "account_id", "consent_type"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_type: Mapped[ConsentType] = mapped_column(
        SqlEnum(ConsentType, name="auth_consent_type"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str | None] = mapped_column(String(160))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text())
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON_FIELD)

    account: Mapped[Account] = relationship(back_populates="consents")


class CommunicationChannel(TimestampMixin, Base):
    __tablename__ = "auth_communication_channels"
    __table_args__ = (
        UniqueConstraint("account_id", "channel_type", "value", name="uq_auth_channels_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_type: Mapped[ChannelType] = mapped_column(
        SqlEnum(ChannelType, name="auth_channel_type"),
        nullable=False,
    )
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ChannelStatus] = mapped_column(
        SqlEnum(ChannelStatus, name="auth_channel_status"),
        default=ChannelStatus.PENDING,
        server_default=ChannelStatus.PENDING.name,
        nullable=False,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opt_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON_FIELD)

    account: Mapped[Account] = relationship(back_populates="communication_channels")


class EmailVerificationToken(TimestampMixin, Base):
    __tablename__ = "auth_email_tokens"
    __table_args__ = (
        Index("ix_auth_email_tokens_account", "account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(64), nullable=False)
    sent_to: Mapped[str] = mapped_column(String(160), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_channel: Mapped[ChannelType] = mapped_column(
        SqlEnum(ChannelType, name="auth_email_token_channel"),
        default=ChannelType.EMAIL,
        nullable=False,
    )


class PasswordResetToken(TimestampMixin, Base):
    __tablename__ = "auth_password_reset_tokens"
    __table_args__ = (
        Index("ix_auth_reset_tokens_account", "account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text())


class AuthSession(TimestampMixin, Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_account", "account_id"),
        Index("ix_auth_sessions_refresh_hash", "refresh_token_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    refresh_token_hash: Mapped[bytes] = mapped_column(LargeBinary(64), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text())
    ip_address: Mapped[str | None] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None]
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON_FIELD)

    account: Mapped[Account] = relationship()


class AccountLimit(TimestampMixin, Base):
    __tablename__ = "auth_account_limits"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    daily_call_limit: Mapped[int | None] = mapped_column(Integer)
    monthly_call_limit: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text())
    granted_by: Mapped[str | None] = mapped_column(String(120))

    account: Mapped["Account"] = relationship(back_populates="limits")


class BillingProvider(str, enum.Enum):
    STRIPE = "stripe"
    MANUAL = "manual"
    CRYPTO = "crypto"


class BillingCryptoChain(str, enum.Enum):
    TRC20 = "trc20"
    BSC = "bsc"
    POLYGON = "polygon"


class BillingCryptoPaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    EXPIRED = "expired"


class BillingSubscriptionStatus(str, enum.Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    UNPAID = "unpaid"


class BillingCustomer(TimestampMixin, Base):
    __tablename__ = "billing_customers"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_billing_customer_account"),
        UniqueConstraint("provider_customer_id", name="uq_billing_customer_provider_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[BillingProvider] = mapped_column(
        SqlEnum(BillingProvider, name="billing_provider"),
        nullable=False,
    )
    provider_customer_id: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(160))
    currency: Mapped[str] = mapped_column(String(12), nullable=False, default="usd")
    delinquent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_payment_method: Mapped[str | None] = mapped_column(String(160))
    stripe_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSON_FIELD,
    )

    account: Mapped["Account"] = relationship(back_populates="billing_customer")
    subscriptions: Mapped[list["BillingSubscription"]] = relationship(back_populates="customer")


class BillingSubscription(TimestampMixin, Base):
    __tablename__ = "billing_subscriptions"
    __table_args__ = (
        UniqueConstraint("provider_subscription_id", name="uq_billing_subscription_provider_id"),
        Index("ix_billing_sub_account_status", "account_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("billing_customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_subscription_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[BillingSubscriptionStatus] = mapped_column(
        SqlEnum(BillingSubscriptionStatus, name="billing_subscription_status"),
        nullable=False,
        default=BillingSubscriptionStatus.TRIALING,
    )
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False)
    price_id: Mapped[str | None] = mapped_column(String(160))
    currency: Mapped[str] = mapped_column(String(12), nullable=False, default="usd")
    unit_amount_cents: Mapped[int | None] = mapped_column(Integer)
    interval: Mapped[str] = mapped_column(String(32), nullable=False, default="month")
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latest_invoice_id: Mapped[str | None] = mapped_column(String(160))
    checkout_session_id: Mapped[str | None] = mapped_column(String(160))
    hosted_checkout_url: Mapped[str | None] = mapped_column(String(500))
    customer_portal_url: Mapped[str | None] = mapped_column(String(500))
    last_event_id: Mapped[str | None] = mapped_column(String(160))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSON_FIELD)

    account: Mapped["Account"] = relationship(back_populates="billing_subscriptions")
    customer: Mapped[Optional["BillingCustomer"]] = relationship(back_populates="subscriptions")


class BillingCryptoPayment(TimestampMixin, Base):
    __tablename__ = "billing_crypto_payments"
    __table_args__ = (
        UniqueConstraint("invoice_id", name="uq_crypto_payment_invoice_id"),
        Index("ix_crypto_payment_account_status", "account_id", "status"),
        Index("ix_crypto_payment_tx_hash", "tx_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_id: Mapped[str] = mapped_column(String(160), nullable=False)
    tx_hash: Mapped[str | None] = mapped_column(String(200))
    chain: Mapped[BillingCryptoChain] = mapped_column(
        SqlEnum(BillingCryptoChain, name="billing_crypto_chain"),
        nullable=False,
    )
    expected_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=20, scale=8),
        nullable=False,
    )
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=20, scale=8),
        nullable=False,
        default=Decimal("0"),
    )
    confirmations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[BillingCryptoPaymentStatus] = mapped_column(
        SqlEnum(BillingCryptoPaymentStatus, name="billing_crypto_payment_status"),
        nullable=False,
        default=BillingCryptoPaymentStatus.PENDING,
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_FIELD)
    period_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped["Account"] = relationship(back_populates="billing_crypto_payments")


class BillingEvent(TimestampMixin, Base):
    __tablename__ = "billing_events"
    __table_args__ = (
        UniqueConstraint("provider_event_id", name="uq_billing_event_provider_id"),
        Index("ix_billing_event_account", "account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[BillingProvider] = mapped_column(
        SqlEnum(BillingProvider, name="billing_event_provider"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str | None] = mapped_column(String(64))
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="SET NULL"),
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("billing_subscriptions.id", ondelete="SET NULL"),
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_FIELD)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_api_keys_hash"),
        Index("ix_api_keys_account_status", "account_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    application_name: Mapped[str | None] = mapped_column(String(160))
    tags: Mapped[list[str] | None] = mapped_column(JSON_FIELD)
    role: Mapped[ApiKeyRole] = mapped_column(
        SqlEnum(ApiKeyRole, name="api_key_role"),
        default=ApiKeyRole.STANDARD,
        nullable=False,
    )
    status: Mapped[ApiKeyStatus] = mapped_column(
        SqlEnum(ApiKeyStatus, name="api_key_status"),
        default=ApiKeyStatus.ACTIVE,
        nullable=False,
    )
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False, default="free")
    token_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    token_suffix: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    daily_quota_override: Mapped[int | None] = mapped_column(Integer)
    monthly_quota_override: Mapped[int | None] = mapped_column(Integer)
    burst_limit_override: Mapped[int | None] = mapped_column(Integer)
    data_latency_override: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text())
    attributes: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON_FIELD)
    created_by: Mapped[str | None] = mapped_column(String(120))
    updated_by: Mapped[str | None] = mapped_column(String(120))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text())

    account: Mapped["Account"] = relationship(back_populates="api_keys")
    daily_usage: Mapped[list["ApiKeyUsageDaily"]] = relationship(
        back_populates="api_key",
        cascade="all, delete-orphan",
    )
    monthly_usage: Mapped[list["ApiKeyUsageMonthly"]] = relationship(
        back_populates="api_key",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list["ApiKeyAuditEvent"]] = relationship(
        back_populates="api_key",
        cascade="all, delete-orphan",
    )


class ApiKeyUsageDaily(TimestampMixin, Base):
    __tablename__ = "api_key_usage_daily"
    __table_args__ = (
        UniqueConstraint("api_key_id", "usage_date", name="uq_api_key_usage_daily"),
        Index("ix_api_key_usage_daily_date", "usage_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    compute_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_notified_threshold: Mapped[int | None] = mapped_column(Integer)

    api_key: Mapped["ApiKey"] = relationship(back_populates="daily_usage")


class ApiKeyUsageMonthly(TimestampMixin, Base):
    __tablename__ = "api_key_usage_monthly"
    __table_args__ = (
        UniqueConstraint("api_key_id", "period_start", name="uq_api_key_usage_monthly"),
        Index("ix_api_key_usage_monthly_period", "period_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    compute_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_notified_threshold: Mapped[int | None] = mapped_column(Integer)

    api_key: Mapped["ApiKey"] = relationship(back_populates="monthly_usage")


class ApiKeyAuditEvent(TimestampMixin, Base):
    __tablename__ = "api_key_audit_events"
    __table_args__ = (
        Index("ix_api_key_audit_key_created_at", "api_key_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[ApiKeyAuditEventType] = mapped_column(
        SqlEnum(ApiKeyAuditEventType, name="api_key_audit_event_type"),
        nullable=False,
    )
    actor: Mapped[str | None] = mapped_column(String(160))
    actor_ip: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON_FIELD)

    api_key: Mapped["ApiKey"] = relationship(back_populates="audit_events")


class UsageAlertRule(TimestampMixin, Base):
    __tablename__ = "usage_alert_rules"
    __table_args__ = (
        Index("ix_usage_alert_rules_account", "account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_type: Mapped[ChannelType] = mapped_column(
        SqlEnum(ChannelType, name="usage_alert_channel"),
        nullable=False,
    )
    destination: Mapped[str] = mapped_column(String(512), nullable=False)
    label: Mapped[str | None] = mapped_column(String(160))
    threshold_percent: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=80)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_triggered_percent: Mapped[int | None] = mapped_column(SmallInteger)

    account: Mapped[Account] = relationship(back_populates="usage_alert_rules")


class ApiUsageEvent(TimestampMixin, Base):
    __tablename__ = "api_usage_events"
    __table_args__ = (
        Index("ix_usage_events_account_created", "account_id", "created_at"),
        Index("ix_usage_events_key_created", "api_key_id", "created_at"),
        Index("ix_usage_events_status", "status_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_code: Mapped[str | None] = mapped_column(String(64))
    route_path: Mapped[str] = mapped_column(String(160), nullable=False)
    route_name: Mapped[str | None] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    request_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text())
    request_id: Mapped[str | None] = mapped_column(String(80))

    api_key: Mapped["ApiKey"] = relationship()


class IndexRunSource(str, enum.Enum):
    USER = "user"
    AUTO = "auto"


class IndexRun(TimestampMixin, Base):
    __tablename__ = "index_runs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_index_runs_run_id"),
        CheckConstraint("length(run_id) BETWEEN 3 AND 80", name="ck_index_runs_run_id_length"),
        Index("ix_index_runs_account_source_created", "account_id", "source", "created_at"),
        Index("ix_index_runs_source_created", "source", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[IndexRunSource] = mapped_column(
        SqlEnum(IndexRunSource, name="index_run_source"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )


class OAuthConnection(TimestampMixin, Base):
    __tablename__ = "auth_oauth_connections"
    __table_args__ = (
        UniqueConstraint("account_id", "provider", name="uq_auth_oauth_connections_account_id"),
        Index("ix_auth_oauth_connections_provider_user", "provider", "provider_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[OAuthProvider] = mapped_column(
        SqlEnum(OAuthProvider, name="auth_oauth_provider"),
        nullable=False,
    )
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    access_token: Mapped[str] = mapped_column(Text(), nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(Text())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped["Account"] = relationship(back_populates="oauth_connections")
