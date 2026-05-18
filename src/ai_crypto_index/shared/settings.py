from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_ENV = "AI_CRYPTO_CONFIG"
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthSettings:
    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str
    access_token_ttl_seconds: int
    refresh_token_ttl_seconds: int
    email_token_ttl_seconds: int
    password_reset_ttl_seconds: int
    session_cookie_name: str
    session_cookie_domain: str | None
    session_cookie_secure: bool
    public_app_url: str
    expose_tokens_in_responses: bool
    echo_sql: bool


@dataclass(frozen=True)
class ApiKeyPlanSettings:
    code: str
    daily_quota: int | None
    monthly_quota: int | None
    burst_per_minute: int
    burst_per_second: int
    data_latency_seconds: int
    max_keys: int | None
    default_role: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class ApiTokenPricingSettings:
    per_call_tokens: int
    pipeline_trigger_tokens: int
    minimum_debit_tokens: int


@dataclass(frozen=True)
class ApiKeySettings:
    encryption_secret: str
    key_prefix: str
    max_keys_per_account: int
    default_plan_code: str
    notification_thresholds: tuple[float, ...]
    token_pricing: ApiTokenPricingSettings
    plans: dict[str, ApiKeyPlanSettings]
    rotation_webhook_urls: tuple[str, ...]
    usage_alert_webhook_urls: tuple[str, ...]


@dataclass(frozen=True)
class ServiceSettings:
    """Materialized configuration shared between API and pipelines."""

    config_path: Path
    runs_root: Path
    auth: AuthSettings
    billing: "BillingSettings"
    api_keys: ApiKeySettings
    google_client_id: str | None
    google_client_secret: str | None


@dataclass(frozen=True)
class BillingPlanSettings:
    code: str
    name: str
    price_id: str | None
    product_id: str | None
    unit_amount_cents: int
    currency: str
    interval: str
    trial_days: int
    self_serve: bool
    features: tuple[str, ...]


@dataclass(frozen=True)
class BillingCryptoNetworkSettings:
    code: str
    currency: str
    chain: str
    confirmations_required: int
    fee_percent: float


@dataclass(frozen=True)
class BillingCryptoSettings:
    provider: str
    api_key: str | None
    api_secret: str | None
    webhook_secret: str | None
    payout_address: str | None
    default_currency: str
    default_network: str
    usd_to_crypto_rate: float
    service_fee_percent: float
    networks: dict[str, BillingCryptoNetworkSettings]


@dataclass(frozen=True)
class BillingSettings:
    provider: str
    currency: str
    default_trial_days: int
    enterprise_invoice_terms_days: int
    checkout_success_url: str
    checkout_cancel_url: str
    portal_return_url: str
    stripe_secret_key: str | None
    stripe_publishable_key: str | None
    stripe_webhook_secret: str | None
    plans: dict[str, BillingPlanSettings]
    crypto: BillingCryptoSettings | None


_DEFAULT_API_KEY_PLANS: dict[str, dict[str, object]] = {
    "free": {
        "code": "free",
        "daily_quota": 500,
        "monthly_quota": 500,
        "burst_per_minute": 60,
        "burst_per_second": 5,
        "data_latency_seconds": 86400,
        "max_keys": 2,
        "default_role": "reader",
        "roles": ("reader",),
    },
    "pro": {
        "code": "pro",
        "daily_quota": 1000,
        "monthly_quota": 10000,
        "burst_per_minute": 600,
        "burst_per_second": 40,
        "data_latency_seconds": 900,
        "max_keys": 5,
        "default_role": "standard",
        "roles": ("standard", "automation"),
    },
    "ultra": {
        "code": "ultra",
        "daily_quota": 10000,
        "monthly_quota": 100000,
        "burst_per_minute": 1200,
        "burst_per_second": 80,
        "data_latency_seconds": 0,
        "max_keys": 10,
        "default_role": "standard",
        "roles": ("standard", "automation"),
    },
    "enterprise": {
        "code": "enterprise",
        "daily_quota": None,
        "monthly_quota": None,
        "burst_per_minute": 2000,
        "burst_per_second": 200,
        "data_latency_seconds": 0,
        "max_keys": 20,
        "default_role": "standard",
        "roles": ("standard", "automation", "admin"),
    },
}


def _load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _resolve_runs_root(config: dict[str, Any], config_path: Path) -> Path:
    configured = config.get("runs", {}).get("root", "runs")
    candidate = Path(configured)

    if candidate.is_absolute():
        resolved = candidate
    else:
        config_dir = config_path.resolve().parent
        base_dir = config_dir.parent if config_dir.name.lower() == "config" else config_dir
        resolved = (base_dir / candidate).resolve()

    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _as_sqlite_url(path: Path) -> str:
    normalized = path.resolve().as_posix()
    if normalized.startswith("/"):
        return f"sqlite+aiosqlite://{normalized}"
    return f"sqlite+aiosqlite:///{normalized}"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_str(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    return value or default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _absolute_url(base: str, path: str | None, fallback: str) -> str:
    if path is None or not path.strip():
        return fallback
    normalized_base = (base or fallback).rstrip("/")
    candidate = path.strip()
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    return f"{normalized_base}{candidate}"


def _resolve_billing_settings(config: dict[str, Any], auth_settings: AuthSettings) -> BillingSettings:
    billing_config = config.get("billing", {})
    provider = str(billing_config.get("provider") or "stripe").lower()
    currency = str(billing_config.get("currency") or "usd").lower()
    default_trial_days = _env_int("AICI_BILLING_TRIAL_DAYS", int(billing_config.get("trial_days", 14)))
    enterprise_terms = _env_int(
        "AICI_BILLING_ENTERPRISE_TERMS_DAYS",
        int(billing_config.get("enterprise_invoice_terms_days", 30)),
    )
    plans_config = billing_config.get("plans") or {}
    plans: dict[str, BillingPlanSettings] = {}
    if isinstance(plans_config, dict):
        for code, plan in plans_config.items():
            if not isinstance(plan, dict):
                continue
            plan_code = str(plan.get("code") or code).lower()
            price_id_env = plan.get("price_id_env")
            price_id = _env_str(price_id_env) if price_id_env else None
            price_id = price_id or plan.get("price_id")
            unit_amount = int(plan.get("unit_amount_cents", 0) or 0)
            plan_currency = str(plan.get("currency") or currency).lower()
            features = plan.get("features") or []
            feature_tuple: tuple[str, ...] = tuple(
                str(item).strip() for item in features if isinstance(item, str) and item.strip()
            )
            plans[plan_code] = BillingPlanSettings(
                code=plan_code,
                name=str(plan.get("name") or plan_code.title()),
                price_id=price_id,
                product_id=plan.get("product_id"),
                unit_amount_cents=unit_amount,
                currency=plan_currency,
                interval=str(plan.get("interval") or "month"),
                trial_days=int(plan.get("trial_days", default_trial_days) or default_trial_days),
                self_serve=bool(plan.get("self_serve", True)),
                features=feature_tuple,
            )

    stripe_config = billing_config.get("stripe") or {}
    secret_key = _env_str("AICI_STRIPE_SECRET_KEY", stripe_config.get("secret_key"))
    publishable_key = _env_str("AICI_STRIPE_PUBLISHABLE_KEY", stripe_config.get("publishable_key"))
    webhook_secret = _env_str("AICI_STRIPE_WEBHOOK_SECRET", stripe_config.get("webhook_secret"))
    default_domain = stripe_config.get("default_domain") or auth_settings.public_app_url

    checkout_success_url = _absolute_url(
        default_domain,
        stripe_config.get("checkout_success_path"),
        f"{auth_settings.public_app_url.rstrip('/')}/app?billing=success",
    )
    checkout_cancel_url = _absolute_url(
        default_domain,
        stripe_config.get("checkout_cancel_path"),
        f"{auth_settings.public_app_url.rstrip('/')}/app?billing=cancelled",
    )
    portal_return_url = _absolute_url(
        default_domain,
        stripe_config.get("portal_return_path"),
        f"{auth_settings.public_app_url.rstrip('/')}/app?billing=portal",
    )

    crypto_config = billing_config.get("crypto") or {}
    crypto_provider = str(crypto_config.get("provider") or "nowpayments").lower()
    api_key_env = crypto_config.get("api_key_env") or "AICI_CRYPTO_API_KEY"
    api_secret_env = crypto_config.get("api_secret_env") or "AICI_CRYPTO_API_SECRET"
    webhook_secret_env = crypto_config.get("webhook_secret_env") or "AICI_CRYPTO_WEBHOOK_SECRET"
    payout_address_env = crypto_config.get("payout_address_env") or "AICI_CRYPTO_PAYOUT_ADDRESS"
    usd_rate_env = crypto_config.get("usd_rate_env") or "AICI_CRYPTO_USD_RATE"
    fee_percent_env = crypto_config.get("fee_percent_env") or "AICI_CRYPTO_FEE_PERCENT"

    crypto_networks_config = crypto_config.get("networks") or {}
    crypto_networks: dict[str, BillingCryptoNetworkSettings] = {}
    if isinstance(crypto_networks_config, dict):
        for code, payload in crypto_networks_config.items():
            if not isinstance(payload, dict):
                continue
            network_code = str(payload.get("code") or code).lower()
            currency_code = str(payload.get("currency") or "usdt").lower()
            chain_code = str(payload.get("chain") or network_code).lower()
            confirmations_required = _coerce_positive_int(payload.get("confirmations_required")) or 1
            fee_percent = float(payload.get("fee_percent", 0.0) or 0.0)
            crypto_networks[network_code] = BillingCryptoNetworkSettings(
                code=network_code,
                currency=currency_code,
                chain=chain_code,
                confirmations_required=confirmations_required,
                fee_percent=fee_percent,
            )

    default_network = str(crypto_config.get("default_network") or "usdt_trc20").lower()
    if default_network not in crypto_networks and crypto_networks:
        default_network = next(iter(crypto_networks.keys()))
    default_currency = str(crypto_config.get("default_currency") or "usdt").lower()
    network_default_fee = (
        crypto_networks.get(default_network).fee_percent
        if default_network in crypto_networks
        else 0.0
    )
    usd_to_crypto_rate = _env_float(
        usd_rate_env,
        float(crypto_config.get("usd_to_crypto_rate", 1.0) or 1.0),
    )
    service_fee_percent = _env_float(
        fee_percent_env,
        float(crypto_config.get("service_fee_percent", network_default_fee) or network_default_fee),
    )
    crypto_api_key = _env_str(api_key_env, crypto_config.get("api_key"))
    crypto_api_secret = _env_str(api_secret_env, crypto_config.get("api_secret"))
    crypto_webhook_secret = _env_str(webhook_secret_env, crypto_config.get("webhook_secret"))
    crypto_payout_address = _env_str(payout_address_env, crypto_config.get("payout_address"))
    if crypto_networks and service_fee_percent <= 0:
        service_fee_percent = max(network_default_fee, next(iter(crypto_networks.values())).fee_percent)
    service_fee_percent = max(service_fee_percent, 0.0)

    crypto_settings = BillingCryptoSettings(
        provider=crypto_provider,
        api_key=crypto_api_key,
        api_secret=crypto_api_secret,
        webhook_secret=crypto_webhook_secret,
        payout_address=crypto_payout_address,
        default_currency=default_currency,
        default_network=default_network,
        usd_to_crypto_rate=usd_to_crypto_rate,
        service_fee_percent=service_fee_percent,
        networks=crypto_networks,
    )

    return BillingSettings(
        provider=provider,
        currency=currency,
        default_trial_days=default_trial_days,
        enterprise_invoice_terms_days=enterprise_terms,
        checkout_success_url=checkout_success_url,
        checkout_cancel_url=checkout_cancel_url,
        portal_return_url=portal_return_url,
        stripe_secret_key=secret_key,
        stripe_publishable_key=publishable_key,
        stripe_webhook_secret=webhook_secret,
        plans=plans,
        crypto=crypto_settings,
    )


def _coerce_positive_int(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_thresholds(raw_values: object | None) -> tuple[float, ...]:
    if raw_values is None:
        return (0.8, 1.0)
    if not isinstance(raw_values, (list, tuple, set)):
        raw_values = [raw_values]
    cleaned: set[float] = set()
    for item in raw_values:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        if value > 1:
            value = value / 100.0
        cleaned.add(round(min(value, 1.0), 4))
    if not cleaned:
        return (0.8, 1.0)
    return tuple(sorted(cleaned))


def _resolve_token_pricing(api_config: dict[str, Any]) -> ApiTokenPricingSettings:
    pricing_config = api_config.get("token_pricing") or api_config.get("pricing") or {}
    per_call_tokens = _coerce_positive_int(pricing_config.get("per_call_tokens")) or 1
    pipeline_tokens = _coerce_positive_int(pricing_config.get("pipeline_trigger_tokens")) or 2
    minimum_debit = _coerce_positive_int(pricing_config.get("minimum_debit_tokens")) or 1
    per_call_tokens = max(per_call_tokens, minimum_debit)
    pipeline_tokens = max(pipeline_tokens, minimum_debit)
    return ApiTokenPricingSettings(
        per_call_tokens=per_call_tokens,
        pipeline_trigger_tokens=pipeline_tokens,
        minimum_debit_tokens=minimum_debit,
    )


def _resolve_api_key_settings(config: dict[str, Any], auth_settings: AuthSettings) -> ApiKeySettings:
    api_config = config.get("api_keys") or {}
    prefix = str(api_config.get("key_prefix") or "aici_live_")
    max_keys = _coerce_positive_int(api_config.get("max_keys_per_account")) or 5
    default_plan_code = str(api_config.get("default_plan_code") or "free").lower()
    thresholds = _normalize_thresholds(api_config.get("notification_thresholds"))
    token_pricing = _resolve_token_pricing(api_config)

    encryption_secret = (
        os.getenv("AICI_API_KEY_SECRET")
        or api_config.get("encryption_secret")
        or auth_settings.jwt_secret_key
    )
    if not encryption_secret:
        raise RuntimeError("API key encryption secret must be configured.")

    def _normalize_webhook_values(config_value: object, env_value: str | None) -> tuple[str, ...]:
        urls: list[str] = []
        raw_sources: list[object] = []
        if env_value:
            raw_sources.extend(env_value.split(","))
        if isinstance(config_value, str):
            raw_sources.append(config_value)
        elif isinstance(config_value, (list, tuple, set)):
            raw_sources.extend(config_value)
        for entry in raw_sources:
            text = str(entry).strip()
            if text and text not in urls:
                urls.append(text)
            if len(urls) >= 8:
                break
        return tuple(urls)

    webhooks_config = api_config.get("webhooks") or {}
    rotation_webhooks = _normalize_webhook_values(
        webhooks_config.get("rotation"),
        os.getenv("AICI_API_KEY_ROTATION_WEBHOOKS"),
    )
    usage_webhooks = _normalize_webhook_values(
        webhooks_config.get("usage_alerts"),
        os.getenv("AICI_API_KEY_USAGE_WEBHOOKS"),
    )

    raw_plan_configs = dict(_DEFAULT_API_KEY_PLANS)
    user_plans = api_config.get("plans") or {}
    for code, payload in user_plans.items():
        if not isinstance(payload, dict):
            continue
        normalized_code = str(code).lower()
        merged = dict(raw_plan_configs.get(normalized_code, {}))
        merged.update(payload)
        raw_plan_configs[normalized_code] = merged

    plans: dict[str, ApiKeyPlanSettings] = {}
    for code, payload in raw_plan_configs.items():
        if not isinstance(payload, dict):
            continue
        plan_code = str(payload.get("code") or code).lower()
        roles = payload.get("roles") or ()
        if not isinstance(roles, (list, tuple, set)):
            roles = (roles,)
        normalized_roles = tuple(
            str(role).strip().lower()
            for role in roles
            if isinstance(role, str) and role.strip()
        )
        default_role = str(payload.get("default_role") or (normalized_roles[0] if normalized_roles else "standard")).lower()
        if default_role not in normalized_roles:
            normalized_roles = normalized_roles + (default_role,)
        plans[plan_code] = ApiKeyPlanSettings(
            code=plan_code,
            daily_quota=_coerce_positive_int(payload.get("daily_quota")),
            monthly_quota=_coerce_positive_int(payload.get("monthly_quota")),
            burst_per_minute=_coerce_positive_int(payload.get("burst_per_minute")) or 60,
            burst_per_second=_coerce_positive_int(payload.get("burst_per_second")) or 5,
            data_latency_seconds=_coerce_positive_int(payload.get("data_latency_seconds")) or 0,
            max_keys=_coerce_positive_int(payload.get("max_keys")),
            default_role=default_role,
            roles=normalized_roles,
        )

    if default_plan_code not in plans:
        default_plan_code = next(iter(plans.keys()))

    return ApiKeySettings(
        encryption_secret=str(encryption_secret),
        key_prefix=prefix,
        max_keys_per_account=max_keys,
        default_plan_code=default_plan_code,
        notification_thresholds=thresholds,
        token_pricing=token_pricing,
        plans=plans,
        rotation_webhook_urls=rotation_webhooks,
        usage_alert_webhook_urls=usage_webhooks,
    )


def _resolve_auth_settings(config: dict[str, Any], runs_root: Path) -> AuthSettings:
    auth_config = config.get("auth", {})
    default_db_url = _as_sqlite_url(runs_root / "auth.db")

    database_url = os.getenv("AICI_AUTH_DATABASE_URL") or auth_config.get("database_url") or default_db_url
    jwt_secret = os.getenv("AICI_AUTH_JWT_SECRET") or auth_config.get("jwt_secret_key") or "change-me-in-prod"
    jwt_algorithm = os.getenv("AICI_AUTH_JWT_ALG") or auth_config.get("jwt_algorithm") or "HS256"
    session_cookie_name = (
        os.getenv("AICI_AUTH_SESSION_COOKIE") or auth_config.get("session_cookie_name") or "aici_session"
    )
    session_cookie_domain = os.getenv("AICI_AUTH_SESSION_DOMAIN") or auth_config.get("session_cookie_domain")
    public_app_url = os.getenv("AICI_AUTH_APP_URL") or auth_config.get("public_app_url") or "https://aici.pro"

    return AuthSettings(
        database_url=database_url,
        jwt_secret_key=jwt_secret,
        jwt_algorithm=jwt_algorithm,
        access_token_ttl_seconds=_env_int(
            "AICI_AUTH_ACCESS_TTL",
            int(auth_config.get("access_token_ttl_seconds", 3600)),
        ),
        refresh_token_ttl_seconds=_env_int(
            "AICI_AUTH_REFRESH_TTL",
            int(auth_config.get("refresh_token_ttl_seconds", 2592000)),
        ),
        email_token_ttl_seconds=_env_int(
            "AICI_AUTH_EMAIL_TOKEN_TTL",
            int(auth_config.get("email_token_ttl_seconds", 259200)),
        ),
        password_reset_ttl_seconds=_env_int(
            "AICI_AUTH_RESET_TOKEN_TTL",
            int(auth_config.get("password_reset_ttl_seconds", 3600)),
        ),
        session_cookie_name=session_cookie_name,
        session_cookie_domain=session_cookie_domain,
        session_cookie_secure=_env_bool(
            "AICI_AUTH_SESSION_SECURE",
            bool(auth_config.get("session_cookie_secure", False)),
        ),
        public_app_url=public_app_url,
        expose_tokens_in_responses=_env_bool(
            "AICI_AUTH_DEBUG_TOKENS",
            bool(auth_config.get("expose_tokens_in_responses", False)),
        ),
        echo_sql=_env_bool("AICI_AUTH_ECHO_SQL", bool(auth_config.get("echo_sql", False))),
    )


@lru_cache(maxsize=1)
def get_settings() -> ServiceSettings:
    """Load service settings once and cache for subsequent requests."""

    config_candidate = os.getenv(DEFAULT_CONFIG_ENV, "config/pipeline.json")
    config_path = Path(config_candidate).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found at {config_path}")

    config = _load_config(config_path)
    runs_root = _resolve_runs_root(config, config_path)
    auth_settings = _resolve_auth_settings(config, runs_root)
    billing_settings = _resolve_billing_settings(config, auth_settings)
    api_key_settings = _resolve_api_key_settings(config, auth_settings)
    google_client_id = _env_str("GOOGLE_CLIENT_ID", config.get("google_client_id"))
    google_client_secret = _env_str("GOOGLE_CLIENT_SECRET", config.get("google_client_secret"))
    return ServiceSettings(
        config_path=config_path,
        runs_root=runs_root,
        auth=auth_settings,
        billing=billing_settings,
        api_keys=api_key_settings,
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
    )
