from __future__ import annotations

import base64
import csv
import asyncio
import copy
import hashlib
import io
import json
import logging
import os
import secrets
import textwrap
import time
import uuid
import zipfile
from contextlib import suppress
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, NoReturn
from datetime import date, datetime, timedelta, timezone
from functools import partial
from math import ceil, fsum, isfinite
from pathlib import Path
from ipaddress import ip_address, ip_network
from threading import Event, Lock
from urllib.parse import parse_qsl, urlencode, urlparse

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, pass_context
import httpx
from jose import JWTError, jwt
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.routing import NoMatchFound

from ai_crypto_index.accounts import bootstrap as accounts_bootstrap
from ai_crypto_index.accounts import ensure_schema as ensure_auth_schema
from ai_crypto_index.accounts import models as account_models
from ai_crypto_index.accounts.db import get_sessionmaker as get_auth_sessionmaker
from ai_crypto_index.accounts.dependencies import get_account_service, get_db_session
from ai_crypto_index.accounts.exceptions import (
    AccountAlreadyExists,
    AccountInactive,
    AccountNotFound,
    ConfirmationResendRateLimited,
    InvalidCredentials,
    SessionInvalid,
    TokenExpired,
    TokenInvalid,
)
from ai_crypto_index.accounts.service import AccountService, RequestContext
from ai_crypto_index.api_keys.dependencies import get_api_key_service
from ai_crypto_index.api_keys.exceptions import (
    ApiKeyInactive,
    ApiKeyLimitReached,
    ApiKeyNotFound,
    ApiKeyQuotaExceeded,
    ApiKeyRestrictionError,
    InvalidApiKeySecret,
)
from ai_crypto_index.api_keys.service import ApiKeyAuthContext, ApiKeyLimits, ApiKeyService, ApiKeyUsageSnapshot
from ai_crypto_index.billing import (
    BillingError,
    BillingConfigurationError,
    BillingPlanNotFound,
    BillingService,
    StripeWebhookError,
)
from ai_crypto_index.billing.dependencies import get_billing_service
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from ai_crypto_index.api.logging_utils import configure_logging
from ai_crypto_index.api.rate_limit import RateLimitMiddleware
from ai_crypto_index.api.serializers import to_builtin
from ai_crypto_index.pipelines.main import run_monthly_update
from ai_crypto_index.shared import cta_analytics_store, daily_snapshot, email_notifications, intake_store, run_store
from ai_crypto_index.shared.cta_metrics_service import CtaMetricsQuery, CtaMetricsService
from ai_crypto_index.shared.live_backtest_data import (
    build_live_backtest_payload,
    resolve_live_backtest_strategy_key,
    store_live_run_month,
)
from ai_crypto_index.shared.monthly_composition import refresh_monthly_snapshots_store
from ai_crypto_index.shared.monthly_job_lock import (
    MonthlyJobLockBusyError,
    hold_monthly_job_lock,
)
from ai_crypto_index.shared.performance_snapshot import (
    PerformanceBundle,
    PerformanceSnapshotError,
    load_performance_bundle,
)
from ai_crypto_index.shared.performance_refresh import (
    AutoRunConfig,
    collect_variant_snapshots,
    collect_benchmark_snapshots,
    latest_snapshot_date,
    load_auto_config,
    persist_auto_config,
    refresh_performance_data,
    update_next_run_after_failure,
    update_next_run_after_success,
)

from . import models
from .dependencies import ServiceSettings, get_settings
from ai_crypto_index.shared.settings import ApiKeyPlanSettings

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent.parent
FRONTEND_TEMPLATES_DIR = PACKAGE_ROOT / "frontend" / "templates"
FRONTEND_STATIC_DIR = PACKAGE_ROOT / "frontend" / "static"
DIST_ROOT = REPO_ROOT / "dist"
DIST_TEMPLATES_DIR = DIST_ROOT / "templates"
DIST_STATIC_DIR = DIST_ROOT / "static"
ASSET_MANIFEST_ENV = "AICI_ASSET_MANIFEST_PATH"
STATIC_CDN_BASE_ENV = "AICI_STATIC_CDN_BASE_URL"
ASSET_MANIFEST_DEFAULT_PATH = DIST_ROOT / "asset-manifest.json"
_PIPELINE_ENABLED_FLAGS = {"1", "true", "on", "yes"}
_TOKEN_COST_DEFAULT = 5
_TOKEN_COST_PIPELINE_TRIGGER = 0
_TOKEN_MINIMUM_DEBIT = 1
_TOKEN_COST_RUN_READ = 5
_TOKEN_COST_RUN_STATUS = 0

def _prefer_src_templates() -> bool:
    raw_value = os.getenv("AICI_PREFER_SRC_TEMPLATES")
    if raw_value is None:
        return True
    return raw_value.lower() in _PIPELINE_ENABLED_FLAGS

def _select_frontend_assets() -> tuple[Path, list[Path]]:
    prefer_src = _prefer_src_templates()
    dist_ready = DIST_TEMPLATES_DIR.exists() and DIST_STATIC_DIR.exists()
    if dist_ready and not prefer_src:
        return DIST_STATIC_DIR, [DIST_TEMPLATES_DIR, FRONTEND_TEMPLATES_DIR]
    return FRONTEND_STATIC_DIR, [FRONTEND_TEMPLATES_DIR]


def _sync_token_pricing(settings: ServiceSettings) -> None:
    global _TOKEN_COST_DEFAULT, _TOKEN_COST_PIPELINE_TRIGGER, _TOKEN_MINIMUM_DEBIT
    pricing = getattr(settings.api_keys, "token_pricing", None)
    if pricing is None:
        return
    _TOKEN_COST_DEFAULT = max(int(getattr(pricing, "per_call_tokens", _TOKEN_COST_DEFAULT)), 1)
    _TOKEN_COST_PIPELINE_TRIGGER = max(
        int(getattr(pricing, "pipeline_trigger_tokens", _TOKEN_COST_PIPELINE_TRIGGER)),
        _TOKEN_COST_DEFAULT,
    )
    _TOKEN_MINIMUM_DEBIT = max(int(getattr(pricing, "minimum_debit_tokens", _TOKEN_MINIMUM_DEBIT)), 1)

STATIC_DIR, TEMPLATE_DIRS = _select_frontend_assets()

SDK_BUNDLES = {
    "python": {
        "download_name": "aici-python-sdk.zip",
        "sources": [
            (REPO_ROOT / "sdk" / "python", Path("aici-python-sdk") / "sdk"),
            (
                REPO_ROOT / "examples" / "sdk_python_quickstart",
                Path("aici-python-sdk") / "examples" / "sdk_python_quickstart",
            ),
        ],
    },
    "js": {
        "download_name": "aici-js-sdk.zip",
        "sources": [
            (REPO_ROOT / "sdk" / "js", Path("aici-js-sdk") / "sdk"),
            (
                REPO_ROOT / "examples" / "sdk_js_quickstart",
                Path("aici-js-sdk") / "examples" / "sdk_js_quickstart",
            ),
        ],
    },
}


def _compose_sdk_bundle(sources: list[tuple[Path, Path]]) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, prefix in sources:
            root = root.resolve()
            if not root.exists():
                raise FileNotFoundError(root)
            for file_path in root.rglob("*"):
                if not file_path.is_file():
                    continue
                arcname = (prefix / file_path.relative_to(root)).as_posix()
                archive.write(file_path, arcname)
    buffer.seek(0)
    return buffer


def _resolve_asset_manifest_path() -> Path:
    manifest_candidate = os.getenv(ASSET_MANIFEST_ENV)
    if manifest_candidate:
        return Path(manifest_candidate).expanduser()
    return ASSET_MANIFEST_DEFAULT_PATH


def _load_asset_manifest(manifest_path: Path) -> dict[str, dict[str, object]]:
    if not manifest_path.exists():
        return {}

    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logging.getLogger("ai_crypto_index.api").warning(
            "Failed to load asset manifest from %s: %s", manifest_path, exc
        )
        return {}

    if not isinstance(loaded, dict):
        logging.getLogger("ai_crypto_index.api").warning(
            "Asset manifest at %s is not a mapping, ignoring it.", manifest_path
        )
        return {}

    return loaded


def _normalize_cdn_base(raw_value: str | None) -> str:
    if not raw_value:
        return ""
    return raw_value.rstrip("/")


def _normalize_manifest_lookup(asset_path: str) -> tuple[str, str]:
    normalized = asset_path.lstrip("/")
    if normalized.startswith("static/"):
        manifest_key = normalized
        static_relative = normalized[len("static/") :]
    else:
        manifest_key = f"static/{normalized}"
        static_relative = normalized
    return manifest_key, static_relative


def _extract_hash(manifest: dict[str, dict[str, object]], manifest_key: str) -> str | None:
    entry = manifest.get(manifest_key)
    if isinstance(entry, dict):
        candidate = entry.get("hash")
        if isinstance(candidate, str) and candidate:
            return candidate[:12]
    return None


def _resolve_static_version_hint(
    manifest: dict[str, dict[str, object]],
    manifest_key: str,
    relative_path: str,
) -> str | None:
    version_hint = _extract_hash(manifest, manifest_key)
    if version_hint:
        return version_hint

    static_file_path = STATIC_DIR / Path(relative_path)
    try:
        return str(static_file_path.stat().st_mtime_ns)
    except OSError:
        return None


def _append_version_hint(url: str, version_hint: str | None) -> str:
    if not version_hint:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}v={version_hint}"


def _build_static_asset_url(
    request: Request,
    asset_path: str,
    manifest: dict[str, dict[str, object]],
    cdn_base_url: str,
) -> str:
    manifest_key, relative_path = _normalize_manifest_lookup(asset_path)
    version_hint = _resolve_static_version_hint(manifest, manifest_key, relative_path)

    if cdn_base_url:
        return _append_version_hint(f"{cdn_base_url}/{manifest_key}", version_hint)

    try:
        return _append_version_hint(str(request.url_for("static", path=relative_path)), version_hint)
    except NoMatchFound:
        # Fallback to a predictable relative reference if static route is not mounted.
        return _append_version_hint(f"/static/{relative_path}", version_hint)


def _build_cdn_aware_url_for(
    manifest: dict[str, dict[str, object]],
    cdn_base_url: str,
):
    normalized_base = _normalize_cdn_base(cdn_base_url)

    @pass_context
    def url_for_with_cdn(context, name: str, **path_params: object) -> str:
        request = context.get("request")
        if request is None or not isinstance(request, Request):
            raise RuntimeError("Jinja context is missing 'request' for url_for resolution.")

        if name != "static":
            return request.url_for(name, **path_params)

        asset_path = str(path_params.get("path", ""))
        if not asset_path:
            return request.url_for(name, **path_params)

        return _build_static_asset_url(
            request=request,
            asset_path=asset_path,
            manifest=manifest,
            cdn_base_url=normalized_base,
        )

    return url_for_with_cdn

def _build_template_env(template_dirs: list[Path] | None = None) -> Environment:
    search_paths = template_dirs or TEMPLATE_DIRS
    loaders = [FileSystemLoader(str(path)) for path in search_paths]
    loader = loaders[0] if len(loaders) == 1 else ChoiceLoader(loaders)
    return Environment(loader=loader, autoescape=True)


def _refresh_frontend_assets() -> None:
    global STATIC_DIR, TEMPLATE_DIRS, templates
    STATIC_DIR, TEMPLATE_DIRS = _select_frontend_assets()
    templates = Jinja2Templates(env=_build_template_env(TEMPLATE_DIRS))


templates = Jinja2Templates(env=_build_template_env(TEMPLATE_DIRS))
API_PREFIX = "/api"
API_VERSION_LABEL = "v1"
API_VERSION = "1.0.0"
API_VERSION_ROUTE = f"/{API_VERSION_LABEL}"
API_BASE_PATH = f"{API_PREFIX.rstrip('/')}{API_VERSION_ROUTE}"
FREE_PLAN_CODE = "free"
FREE_PLAN_MAX_N_TOP_COINS = 200
FREE_PLAN_MAX_TOTAL_ASSETS = 12
RUN_REQUEST_DEFAULTS = models.RunRequest()


@dataclass(frozen=True)
class IndexAutoStrategyProfile:
    strategy_key: str
    run_prefix: str
    run_kwargs: dict[str, object]


def _resolve_index_auto_prefix(env_name: str, default: str) -> str:
    candidate = (os.getenv(env_name) or "").strip()
    return candidate or default


_INDEX_AUTO_PREFIX = _resolve_index_auto_prefix("AICI_INDEX_AUTO_PREFIX", "auto-classic")
_INDEX_AUTO_STRATEGY_PROFILES: tuple[IndexAutoStrategyProfile, ...] = (
    IndexAutoStrategyProfile(
        strategy_key="classic",
        run_prefix=_INDEX_AUTO_PREFIX,
        run_kwargs={
            "total_assets": RUN_REQUEST_DEFAULTS.total_assets,
            "risk_min_weight": RUN_REQUEST_DEFAULTS.risk_min_weight,
            "risk_max_weight": RUN_REQUEST_DEFAULTS.risk_max_weight,
            "weight_cap": RUN_REQUEST_DEFAULTS.weight_cap,
            "vol_floor_ratio": RUN_REQUEST_DEFAULTS.vol_floor_ratio,
            "gating_tolerance": RUN_REQUEST_DEFAULTS.gating_tolerance,
        },
    ),
    IndexAutoStrategyProfile(
        strategy_key="conservative",
        run_prefix=_resolve_index_auto_prefix("AICI_INDEX_AUTO_PREFIX_CONSERVATIVE", "auto-conservative"),
        run_kwargs={
            "total_assets": 12,
            "risk_min_weight": 0.02,
            "risk_max_weight": 0.18,
            "weight_cap": 0.12,
            "vol_floor_ratio": 0.5,
            "gating_tolerance": 0.015,
        },
    ),
    IndexAutoStrategyProfile(
        strategy_key="aggressive",
        run_prefix=_resolve_index_auto_prefix("AICI_INDEX_AUTO_PREFIX_AGGRESSIVE", "auto-aggressive"),
        run_kwargs={
            "total_assets": 8,
            "risk_min_weight": 0.01,
            "risk_max_weight": 0.35,
            "weight_cap": 0.25,
            "vol_floor_ratio": 0.3,
            "gating_tolerance": 0.03,
        },
    ),
)


def _index_auto_profiles() -> tuple[IndexAutoStrategyProfile, ...]:
    """Return index auto profiles with de-duplicated prefixes."""

    seen_prefixes: set[str] = set()
    profiles: list[IndexAutoStrategyProfile] = []
    for profile in _INDEX_AUTO_STRATEGY_PROFILES:
        normalized_prefix = (profile.run_prefix or "").strip()
        if not normalized_prefix or normalized_prefix in seen_prefixes:
            continue
        seen_prefixes.add(normalized_prefix)
        profiles.append(
            IndexAutoStrategyProfile(
                strategy_key=profile.strategy_key,
                run_prefix=normalized_prefix,
                run_kwargs=dict(profile.run_kwargs),
            )
        )
    return tuple(profiles)


PIPELINE_STAGE_SEQUENCE = [
    {"key": "prep", "label": "Preparing run and resolving configuration..."},
    {"key": "download", "label": "Downloading and merging market data..."},
    {"key": "cluster", "label": "Clustering assets and filtering history..."},
    {"key": "train", "label": "Training forecasts for shortlisted assets..."},
    {"key": "optimize", "label": "Optimizing weights and computing metrics..."},
]
_PIPELINE_PROGRESS: dict[str, dict[str, object]] = {}
_PIPELINE_PROGRESS_LOCK = Lock()
_PIPELINE_PROGRESS_LOG_LIMIT = 200
_PIPELINE_CANCEL_FLAGS: dict[str, Event] = {}
_PIPELINE_RESULT_CACHE: dict[str, dict[str, object]] = {}
_PIPELINE_RESULT_CACHE_LOCK = Lock()
_PIPELINE_RESULT_CACHE_LIMIT = 100
_PERFORMANCE_RUN_LOCK = asyncio.Lock()
_PERFORMANCE_STATUS: dict[str, object] = {
    "state": "idle",
    "reason": None,
    "started_at": None,
    "last_run_at": None,
    "last_status": None,
    "last_error": None,
}
_PERFORMANCE_AUTO_TASK: asyncio.Task | None = None
_PERFORMANCE_POLL_SECONDS = int(os.getenv("AICI_PERFORMANCE_POLL_SECONDS", "3600"))
_PERFORMANCE_AUTO_ENABLED = os.getenv("AICI_PERFORMANCE_AUTO_ENABLED", "1").lower() in _PIPELINE_ENABLED_FLAGS
try:
    _MONTHLY_JOB_LOCK_STALE_SECONDS = max(int(os.getenv("AICI_MONTHLY_JOB_LOCK_STALE_SECONDS", "21600")), 0)
except ValueError:
    _MONTHLY_JOB_LOCK_STALE_SECONDS = 21600
_PERFORMANCE_AUTO_LOCK_CONTOUR = "performance-auto"
_INDEX_AUTO_STATE_DIR = "_index_auto"
_INDEX_AUTO_CONFIG_NAME = "auto_config.json"
_INDEX_AUTO_POLL_SECONDS = int(os.getenv("AICI_INDEX_AUTO_POLL_SECONDS", "21600"))
_INDEX_AUTO_LOCK_CONTOUR = "index-auto"
_INDEX_AUTO_TASK: asyncio.Task | None = None
_INDEX_AUTO_LOCK = asyncio.Lock()
_INDEX_AUTO_STATUS: dict[str, object] = {
    "state": "idle",
    "started_at": None,
    "last_run_at": None,
    "last_status": None,
    "last_error": None,
    "last_run_id": None,
    "strategy_runs": [],
}
_DAILY_SNAPSHOT_TASK: asyncio.Task | None = None
_DAILY_SNAPSHOT_LOCK = asyncio.Lock()
_DAILY_SNAPSHOT_ENABLED = os.getenv("AICI_DAILY_SNAPSHOT_ENABLED", "1").lower() in _PIPELINE_ENABLED_FLAGS
try:
    _DAILY_SNAPSHOT_HOUR_UTC = int(
        os.getenv("AICI_DAILY_SNAPSHOT_HOUR_UTC", str(daily_snapshot.DEFAULT_SNAPSHOT_HOUR_UTC))
    )
except ValueError:
    _DAILY_SNAPSHOT_HOUR_UTC = daily_snapshot.DEFAULT_SNAPSHOT_HOUR_UTC
_DAILY_SNAPSHOT_HOUR_UTC = min(max(_DAILY_SNAPSHOT_HOUR_UTC, 0), 23)
try:
    _DAILY_SNAPSHOT_MINUTE_UTC = int(os.getenv("AICI_DAILY_SNAPSHOT_MINUTE_UTC", "0"))
except ValueError:
    _DAILY_SNAPSHOT_MINUTE_UTC = 0
_DAILY_SNAPSHOT_MINUTE_UTC = min(max(_DAILY_SNAPSHOT_MINUTE_UTC, 0), 59)
_DAILY_SNAPSHOT_STATUS: dict[str, object] = {
    "state": "idle",
    "started_at": None,
    "last_run_at": None,
    "last_status": None,
    "last_error": None,
    "last_snapshot_date": None,
    "last_source_date": None,
    "last_storage_uri": None,
    "stale": None,
    "last_alert_date": None,
}
_BILLING_REMINDER_TASK: asyncio.Task | None = None
_BILLING_REMINDER_POLL_SECONDS = int(os.getenv("AICI_BILLING_REMINDER_SECONDS", "3600"))
_BILLING_REMINDERS_ENABLED = os.getenv("AICI_BILLING_REMINDERS_ENABLED", "1").lower() in _PIPELINE_ENABLED_FLAGS
_CTA_FORMAT_OPTIMIZATION_TASK: asyncio.Task | None = None
_CTA_FORMAT_OPTIMIZATION_LOCK = asyncio.Lock()
_CTA_FORMAT_OPTIMIZATION_ENABLED = (
    os.getenv("AICI_CTA_FORMAT_OPTIMIZATION_ENABLED", "1").lower() in _PIPELINE_ENABLED_FLAGS
)
try:
    _CTA_FORMAT_OPTIMIZATION_POLL_SECONDS = max(int(os.getenv("AICI_CTA_FORMAT_OPTIMIZATION_POLL_SECONDS", "3600")), 60)
except ValueError:
    _CTA_FORMAT_OPTIMIZATION_POLL_SECONDS = 3600
try:
    _CTA_FORMAT_OPTIMIZATION_WINDOW_DAYS = max(
        int(
            os.getenv(
                "AICI_CTA_FORMAT_OPTIMIZATION_WINDOW_DAYS",
                str(cta_analytics_store.DEFAULT_FORMAT_OPTIMIZATION_WINDOW_DAYS),
            )
        ),
        1,
    )
except ValueError:
    _CTA_FORMAT_OPTIMIZATION_WINDOW_DAYS = cta_analytics_store.DEFAULT_FORMAT_OPTIMIZATION_WINDOW_DAYS
try:
    _CTA_FORMAT_OPTIMIZATION_TOP_N = max(
        int(
            os.getenv(
                "AICI_CTA_FORMAT_OPTIMIZATION_TOP_N",
                str(cta_analytics_store.DEFAULT_FORMAT_OPTIMIZATION_TOP_N),
            )
        ),
        1,
    )
except ValueError:
    _CTA_FORMAT_OPTIMIZATION_TOP_N = cta_analytics_store.DEFAULT_FORMAT_OPTIMIZATION_TOP_N
_CTA_FORMAT_OPTIMIZATION_STATUS: dict[str, object] = {
    "state": "idle",
    "started_at": None,
    "last_run_at": None,
    "last_status": None,
    "last_error": None,
    "last_decision_id": None,
    "last_top_formats": [],
}
_CTA_DEDUP_WINDOW_SECONDS = 8.0
_CTA_METADATA_MAX_KEYS = 40
_CTA_METADATA_MAX_ITEMS = 20
_CTA_METADATA_MAX_KEY_LENGTH = 64
_CTA_METADATA_MAX_TEXT_LENGTH = 240
_CTA_USER_AGENT_MAX_LENGTH = 400
_CTA_REFERRER_MAX_LENGTH = 600
_CTA_PAGE_PATH_MAX_LENGTH = 240
_CTA_UTM_MAX_LENGTH = 200
_CTA_ALLOWED_EVENT_TYPES = {"cta_click", "signup_started", "email_confirmed", "paid"}
_CTA_DEDUP_LOCK = Lock()
_CTA_DEDUP_TIMELINE: deque[tuple[float, str]] = deque()
_CTA_DEDUP_LAST_SEEN: dict[str, float] = {}
API_TAGS_METADATA = [
    {
        "name": "system",
        "description": "Service health and operational metadata.",
    },
    {
        "name": "performance",
        "description": "Portfolio performance, weights, and historical metrics.",
    },
    {
        "name": "intake",
        "description": "Lead capture and interaction tracking endpoints.",
    },
    {
        "name": "pipeline",
        "description": "Data pipeline orchestration controls.",
    },
    {
        "name": "billing",
        "description": "Self-serve checkout, subscription status, and Stripe/Crypto webhooks.",
    },
]

bearer_scheme = HTTPBearer(auto_error=False)


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        if limit <= 0 or window_seconds <= 0:
            return
        if not key:
            key = "unknown"
        now = time.monotonic()
        async with self._lock:
            bucket = self._hits[key]
            cutoff = now - window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")
            bucket.append(now)



def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_optional_text(value: object | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        return text[:max_length]
    return text


def _normalize_cta_metadata_key(raw_key: object) -> str | None:
    key = _normalize_optional_text(raw_key, max_length=_CTA_METADATA_MAX_KEY_LENGTH * 2)
    if key is None:
        return None
    normalized = key.lower().replace(" ", "_")
    normalized = "".join(ch for ch in normalized if ch.isalnum() or ch in {"_", ".", "-"})
    if len(normalized) > _CTA_METADATA_MAX_KEY_LENGTH:
        normalized = normalized[:_CTA_METADATA_MAX_KEY_LENGTH]
    return normalized or None


def _normalize_cta_metadata_value(value: object, *, depth: int = 0) -> object | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            return None
        return value
    if isinstance(value, str):
        return _normalize_optional_text(value, max_length=_CTA_METADATA_MAX_TEXT_LENGTH)
    if isinstance(value, (list, tuple, set)):
        if depth >= 1:
            return None
        normalized_items: list[object] = []
        for item in list(value)[:_CTA_METADATA_MAX_ITEMS]:
            normalized_item = _normalize_cta_metadata_value(item, depth=depth + 1)
            if normalized_item is not None:
                normalized_items.append(normalized_item)
        return normalized_items
    if isinstance(value, Mapping):
        if depth >= 1:
            return None
        normalized_object: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if len(normalized_object) >= _CTA_METADATA_MAX_ITEMS:
                break
            normalized_key = _normalize_cta_metadata_key(raw_key)
            if normalized_key is None or normalized_key in normalized_object:
                continue
            normalized_value = _normalize_cta_metadata_value(raw_value, depth=depth + 1)
            if normalized_value is None and raw_value is not None:
                continue
            normalized_object[normalized_key] = normalized_value
        return normalized_object
    return _normalize_optional_text(value, max_length=_CTA_METADATA_MAX_TEXT_LENGTH)


def _normalize_cta_metadata(metadata: object | None) -> dict[str, object]:
    if not isinstance(metadata, Mapping):
        return {}
    normalized_metadata: dict[str, object] = {}
    for raw_key, raw_value in metadata.items():
        if len(normalized_metadata) >= _CTA_METADATA_MAX_KEYS:
            break
        normalized_key = _normalize_cta_metadata_key(raw_key)
        if normalized_key is None or normalized_key in normalized_metadata:
            continue
        normalized_value = _normalize_cta_metadata_value(raw_value)
        if normalized_value is None and raw_value is not None:
            continue
        normalized_metadata[normalized_key] = normalized_value
    return normalized_metadata


def _normalize_cta_location(location: object | None) -> str:
    normalized = _normalize_optional_text(location, max_length=160)
    if normalized is None:
        return "unknown"
    value = normalized.lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "/": "landing",
        "/pricing": "pricing",
        "hero_section": "hero",
        "api_section": "api_section",
    }
    value = aliases.get(value, value)
    if value.startswith("/"):
        value = value.strip("/") or "landing"
    value = "".join(ch for ch in value if ch.isalnum() or ch in {"_", "/", "."})
    return value or "unknown"


def _normalize_cta_href(href: object | None) -> str | None:
    normalized = _normalize_optional_text(href, max_length=320)
    if normalized is None:
        return None
    head, sep, tail = normalized.partition("#")
    if sep and head:
        return head
    if sep and not head:
        return f"#{tail}" if tail else "#"
    return normalized


def _extract_cta_path(value: object | None) -> str | None:
    normalized = _normalize_optional_text(value, max_length=320)
    if normalized is None:
        return None
    if normalized.startswith("/"):
        return normalized
    try:
        parsed = urlparse(normalized)
    except ValueError:
        return None
    if parsed.path:
        return parsed.path
    return None


def _normalize_cta_page_path(
    value: object | None,
    *,
    href: object | None,
    referer: object | None,
) -> str:
    raw = _normalize_optional_text(value, max_length=_CTA_PAGE_PATH_MAX_LENGTH)
    candidate = raw or _extract_cta_path(href) or _extract_cta_path(referer) or "/"
    trimmed = candidate.strip().lower()
    if not trimmed:
        return "/"
    if not trimmed.startswith("/"):
        trimmed = f"/{trimmed.lstrip('/')}"
    return trimmed[:_CTA_PAGE_PATH_MAX_LENGTH]


def _extract_cta_utm_value(field: str, *, href: object | None, referer: object | None) -> str | None:
    for candidate in (href, referer):
        normalized = _normalize_optional_text(candidate, max_length=_CTA_REFERRER_MAX_LENGTH)
        if normalized is None:
            continue
        try:
            parsed = urlparse(normalized)
        except ValueError:
            continue
        for key, value in parse_qsl(parsed.query, keep_blank_values=False):
            if key.strip().lower() != field:
                continue
            extracted = _normalize_optional_text(value, max_length=_CTA_UTM_MAX_LENGTH)
            if extracted is not None:
                return extracted.lower()
    return None


def _normalize_cta_utm_value(
    value: object | None,
    *,
    field: str,
    href: object | None,
    referer: object | None,
) -> str | None:
    normalized = _normalize_optional_text(value, max_length=_CTA_UTM_MAX_LENGTH)
    candidate = normalized.lower() if normalized is not None else None
    if candidate is None:
        candidate = _extract_cta_utm_value(field, href=href, referer=referer)
    if candidate is None:
        return None
    return candidate or None


def _normalize_cta_event_type(value: object | None) -> str:
    normalized = _normalize_optional_text(value, max_length=64)
    if normalized is None:
        return "cta_click"
    candidate = normalized.lower()
    if candidate in _CTA_ALLOWED_EVENT_TYPES:
        return candidate
    return "cta_click"


def _normalize_cta_format(value: object | None) -> str:
    normalized = _normalize_optional_text(value, max_length=120)
    if normalized is None:
        return "unknown"
    candidate = normalized.lower()
    return candidate or "unknown"


def _resolve_cta_unique_actor_id(
    metadata: Mapping[str, object],
    *,
    remote_ip: object | None,
    user_agent: object | None,
    referer: object | None,
) -> str:
    actor_id = _normalize_optional_text(metadata.get("actor_id"), max_length=160)
    if actor_id is not None:
        return actor_id.lower()
    account_id = _normalize_optional_text(metadata.get("account_id"), max_length=120)
    if account_id is not None:
        return f"account:{account_id}"
    session_id = _normalize_optional_text(metadata.get("session_id"), max_length=120)
    if session_id is not None:
        return f"session:{session_id}"
    fingerprint = _normalize_optional_text(metadata.get("fingerprint"), max_length=120)
    if fingerprint is not None:
        return f"fingerprint:{fingerprint}"

    fingerprint_parts = [
        _normalize_optional_text(remote_ip, max_length=80) or "",
        _normalize_optional_text(user_agent, max_length=240) or "",
        _normalize_optional_text(referer, max_length=320) or "",
    ]
    fingerprint_source = "|".join(fingerprint_parts)
    if fingerprint_source.strip("|"):
        digest = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:24]
        return f"fingerprint:{digest}"
    return "anonymous"


def _build_cta_dedup_signature(
    *,
    dedup_scope: str,
    event_type: str,
    cta_id: str,
    cta_format: str,
    unique_actor_id: str,
) -> str:
    source = "|".join(
        [
            dedup_scope,
            event_type,
            cta_id,
            cta_format,
            unique_actor_id,
        ]
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _is_cta_duplicate(*, dedup_signature: str, event_epoch: float) -> bool:
    if not isfinite(event_epoch):
        event_epoch = _utc_now().timestamp()
    cutoff = event_epoch - _CTA_DEDUP_WINDOW_SECONDS
    with _CTA_DEDUP_LOCK:
        while _CTA_DEDUP_TIMELINE and _CTA_DEDUP_TIMELINE[0][0] < cutoff:
            seen_at, seen_signature = _CTA_DEDUP_TIMELINE.popleft()
            if _CTA_DEDUP_LAST_SEEN.get(seen_signature) == seen_at:
                _CTA_DEDUP_LAST_SEEN.pop(seen_signature, None)
        previous_seen_at = _CTA_DEDUP_LAST_SEEN.get(dedup_signature)
        _CTA_DEDUP_LAST_SEEN[dedup_signature] = event_epoch
        _CTA_DEDUP_TIMELINE.append((event_epoch, dedup_signature))
    return previous_seen_at is not None and (event_epoch - previous_seen_at) <= _CTA_DEDUP_WINDOW_SECONDS


def _build_cta_analytics_record(
    record: Mapping[str, Any],
    *,
    event_id: str,
    received_at: str,
    dedup_scope: str,
) -> tuple[dict[str, object], bool]:
    cta_id = (_normalize_optional_text(record.get("cta_id"), max_length=120) or "unknown").lower()
    location_raw = _normalize_optional_text(record.get("location"), max_length=160)
    location = _normalize_cta_location(record.get("location"))
    href = _normalize_cta_href(record.get("href"))
    metadata = _normalize_cta_metadata(record.get("metadata"))
    for identity_key in ("actor_id", "account_id", "session_id", "fingerprint"):
        normalized_identity = _normalize_optional_text(record.get(identity_key), max_length=160)
        if normalized_identity is not None and metadata.get(identity_key) is None:
            metadata[identity_key] = normalized_identity

    event_type = _normalize_cta_event_type(record.get("event_type") or metadata.get("event_type"))
    cta_format = _normalize_cta_format(
        record.get("cta_format") or metadata.get("cta_format") or metadata.get("cta_type")
    )
    page_path = _normalize_cta_page_path(
        record.get("page_path") or metadata.get("page_path"),
        href=href,
        referer=record.get("referer"),
    )
    utm_source = _normalize_cta_utm_value(
        record.get("utm_source") or metadata.get("utm_source"),
        field="utm_source",
        href=href,
        referer=record.get("referer"),
    )
    utm_medium = _normalize_cta_utm_value(
        record.get("utm_medium") or metadata.get("utm_medium"),
        field="utm_medium",
        href=href,
        referer=record.get("referer"),
    )
    utm_campaign = _normalize_cta_utm_value(
        record.get("utm_campaign") or metadata.get("utm_campaign"),
        field="utm_campaign",
        href=href,
        referer=record.get("referer"),
    )
    utm_content = _normalize_cta_utm_value(
        record.get("utm_content") or metadata.get("utm_content"),
        field="utm_content",
        href=href,
        referer=record.get("referer"),
    )
    utm_term = _normalize_cta_utm_value(
        record.get("utm_term") or metadata.get("utm_term"),
        field="utm_term",
        href=href,
        referer=record.get("referer"),
    )
    unique_actor_id = _resolve_cta_unique_actor_id(
        metadata,
        remote_ip=record.get("remote_ip"),
        user_agent=record.get("user_agent"),
        referer=record.get("referer"),
    )
    dedup_signature = _build_cta_dedup_signature(
        dedup_scope=dedup_scope,
        event_type=event_type,
        cta_id=cta_id,
        cta_format=cta_format,
        unique_actor_id=unique_actor_id,
    )
    event_timestamp = _normalize_optional_text(record.get("timestamp"), max_length=64) or received_at
    try:
        event_epoch = datetime.fromisoformat(event_timestamp).timestamp()
    except ValueError:
        try:
            event_epoch = datetime.fromisoformat(received_at).timestamp()
        except ValueError:
            event_epoch = _utc_now().timestamp()
    is_duplicate = _is_cta_duplicate(dedup_signature=dedup_signature, event_epoch=event_epoch)
    return (
        {
            "event_id": event_id,
            "timestamp": event_timestamp,
            "event_type": event_type,
            "cta_id": cta_id,
            "cta_format": cta_format,
            "location_raw": location_raw,
            "location": location,
            "page_path": page_path,
            "utm_source": utm_source,
            "utm_medium": utm_medium,
            "utm_campaign": utm_campaign,
            "utm_content": utm_content,
            "utm_term": utm_term,
            "href": href,
            "metadata": metadata,
            "referer": _normalize_optional_text(record.get("referer"), max_length=_CTA_REFERRER_MAX_LENGTH),
            "user_agent": _normalize_optional_text(
                record.get("user_agent"),
                max_length=_CTA_USER_AGENT_MAX_LENGTH,
            ),
            "received_at": received_at,
            "unique_actor_id": unique_actor_id,
            "dedup_signature": dedup_signature,
            "dedup_window_seconds": int(_CTA_DEDUP_WINDOW_SECONDS),
            "is_duplicate": is_duplicate,
        },
        is_duplicate,
    )


def _init_pipeline_progress(run_id: str) -> dict[str, object]:
    now = _utc_now()
    stages = [
        {
            "key": stage["key"],
            "label": stage["label"],
            "status": "pending",
            "message": None,
            "updated_at": None,
        }
        for stage in PIPELINE_STAGE_SEQUENCE
    ]
    with _PIPELINE_PROGRESS_LOCK:
        _PIPELINE_PROGRESS[run_id] = {
            "run_id": run_id,
            "state": "running",
            "started_at": now,
            "updated_at": now,
            "stages": stages,
            "logs": [],
        }
        if len(_PIPELINE_PROGRESS) > 50:
            stale_keys = list(_PIPELINE_PROGRESS.keys())[:-50]
            for key in stale_keys:
                _PIPELINE_PROGRESS.pop(key, None)
    return _PIPELINE_PROGRESS[run_id]


def _update_pipeline_progress(
    run_id: str,
    *,
    stage: str | None = None,
    status: str | None = None,
    message: str | None = None,
    state: str | None = None,
) -> None:
    now = _utc_now()
    with _PIPELINE_PROGRESS_LOCK:
        progress = _PIPELINE_PROGRESS.get(run_id)
        if not progress:
            return
        progress["updated_at"] = now
        if state:
            progress["state"] = state
            progress.setdefault("finished_at", now)
        if stage:
            for entry in progress["stages"]:
                if entry.get("key") == stage:
                    if status:
                        entry["status"] = status
                    if message:
                        entry["message"] = message
                    entry["updated_at"] = now
                    break
        if message:
            logs = progress.setdefault("logs", [])
            logs.append({"ts": now, "stage": stage, "message": message})
            if len(logs) > _PIPELINE_PROGRESS_LOG_LIMIT:
                del logs[:-_PIPELINE_PROGRESS_LOG_LIMIT]


def _get_pipeline_progress(run_id: str) -> dict[str, object] | None:
    with _PIPELINE_PROGRESS_LOCK:
        progress = _PIPELINE_PROGRESS.get(run_id)
        if not progress:
            return None
        payload = copy.deepcopy(progress)

    stages = payload.get("stages") if isinstance(payload, dict) else None
    active_stage: dict[str, object] | None = None
    if isinstance(stages, list):
        for stage_entry in stages:
            if isinstance(stage_entry, dict) and stage_entry.get("status") == "running":
                active_stage = stage_entry
                break
        if active_stage is None:
            for stage_entry in stages:
                if isinstance(stage_entry, dict) and stage_entry.get("status") == "pending":
                    active_stage = stage_entry
                    break
        if active_stage is None and stages:
            last_stage = stages[-1]
            if isinstance(last_stage, dict):
                active_stage = last_stage

    logs = payload.get("logs") if isinstance(payload, dict) else None
    last_message: str | None = None
    if isinstance(logs, list) and logs:
        last_log = logs[-1]
        if isinstance(last_log, dict):
            raw_message = last_log.get("message")
            if isinstance(raw_message, str) and raw_message.strip():
                last_message = raw_message.strip()

    state = str(payload.get("state", "unknown")) if isinstance(payload, dict) else "unknown"
    active_stage_label = active_stage.get("label") if isinstance(active_stage, dict) else None
    active_stage_status = active_stage.get("status") if isinstance(active_stage, dict) else None

    status_parts = [f"state={state}"]
    if isinstance(active_stage_label, str) and active_stage_label:
        status_parts.append(f"stage={active_stage_label} ({active_stage_status})")
    if last_message:
        status_parts.append(f"message={last_message}")

    if isinstance(payload, dict):
        payload["active_stage_label"] = active_stage_label if isinstance(active_stage_label, str) else None
        payload["active_stage_status"] = active_stage_status if isinstance(active_stage_status, str) else None
        payload["last_message"] = last_message
        payload["status_line"] = " | ".join(status_parts)
    return payload


def _cache_pipeline_result(
    run_id: str, weights: dict[str, float], perf: dict[str, float]
) -> dict[str, object]:
    entry = {
        "run_id": run_id,
        "weights": copy.deepcopy(weights),
        "perf": copy.deepcopy(perf),
        "cached_at": _utc_now(),
    }
    with _PIPELINE_RESULT_CACHE_LOCK:
        _PIPELINE_RESULT_CACHE[run_id] = entry
        if len(_PIPELINE_RESULT_CACHE) > _PIPELINE_RESULT_CACHE_LIMIT:
            stale_keys = list(_PIPELINE_RESULT_CACHE.keys())[:-_PIPELINE_RESULT_CACHE_LIMIT]
            for key in stale_keys:
                _PIPELINE_RESULT_CACHE.pop(key, None)
    return copy.deepcopy(entry)


def _get_cached_pipeline_result(run_id: str) -> dict[str, object] | None:
    with _PIPELINE_RESULT_CACHE_LOCK:
        cached = _PIPELINE_RESULT_CACHE.get(run_id)
        if not cached:
            return None
        return copy.deepcopy(cached)


def _load_and_cache_run_result(settings: ServiceSettings, run_id: str) -> dict[str, object]:
    run_dir = run_store.resolve_run_dir(settings, run_id)
    weights_rows = run_store.load_weights(run_dir)
    perf_metrics = run_store.load_perf(run_dir)
    weights_map = {
        str(row["asset"]): float(row["weight"])
        for row in weights_rows
        if isinstance(row, dict) and row.get("asset") is not None
    }
    return _cache_pipeline_result(run_id, weights_map, perf_metrics)


def _register_cancel_flag(run_id: str) -> Event:
    flag = Event()
    with _PIPELINE_PROGRESS_LOCK:
        _PIPELINE_CANCEL_FLAGS[run_id] = flag
    return flag


def _pop_cancel_flag(run_id: str) -> Event | None:
    with _PIPELINE_PROGRESS_LOCK:
        return _PIPELINE_CANCEL_FLAGS.pop(run_id, None)


def _cancel_run(run_id: str) -> bool:
    with _PIPELINE_PROGRESS_LOCK:
        flag = _PIPELINE_CANCEL_FLAGS.get(run_id)
    if flag:
        flag.set()
        _update_pipeline_progress(run_id, state="cancelled", message="Run cancelled by user.")
        return True
    return False


async def _run_pipeline_background(
    run_id: str,
    runner_kwargs: dict[str, object],
    cancel_flag: Event,
    *,
    settings: ServiceSettings,
    account_id: uuid.UUID | None,
    api_key_id: uuid.UUID | None,
) -> None:
    logger = logging.getLogger("ai_crypto_index.api")
    try:
        result = await run_in_threadpool(partial(run_monthly_update, **runner_kwargs))
        if isinstance(result, tuple) and len(result) == 2:
            weights, perf = result
            if isinstance(weights, dict) and isinstance(perf, dict):
                _cache_pipeline_result(run_id, weights, perf)
                await _persist_index_run_record(
                    settings,
                    run_id=run_id,
                    source=account_models.IndexRunSource.USER,
                    account_id=account_id,
                    api_key_id=api_key_id,
                )
                if account_id is not None:
                    _tag_user_run_metadata(
                        settings,
                        run_id,
                        account_id=account_id,
                        api_key_id=api_key_id,
                    )
        _update_pipeline_progress(run_id, state="done", message="Pipeline finished")
    except Exception as exc:  # noqa: BLE001
        if cancel_flag.is_set():
            _update_pipeline_progress(run_id, state="cancelled", message="Pipeline cancelled")
        else:
            _update_pipeline_progress(run_id, state="error", message=str(exc))
        logger.exception("Pipeline run %s failed", run_id)
    finally:
        _pop_cancel_flag(run_id)


def _build_request_context(request: Request) -> RequestContext:
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return RequestContext(ip_address=ip_address, user_agent=user_agent)


def _build_confirmation_link(settings: ServiceSettings, token: str) -> str:
    base = settings.auth.public_app_url.rstrip("/")
    return f"{base}/auth/confirm?token={token}"


def _build_reset_link(settings: ServiceSettings, token: str) -> str:
    base = settings.auth.public_app_url.rstrip("/")
    return f"{base}/auth/reset?token={token}"


def _set_refresh_cookie(response: Response, settings: ServiceSettings, token: str) -> None:
    response.set_cookie(
        key=settings.auth.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.auth.session_cookie_secure,
        samesite="lax",
        domain=settings.auth.session_cookie_domain,
        max_age=settings.auth.refresh_token_ttl_seconds,
    )


def _clear_refresh_cookie(response: Response, settings: ServiceSettings) -> None:
    response.delete_cookie(
        key=settings.auth.session_cookie_name,
        domain=settings.auth.session_cookie_domain,
        path="/",
    )


def _build_auth_response(
    *,
    service: AccountService,
    settings: ServiceSettings,
    response: Response,
    result,
) -> models.AuthSessionResponse:
    profile_payload = service.build_profile(result.account)
    profile = models.UserProfile.model_validate(profile_payload)
    expires_in = max(
        1,
        int((result.access_expires_at - datetime.now(timezone.utc)).total_seconds()),
    )
    payload = models.AuthSessionResponse(
        access_token=result.access_token,
        expires_in=expires_in,
        profile=profile,
        debug_refresh_token=result.refresh_token if settings.auth.expose_tokens_in_responses else None,
    )
    _set_refresh_cookie(response, settings, result.refresh_token)
    return payload


def _load_recent_registrations(settings: ServiceSettings, *, limit: int = 20) -> list[dict[str, object]]:
    intake_dir = settings.runs_root / intake_store.INTAKE_DIR_NAME
    registrations_path = intake_dir / intake_store.REGISTRATION_REQUESTS_FILE
    if not registrations_path.exists():
        return []

    try:
        lines = registrations_path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return []

    items: list[dict[str, object]] = []
    for raw_line in reversed(lines[-limit:]):
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        items.append(record)
    return items


def _build_admin_dependency(admin_config: dict[str, str] | None):
    if not admin_config:
        def admin_disabled_dependency() -> str:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin_disabled")

        return admin_disabled_dependency

    admin_basic = HTTPBasic()

    def require_admin(credentials: HTTPBasicCredentials = Depends(admin_basic)) -> str:
        username_ok = secrets.compare_digest(credentials.username or "", admin_config["username"])
        password_ok = secrets.compare_digest(credentials.password or "", admin_config["password"])
        if not (username_ok and password_ok):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="invalid_admin_credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        return credentials.username

    return require_admin


async def _current_account(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
    settings: ServiceSettings = Depends(get_settings),
    account_service: AccountService = Depends(get_account_service),
) -> account_models.Account:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not_authenticated")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.auth.jwt_secret_key,
            algorithms=[settings.auth.jwt_algorithm],
        )
    except JWTError as exc:  # pragma: no cover - signature validation
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token") from exc

    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")

    try:
        account_id = uuid.UUID(str(subject))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token") from exc

    try:
        return await account_service.get_account_profile(session, account_id=account_id)
    except AccountNotFound as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not_authenticated") from exc

configure_logging(os.getenv("AICI_LOG_LEVEL", "INFO"))
logger = logging.getLogger("ai_crypto_index.api")
START_TIME = datetime.now(timezone.utc)
START_TIME_MONOTONIC = time.monotonic()


def _require_api_key_header(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing_api_key")
    return x_api_key


async def _enforce_api_key_rate_limits(request: Request, context: ApiKeyAuthContext) -> None:
    app_state = getattr(request.app, "state", None)
    limiter_second = getattr(app_state, "api_key_second_limiter", None)
    if limiter_second is None:
        limiter_second = SlidingWindowRateLimiter()
        if app_state is not None:
            app_state.api_key_second_limiter = limiter_second
    await limiter_second.hit(str(context.api_key.id), limit=context.limits.burst_per_second, window_seconds=1)

    limiter_minute = getattr(app_state, "api_key_minute_limiter", None)
    if limiter_minute is None:
        limiter_minute = SlidingWindowRateLimiter()
        if app_state is not None:
            app_state.api_key_minute_limiter = limiter_minute
    await limiter_minute.hit(str(context.api_key.id), limit=context.limits.burst_per_minute, window_seconds=60)


async def _authenticate_api_request(
    request: Request,
    session: AsyncSession,
    api_key_service: ApiKeyService,
    raw_key: str,
) -> ApiKeyAuthContext:
    try:
        context = await api_key_service.authenticate(session, raw_key)
    except InvalidApiKeySecret as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_api_key") from exc
    except ApiKeyInactive as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="api_key_inactive") from exc

    account = context.account
    if account.status != account_models.AccountStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="account_inactive")
    if account.email_verified_at is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Email is not verified. Confirm your email to access API endpoints.",
        )

    await _enforce_api_key_rate_limits(request, context)
    _enforce_api_key_restrictions(request, context)
    request.state.api_key_context = context
    request.state.usage_started_at = time.perf_counter()
    return context


def _resolve_request_metadata(request: Request) -> tuple[str, str | None]:
    route = request.scope.get("route")
    if route is not None:
        route_path = getattr(route, "path_format", str(request.url.path))
        route_name = getattr(route, "name", None)
        return str(route_path), route_name
    return str(request.url.path), None


def _compute_usage_duration_ms(request: Request) -> int | None:
    started = getattr(request.state, "usage_started_at", None)
    if not started:
        return None
    elapsed = max(time.perf_counter() - started, 0.0)
    return int(elapsed * 1000)


def _resolve_token_cost(cost: int | None) -> int:
    if cost is None:
        return max(_TOKEN_COST_DEFAULT, _TOKEN_MINIMUM_DEBIT)
    try:
        candidate = int(cost)
    except (TypeError, ValueError):
        candidate = _TOKEN_COST_DEFAULT
    if candidate == 0:
        return 0
    return max(candidate, _TOKEN_MINIMUM_DEBIT)


def _has_custom_run_id(request: Request) -> bool:
    raw_value = request.query_params.get("run_id")
    if raw_value is None:
        return False
    source = str(request.query_params.get("run_id_source", "")).strip().lower()
    if source in {"auto", "ui"}:
        return False
    return bool(str(raw_value).strip())


def _calculate_pipeline_token_cost(
    run_payload: models.RunRequest,
    *,
    custom_run_id: bool = False,
) -> int:
    base_cost = _TOKEN_COST_PIPELINE_TRIGGER
    total_cost = float(base_cost)
    total_cost += 0.5 * run_payload.n_top_coins
    per_asset = 10 if run_payload.advanced_forecast else 4
    total_cost += per_asset * run_payload.total_assets
    if custom_run_id:
        total_cost += 5
    if run_payload.visualization:
        total_cost += 5
    history_days = run_payload.lookback_days
    if run_payload.start_date:
        try:
            parsed = datetime.strptime(run_payload.start_date, "%Y-%m-%d").date()
        except ValueError:
            parsed = None
        if parsed is not None:
            history_days = max((date.today() - parsed).days, 0)
    history_surcharge = max(0, ceil((history_days - 365) / 90))
    total_cost += history_surcharge
    return int(ceil(total_cost))


def _apply_free_plan_run_limits(
    run_payload: models.RunRequest,
    plan: ApiKeyPlanSettings | None,
) -> models.RunRequest:
    if plan is None or plan.code != FREE_PLAN_CODE:
        return run_payload

    updates: dict[str, int] = {}
    fallback_to_default = False
    if run_payload.n_top_coins > FREE_PLAN_MAX_N_TOP_COINS:
        updates["n_top_coins"] = RUN_REQUEST_DEFAULTS.n_top_coins
        fallback_to_default = True
    if run_payload.total_assets > FREE_PLAN_MAX_TOTAL_ASSETS:
        updates["total_assets"] = RUN_REQUEST_DEFAULTS.total_assets
    if fallback_to_default and "total_assets" not in updates:
        updates["total_assets"] = RUN_REQUEST_DEFAULTS.total_assets

    if not updates:
        return run_payload

    sanitized = models.RunRequest.model_validate({**run_payload.model_dump(), **updates})
    logger.info(
        "Free plan run parameters exceeded limits; applied defaults",
        extra={
            "requested_n_top_coins": run_payload.n_top_coins,
            "requested_total_assets": run_payload.total_assets,
            "effective_n_top_coins": sanitized.n_top_coins,
            "effective_total_assets": sanitized.total_assets,
        },
    )
    return sanitized


def _normalize_usage_date(value: object) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            try:
                return datetime.fromisoformat(value).date()
            except ValueError:
                return date.today()
    return date.today()


async def _persist_usage_event(
    session: AsyncSession,
    context: ApiKeyAuthContext,
    request: Request,
    *,
    status_code: int,
    cost: int,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> None:
    route_path, route_name = _resolve_request_metadata(request)
    duration_ms = _compute_usage_duration_ms(request)
    request_id = request.headers.get("x-request-id") or getattr(request.state, "request_id", None)
    event = account_models.ApiUsageEvent(
        account_id=context.account.id,
        api_key_id=context.api_key.id,
        plan_code=context.plan.code,
        route_path=route_path[:160],
        route_name=(route_name or route_path)[:120],
        method=request.method[:16],
        status_code=status_code,
        request_cost=cost,
        duration_ms=duration_ms,
        error_code=error_code[:120] if error_code else None,
        error_detail=error_detail[:2000] if error_detail else None,
        request_id=request_id[:80] if request_id else None,
    )
    session.add(event)
    try:
        await session.commit()
    except Exception:
        logger.exception("usage_event_log_failed", extra={"route": route_path, "status_code": status_code})
        await session.rollback()


async def _record_api_usage(
    request: Request,
    response: Response | None,
    session: AsyncSession,
    api_key_service: ApiKeyService,
    context: ApiKeyAuthContext,
    *,
    cost: int | None = None,
    success: bool = True,
    status_code: int | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> None:
    resolved_cost = _resolve_token_cost(cost)
    recorded_cost = resolved_cost if success else 0
    response_status = response.status_code if response is not None else None
    resolved_status = status_code or response_status or status.HTTP_200_OK
    if response is not None:
        response.headers.setdefault("X-API-Request-Tokens", str(resolved_cost))
    if success:
        try:
            snapshot = await api_key_service.record_usage(
                session,
                context,
                cost=resolved_cost,
                route_name=request.url.path,
            )
        except ApiKeyQuotaExceeded as exc:
            quota_status = status.HTTP_429_TOO_MANY_REQUESTS
            await _persist_usage_event(
                session,
                context,
                request,
                status_code=quota_status,
                cost=0,
                error_code=f"{exc.scope}_quota_exceeded",
                error_detail="usage_quota_reached",
            )
            raise HTTPException(quota_status, detail=f"{exc.scope}_quota_exceeded") from exc

        if response is not None:
            response.headers.setdefault("X-API-Key-ID", str(context.api_key.id))
            response.headers.setdefault("X-API-Plan", context.plan.code)
            response.headers.setdefault("X-API-Latency-Seconds", str(context.limits.data_latency_seconds))
            response.headers.setdefault(
                "X-API-Quota-Daily",
                str(context.limits.daily_quota if context.limits.daily_quota is not None else -1),
            )
            response.headers.setdefault(
                "X-API-Quota-Monthly",
                str(context.limits.monthly_quota if context.limits.monthly_quota is not None else -1),
            )
            response.headers.setdefault("X-API-Usage-Daily", str(snapshot.daily_calls))
            response.headers.setdefault("X-API-Usage-Monthly", str(snapshot.monthly_calls))

    await _persist_usage_event(
        session,
        context,
        request,
        status_code=resolved_status,
        cost=recorded_cost,
        error_code=error_code,
        error_detail=error_detail,
    )


async def _record_usage_failure(
    request: Request,
    response: Response | None,
    session: AsyncSession,
    api_key_service: ApiKeyService,
    context: ApiKeyAuthContext,
    exc: Exception,
    *,
    cost: int | None = None,
) -> NoReturn:
    if isinstance(exc, HTTPException):
        await _record_api_usage(
            request,
            response,
            session,
            api_key_service,
            context,
            success=False,
            status_code=exc.status_code,
            error_code=str(exc.detail),
            error_detail=str(exc.detail),
            cost=cost,
        )
        raise exc
    await _record_api_usage(
        request,
        response,
        session,
        api_key_service,
        context,
        success=False,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code="internal_error",
        error_detail=str(exc),
        cost=cost,
    )
    raise exc


def _resolve_latency_cutoff(context: ApiKeyAuthContext) -> float | None:
    latency = max(context.limits.data_latency_seconds, 0)
    if latency <= 0:
        return None
    return time.time() - latency


def _ensure_run_latency_allowed(run_dir: Path, context: ApiKeyAuthContext) -> None:
    cutoff = _resolve_latency_cutoff(context)
    if cutoff is None:
        return
    if run_dir.stat().st_mtime > cutoff:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="data_locked_for_plan")


def _read_attribute_list(source: dict[str, object] | None, key: str) -> list[str]:
    if not isinstance(source, dict):
        return []
    raw_value = source.get(key)
    if not isinstance(raw_value, list):
        return []
    cleaned: list[str] = []
    for entry in raw_value:
        candidate = str(entry or "").strip()
        if candidate:
            cleaned.append(candidate)
    return cleaned


def _extract_request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = request.client
    return client.host if client else None


def _ip_in_allowlist(client_ip: str, allowlist: list[str]) -> bool:
    try:
        ip_obj = ip_address(client_ip)
    except ValueError:
        return False
    for item in allowlist:
        try:
            network = ip_network(item, strict=False)
        except ValueError:
            continue
        if ip_obj.version != network.version:
            continue
        if ip_obj in network:
            return True
    return False


def _enforce_api_key_restrictions(request: Request, context: ApiKeyAuthContext) -> None:
    attributes = context.api_key.attributes if isinstance(context.api_key.attributes, dict) else {}
    ip_rules = _read_attribute_list(attributes, "ip_allowlist")
    label_rules = [value.lower() for value in _read_attribute_list(attributes, "label_constraints")]
    if ip_rules:
        client_ip = _extract_request_ip(request)
        if not client_ip or not _ip_in_allowlist(client_ip, ip_rules):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="ip_not_allowed")
    if label_rules:
        label_value = request.headers.get("x-aici-label") or request.headers.get("x-aici-client-label")
        if not label_value or label_value.strip().lower() not in label_rules:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="label_not_allowed")


def _build_api_key_payload(
    api_key: account_models.ApiKey,
    *,
    plan: ApiKeyPlanSettings,
    limits: "ApiKeyLimits",
    usage: "ApiKeyUsageSnapshot",
    include_owner: bool = False,
) -> dict[str, object]:
    def pct(consumed: int, limit: int | None) -> float | None:
        if not limit:
            return None
        return round(min(consumed / limit, 1.0), 4)

    attributes = api_key.attributes if isinstance(api_key.attributes, dict) else {}
    ip_allowlist = _read_attribute_list(attributes, "ip_allowlist")
    label_constraints = _read_attribute_list(attributes, "label_constraints")

    payload: dict[str, object] = {
        "id": str(api_key.id),
        "label": api_key.label,
        "application_name": api_key.application_name,
        "tags": list(api_key.tags or []),
        "role": api_key.role.value,
        "status": api_key.status.value,
        "plan_code": plan.code,
        "token_preview": f"{api_key.token_prefix}˘?ł{api_key.token_suffix}",
        "created_at": api_key.created_at,
        "last_used_at": api_key.last_used_at,
        "expires_at": api_key.expires_at,
        "revoked_at": api_key.revoked_at,
        "ip_allowlist": ip_allowlist,
        "label_constraints": label_constraints,
        "usage": {
            "unit": "tokens",
            "daily_calls": usage.daily_calls,
            "daily_quota": limits.daily_quota,
            "daily_pct": pct(usage.daily_calls, limits.daily_quota),
            "monthly_calls": usage.monthly_calls,
            "monthly_quota": limits.monthly_quota,
            "monthly_pct": pct(usage.monthly_calls, limits.monthly_quota),
            "burst_per_second": limits.burst_per_second,
            "burst_per_minute": limits.burst_per_minute,
            "data_latency_seconds": limits.data_latency_seconds,
        },
    }
    if include_owner and api_key.account:
        payload.update(
            {
                "account_id": str(api_key.account.id),
                "account_email": api_key.account.email,
                "account_status": api_key.account.status.value,
            }
        )
    return payload


def _parse_uuid_or_400(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"invalid_{label}") from exc


class IndexCompositionError(RuntimeError):
    """Signal issues while preparing the index composition block."""


def _build_index_composition(
    settings: ServiceSettings,
    *,
    run_dir: Path | None = None,
    preferred_strategy_key: str | None = "classic",
) -> dict[str, object]:
    if run_dir is None:
        run_dir = _find_latest_index_run(settings)
    if run_dir is None:
        raise IndexCompositionError("No completed auto index runs found.")

    weights_rows = run_store.load_weights(run_dir)
    if not weights_rows:
        raise IndexCompositionError("Latest auto run has no weights recorded.")

    sorted_rows = sorted(weights_rows, key=lambda row: row.get("weight", 0.0), reverse=True)
    total_weight = fsum(row.get("weight", 0.0) for row in sorted_rows)
    if total_weight <= 0:
        raise IndexCompositionError("Latest auto-run weights sum to a non-positive value.")

    items: list[dict[str, object]] = []
    cumulative_pct = 0.0
    weight_pcts: list[float] = []
    count = len(sorted_rows)
    mean_weight = total_weight / count if count else 0.0

    for index, row in enumerate(sorted_rows, start=1):
        weight = float(row.get("weight", 0.0))
        asset = str(row.get("asset", "")).strip()
        weight_pct = (weight / total_weight) * 100
        cumulative_pct += weight_pct
        relative_to_mean = weight / mean_weight if mean_weight else 0.0

        items.append(
            {
                "rank": index,
                "asset": asset or "Unknown",
                "weight_pct": weight_pct,
                "weight": weight,
                "cumulative_pct": cumulative_pct,
                "relative_to_mean": relative_to_mean,
            }
        )
        weight_pcts.append(weight_pct)

    top3_pct = fsum(weight_pcts[:3]) if weight_pcts else 0.0
    herfindahl = fsum((pct / 100) ** 2 for pct in weight_pcts)
    effective_assets = (1 / herfindahl) if herfindahl > 0 else None
    updated_at = datetime.utcfromtimestamp(run_dir.stat().st_mtime)

    summary = {
        "count": count,
        "top3_pct": top3_pct,
        "herfindahl": herfindahl,
        "effective_assets": effective_assets,
        "max_weight_pct": max(weight_pcts) if weight_pcts else 0.0,
        "min_weight_pct": min(weight_pcts) if weight_pcts else 0.0,
        "mean_weight_pct": fsum(weight_pcts) / count if count else 0.0,
        "total_weight_pct": fsum(weight_pcts),
    }

    live_backtest_payload: dict[str, object] | None = None
    live_backtest_payloads_by_strategy: dict[str, dict[str, object] | None] = {}
    monthly_snapshots_payloads_by_strategy: dict[str, dict[str, object] | None] = {}
    monthly_snapshots_payload: dict[str, object] | None = None
    monthly_snapshots_selected_strategy = _normalize_ui_strategy_key(preferred_strategy_key) or "classic"
    try:
        live_backtest_payloads_by_strategy = _build_live_backtest_payloads_by_strategy(settings)
        live_backtest_payload = _select_live_backtest_payload_for_strategy(
            live_backtest_payloads_by_strategy,
            strategy_key=monthly_snapshots_selected_strategy,
        )
        monthly_snapshots_payloads_by_strategy = _build_monthly_snapshots_payloads_by_strategy(
            settings,
            live_backtest_payloads_by_strategy=live_backtest_payloads_by_strategy,
        )
        (
            resolved_monthly_strategy,
            monthly_snapshots_payload,
        ) = _select_monthly_snapshots_payload_for_strategy(
            monthly_snapshots_payloads_by_strategy,
            strategy_key=monthly_snapshots_selected_strategy,
        )
        if resolved_monthly_strategy:
            monthly_snapshots_selected_strategy = resolved_monthly_strategy
    except Exception as exc:  # noqa: BLE001 - keep composition endpoint resilient
        logger.warning("monthly_composition_context_unavailable: %s", exc)

    monthly_snapshots_by_strategy: dict[str, list[dict[str, object]]] = {}
    monthly_live_snapshots_by_strategy: dict[str, list[dict[str, object]]] = {}
    monthly_backtest_snapshots_by_strategy: dict[str, list[dict[str, object]]] = {}
    monthly_snapshots_updated_at_by_strategy: dict[str, str | None] = {}
    monthly_snapshots_current_month_by_strategy: dict[str, str | None] = {}
    for strategy_key, strategy_payload in monthly_snapshots_payloads_by_strategy.items():
        if not isinstance(strategy_payload, dict):
            continue
        monthly_snapshots_by_strategy[strategy_key] = (
            strategy_payload.get("snapshots", [])
            if isinstance(strategy_payload.get("snapshots"), list)
            else []
        )
        monthly_live_snapshots_by_strategy[strategy_key] = (
            strategy_payload.get("live_snapshots", [])
            if isinstance(strategy_payload.get("live_snapshots"), list)
            else []
        )
        monthly_backtest_snapshots_by_strategy[strategy_key] = (
            strategy_payload.get("backtest_snapshots", [])
            if isinstance(strategy_payload.get("backtest_snapshots"), list)
            else []
        )
        monthly_snapshots_updated_at_by_strategy[strategy_key] = (
            str(strategy_payload.get("updated_at"))
            if strategy_payload.get("updated_at") is not None
            else None
        )
        monthly_snapshots_current_month_by_strategy[strategy_key] = (
            str(strategy_payload.get("current_month"))
            if strategy_payload.get("current_month") is not None
            else None
        )

    payload = {
        "run_id": run_dir.name,
        "updated_display": updated_at.strftime("%d %b %Y, %H:%M UTC"),
        "updated_iso": updated_at.replace(microsecond=0).isoformat() + "Z",
        "assets": items,
        "summary": summary,
        "downloads": {
            "csv": f"{API_BASE_PATH}/runs/{run_dir.name}/export?fmt=csv",
            "zip": f"{API_BASE_PATH}/runs/{run_dir.name}/export?fmt=zip",
        },
        "live_backtest": live_backtest_payload,
        "live_backtest_by_strategy": live_backtest_payloads_by_strategy,
        "monthly_snapshots": (
            monthly_snapshots_payload.get("snapshots", [])
            if isinstance(monthly_snapshots_payload, dict)
            else []
        ),
        "monthly_live_snapshots": (
            monthly_snapshots_payload.get("live_snapshots", [])
            if isinstance(monthly_snapshots_payload, dict)
            else []
        ),
        "monthly_backtest_snapshots": (
            monthly_snapshots_payload.get("backtest_snapshots", [])
            if isinstance(monthly_snapshots_payload, dict)
            else []
        ),
        "monthly_snapshots_updated_at": (
            monthly_snapshots_payload.get("updated_at")
            if isinstance(monthly_snapshots_payload, dict)
            else None
        ),
        "monthly_snapshots_current_month": (
            monthly_snapshots_payload.get("current_month")
            if isinstance(monthly_snapshots_payload, dict)
            else None
        ),
        "monthly_snapshots_default_strategy": monthly_snapshots_selected_strategy,
        "monthly_snapshots_by_strategy": monthly_snapshots_by_strategy,
        "monthly_live_snapshots_by_strategy": monthly_live_snapshots_by_strategy,
        "monthly_backtest_snapshots_by_strategy": monthly_backtest_snapshots_by_strategy,
        "monthly_snapshots_updated_at_by_strategy": monthly_snapshots_updated_at_by_strategy,
        "monthly_snapshots_current_month_by_strategy": monthly_snapshots_current_month_by_strategy,
    }
    return payload


def _resolve_allowed_origins() -> list[str]:
    raw = os.getenv("AICI_ALLOWED_ORIGINS")
    if not raw:
        return ["*"]
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or ["*"]


def _resolve_rate_limit() -> tuple[int, int]:
    limit_raw = os.getenv("AICI_RATE_LIMIT", "120")
    window_raw = os.getenv("AICI_RATE_LIMIT_WINDOW", "60")
    try:
        limit = max(1, int(limit_raw))
    except (TypeError, ValueError):
        limit = 120
    try:
        window = max(1, int(window_raw))
    except (TypeError, ValueError):
        window = 60
    return limit, window


def _resolve_signup_rate_limit() -> tuple[int, int]:
    limit_raw = os.getenv("AICI_SIGNUP_RATE_LIMIT", "5")
    window_raw = os.getenv("AICI_SIGNUP_RATE_WINDOW", "60")
    try:
        limit = max(0, int(limit_raw))
    except (TypeError, ValueError):
        limit = 5
    try:
        window = max(1, int(window_raw))
    except (TypeError, ValueError):
        window = 60
    return limit, window


def _resolve_resend_rate_limit() -> tuple[int, int]:
    limit_raw = os.getenv("AICI_RESEND_RATE_LIMIT", "5")
    window_raw = os.getenv("AICI_RESEND_RATE_WINDOW", "60")
    try:
        limit = max(0, int(limit_raw))
    except (TypeError, ValueError):
        limit = 5
    try:
        window = max(1, int(window_raw))
    except (TypeError, ValueError):
        window = 60
    return limit, window


SIGNUP_RATE_LIMIT, SIGNUP_RATE_WINDOW = _resolve_signup_rate_limit()
RESEND_RATE_LIMIT, RESEND_RATE_WINDOW = _resolve_resend_rate_limit()


@dataclass(frozen=True)
class SwaggerConfig:
    enabled: bool
    docs_url: str
    openapi_url: str
    username: str | None = None
    password: str | None = None


def _normalize_subpath(raw_value: str | None, *, default: str) -> str:
    candidate = (raw_value or default).strip() or default
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    return candidate


def _resolve_swagger_config() -> SwaggerConfig:
    enabled = str(os.getenv("AICI_SWAGGER_ENABLED", "")).lower() in _PIPELINE_ENABLED_FLAGS
    docs_url = _normalize_subpath(os.getenv("AICI_SWAGGER_DOCS_URL"), default="/docs")
    openapi_url = _normalize_subpath(
        os.getenv("AICI_SWAGGER_OPENAPI_URL"),
        default="/openapi.json",
    )

    if not enabled:
        return SwaggerConfig(enabled=False, docs_url=docs_url, openapi_url=openapi_url)

    username = os.getenv("AICI_SWAGGER_USERNAME")
    password = os.getenv("AICI_SWAGGER_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "Swagger basic auth is enabled but credentials are missing. "
            "Set both AICI_SWAGGER_USERNAME and AICI_SWAGGER_PASSWORD.",
        )

    return SwaggerConfig(
        enabled=True,
        docs_url=docs_url,
        openapi_url=openapi_url,
        username=username,
        password=password,
    )


def _resolve_admin_config() -> dict[str, str] | None:
    enabled_flag = os.getenv("AICI_ADMIN_ENABLED", "0").lower() in _PIPELINE_ENABLED_FLAGS
    username = os.getenv("AICI_ADMIN_USERNAME")
    password = os.getenv("AICI_ADMIN_PASSWORD")
    if not enabled_flag or not username or not password:
        return None
    return {"username": username, "password": password}


def _install_swagger_endpoints(api_app: FastAPI, config: SwaggerConfig) -> None:
    if not config.enabled:
        api_app.state.swagger_enabled = False
        api_app.state.swagger_docs_url = None
        api_app.state.swagger_openapi_url = None
        return

    security = HTTPBasic(auto_error=False)
    api_app.state.swagger_enabled = True
    api_app.state.swagger_docs_url = config.docs_url
    api_app.state.swagger_openapi_url = config.openapi_url
    logger.info(
        "swagger_docs_enabled",
        extra={"docs_url": config.docs_url, "openapi_url": config.openapi_url},
    )

    def _authenticate(credentials: HTTPBasicCredentials | None) -> None:
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="credentials_required",
                headers={"WWW-Authenticate": "Basic"},
            )

        username_match = secrets.compare_digest(credentials.username or "", config.username or "")
        password_match = secrets.compare_digest(credentials.password or "", config.password or "")
        if not (username_match and password_match):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

    @api_app.get(config.docs_url, include_in_schema=False)
    async def swagger_ui(
        request: Request,
        credentials: HTTPBasicCredentials = Depends(security),
    ) -> HTMLResponse:
        _authenticate(credentials)
        openapi_url = request.url_for("openapi_schema")
        return get_swagger_ui_html(
            openapi_url=openapi_url,
            title=f"{api_app.title or 'API'} - Swagger UI",
            swagger_ui_parameters={"persistAuthorization": True},
        )

    @api_app.get(config.openapi_url, include_in_schema=False)
    async def openapi_schema(credentials: HTTPBasicCredentials = Depends(security)) -> JSONResponse:
        _authenticate(credentials)
        return JSONResponse(api_app.openapi())


def _prepare_performance_snapshots(
    bundle: PerformanceBundle,
) -> dict[str, models.PerformanceSnapshotModel]:
    snapshots: dict[str, models.PerformanceSnapshotModel] = {}
    for key, snapshot in bundle.snapshots.items():
        serialized = to_builtin(snapshot)
        snapshots[key] = models.PerformanceSnapshotModel.model_validate(serialized)
    return snapshots


def _build_live_backtest_payloads_by_strategy(
    settings: ServiceSettings,
) -> dict[str, dict[str, object] | None]:
    payloads: dict[str, dict[str, object] | None] = {}
    for profile in _index_auto_profiles():
        ui_strategy_key = (profile.strategy_key or "").strip().lower()
        if not ui_strategy_key:
            continue
        backtest_strategy_key = resolve_live_backtest_strategy_key(ui_strategy_key)
        try:
            strategy_payload = build_live_backtest_payload(
                settings,
                strategy_key=backtest_strategy_key,
                live_run_prefix=profile.run_prefix,
            ).to_dict()
            payloads[ui_strategy_key] = strategy_payload
            if ui_strategy_key == "aggressive":
                payloads["risky"] = strategy_payload
        except Exception as exc:  # noqa: BLE001 - keep partial payloads available
            logger.warning(
                "live_backtest_payload_unavailable_for_strategy strategy=%s run_prefix=%s: %s",
                ui_strategy_key,
                profile.run_prefix,
                exc,
            )
            payloads[ui_strategy_key] = None
            if ui_strategy_key == "aggressive":
                payloads["risky"] = None
    return payloads


def _select_live_backtest_payload_for_strategy(
    payloads_by_strategy: dict[str, dict[str, object] | None],
    *,
    strategy_key: str | None,
) -> dict[str, object] | None:
    preferred = (strategy_key or "").strip().lower()
    if preferred and isinstance(payloads_by_strategy.get(preferred), dict):
        return payloads_by_strategy[preferred]

    if preferred == "risky" and isinstance(payloads_by_strategy.get("aggressive"), dict):
        return payloads_by_strategy["aggressive"]

    for fallback_key in ("classic", "conservative", "aggressive"):
        candidate = payloads_by_strategy.get(fallback_key)
        if isinstance(candidate, dict):
            return candidate

    for candidate in payloads_by_strategy.values():
        if isinstance(candidate, dict):
            return candidate
    return None


def _normalize_ui_strategy_key(strategy_key: str | None) -> str:
    normalized = (strategy_key or "").strip().lower()
    if normalized == "risky":
        return "aggressive"
    return normalized


def _select_live_backtest_payload_for_exact_strategy(
    payloads_by_strategy: dict[str, dict[str, object] | None],
    *,
    strategy_key: str | None,
) -> dict[str, object] | None:
    normalized = _normalize_ui_strategy_key(strategy_key)
    if not normalized:
        return None

    candidate = payloads_by_strategy.get(normalized)
    if isinstance(candidate, dict):
        return candidate

    if normalized == "aggressive":
        risky_candidate = payloads_by_strategy.get("risky")
        if isinstance(risky_candidate, dict):
            return risky_candidate
    return None


def _build_monthly_snapshots_payloads_by_strategy(
    settings: ServiceSettings,
    *,
    live_backtest_payloads_by_strategy: dict[str, dict[str, object] | None],
) -> dict[str, dict[str, object] | None]:
    payloads: dict[str, dict[str, object] | None] = {}
    for profile in _index_auto_profiles():
        ui_strategy_key = _normalize_ui_strategy_key(profile.strategy_key)
        if not ui_strategy_key:
            continue
        live_backtest_payload = _select_live_backtest_payload_for_exact_strategy(
            live_backtest_payloads_by_strategy,
            strategy_key=ui_strategy_key,
        )
        live_start_date = (
            str(live_backtest_payload.get("live_start_date"))
            if isinstance(live_backtest_payload, dict)
            and isinstance(live_backtest_payload.get("live_start_date"), str)
            else None
        )
        try:
            store = refresh_monthly_snapshots_store(
                settings,
                live_start_date=live_start_date,
                run_prefix=profile.run_prefix,
                persist=False,
            )
            payload = store.to_dict()
            payloads[ui_strategy_key] = payload
            if ui_strategy_key == "aggressive":
                payloads["risky"] = payload
        except Exception as exc:  # noqa: BLE001 - keep partial payloads available
            logger.warning(
                "monthly_snapshots_payload_unavailable_for_strategy strategy=%s run_prefix=%s: %s",
                ui_strategy_key,
                profile.run_prefix,
                exc,
            )
            payloads[ui_strategy_key] = None
            if ui_strategy_key == "aggressive":
                payloads["risky"] = None
    return payloads


def _select_monthly_snapshots_payload_for_strategy(
    payloads_by_strategy: dict[str, dict[str, object] | None],
    *,
    strategy_key: str | None,
) -> tuple[str | None, dict[str, object] | None]:
    preferred = _normalize_ui_strategy_key(strategy_key)
    if preferred and isinstance(payloads_by_strategy.get(preferred), dict):
        return preferred, payloads_by_strategy[preferred]

    for fallback_key in ("classic", "conservative", "aggressive"):
        candidate = payloads_by_strategy.get(fallback_key)
        if isinstance(candidate, dict):
            return fallback_key, candidate

    for key, candidate in payloads_by_strategy.items():
        if isinstance(candidate, dict):
            return key, candidate
    return None, None


def _serialize_dt(value: object | None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _runtime_status_payload() -> dict[str, object]:
    payload = dict(_PERFORMANCE_STATUS)
    payload["started_at"] = _serialize_dt(payload.get("started_at"))
    payload["last_run_at"] = _serialize_dt(payload.get("last_run_at"))
    payload["is_running"] = _PERFORMANCE_RUN_LOCK.locked()
    return payload


def _load_auto_config_with_latest(settings: ServiceSettings) -> tuple[AutoRunConfig, list[dict[str, object]], list[dict[str, object]]]:
    snapshots = collect_variant_snapshots(settings)
    benchmark_snapshots = collect_benchmark_snapshots(settings)
    latest_existing = latest_snapshot_date(snapshots + benchmark_snapshots)
    config = load_auto_config(settings, latest_date=latest_existing)
    if config.next_run_date is None:
        config.next_run_date = date.today()
        persist_auto_config(settings, config)
    return config, snapshots, benchmark_snapshots


def _build_performance_status_payload(
    config: AutoRunConfig,
    snapshots: list[dict[str, object]],
    benchmarks: list[dict[str, object]] | None = None,
    report=None,
) -> dict[str, object]:
    report_payload = report.to_dict() if report else (config.last_summary if config.last_summary else None)
    return {
        "config": config.to_dict(),
        "snapshots": snapshots,
        "benchmarks": benchmarks or [],
        "runtime": _runtime_status_payload(),
        "report": report_payload,
    }


async def _trigger_performance_refresh(
    settings: ServiceSettings,
    *,
    reason: str,
    config: AutoRunConfig | None = None,
    snapshots: list[dict[str, object]] | None = None,
    benchmark_snapshots: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if _PERFORMANCE_RUN_LOCK.locked():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="performance_refresh_running")

    snapshots = snapshots or collect_variant_snapshots(settings)
    benchmark_snapshots = benchmark_snapshots or collect_benchmark_snapshots(settings)
    latest_existing = latest_snapshot_date(snapshots + benchmark_snapshots)
    config = config or load_auto_config(settings, latest_date=latest_existing)

    async with _PERFORMANCE_RUN_LOCK:
        try:
            with hold_monthly_job_lock(
                settings.runs_root,
                contour=_PERFORMANCE_AUTO_LOCK_CONTOUR,
                target_month=date.today(),
                stale_after_seconds=_MONTHLY_JOB_LOCK_STALE_SECONDS,
            ):
                _PERFORMANCE_STATUS.update(
                    {
                        "state": "running",
                        "reason": reason,
                        "started_at": datetime.utcnow(),
                    }
                )
                report = await run_in_threadpool(partial(refresh_performance_data, settings=settings))
                config = update_next_run_after_success(settings, config, report)
                _PERFORMANCE_STATUS.update(
                    {
                        "state": "idle",
                        "reason": None,
                        "started_at": None,
                        "last_run_at": config.last_run_at,
                        "last_status": config.last_run_status,
                        "last_error": None,
                    }
                )
                refreshed_snapshots = collect_variant_snapshots(settings)
                refreshed_benchmarks = collect_benchmark_snapshots(settings)
                return _build_performance_status_payload(config, refreshed_snapshots, refreshed_benchmarks, report=report)
        except MonthlyJobLockBusyError:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="performance_refresh_running")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            config = update_next_run_after_failure(settings, config, str(exc))
            _PERFORMANCE_STATUS.update(
                {
                    "state": "idle",
                    "reason": None,
                    "started_at": None,
                    "last_run_at": config.last_run_at,
                    "last_status": "error",
                    "last_error": str(exc),
                }
            )
            raise


async def _maybe_run_performance_auto(settings: ServiceSettings) -> AutoRunConfig:
    config, snapshots, benchmarks = _load_auto_config_with_latest(settings)
    if not _PERFORMANCE_AUTO_ENABLED:
        return config
    if not config.enabled or _PERFORMANCE_RUN_LOCK.locked():
        return config

    due_date = config.next_run_date or date.today()
    if date.today() < due_date:
        return config

    try:
        await _trigger_performance_refresh(
            settings,
            reason="auto",
            config=config,
            snapshots=snapshots,
            benchmark_snapshots=benchmarks,
        )
    except HTTPException as exc:
        logger.info("Automatic performance refresh skipped: %s", exc.detail)
    except Exception:
        logger.exception("Automatic performance refresh failed")
    return config


async def _performance_auto_loop(settings: ServiceSettings) -> None:
    while True:
        try:
            if not _PERFORMANCE_AUTO_ENABLED:
                return
            await _maybe_run_performance_auto(settings)
            await asyncio.sleep(_PERFORMANCE_POLL_SECONDS)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Performance auto loop error")
            await asyncio.sleep(_PERFORMANCE_POLL_SECONDS)


async def _start_performance_scheduler(settings: ServiceSettings) -> None:
    global _PERFORMANCE_AUTO_TASK
    if not _PERFORMANCE_AUTO_ENABLED:
        return
    if _PERFORMANCE_AUTO_TASK is None or _PERFORMANCE_AUTO_TASK.done():
        _PERFORMANCE_AUTO_TASK = asyncio.create_task(_performance_auto_loop(settings))


async def _stop_performance_scheduler() -> None:
    global _PERFORMANCE_AUTO_TASK
    if _PERFORMANCE_AUTO_TASK is None:
        return
    task = _PERFORMANCE_AUTO_TASK
    if task.done():
        _PERFORMANCE_AUTO_TASK = None
        return
    task.cancel()
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    task_loop = task.get_loop() if hasattr(task, "get_loop") else getattr(task, "_loop", None)
    if running_loop is not None and task_loop is running_loop:
        with suppress(asyncio.CancelledError):
            await task
    _PERFORMANCE_AUTO_TASK = None


def _daily_snapshot_schedule_at(target_date: date) -> datetime:
    return datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        _DAILY_SNAPSHOT_HOUR_UTC,
        _DAILY_SNAPSHOT_MINUTE_UTC,
        tzinfo=timezone.utc,
    )


def _next_daily_snapshot_run(now: datetime) -> datetime:
    scheduled = _daily_snapshot_schedule_at(now.date())
    if now < scheduled:
        return scheduled
    return scheduled + timedelta(days=1)


def _update_daily_snapshot_status(
    meta: daily_snapshot.DailySnapshotMeta | None,
    *,
    status: str,
    error: str | None = None,
    run_at: datetime | None = None,
    update_run_at: bool = True,
) -> None:
    last_run_at = _DAILY_SNAPSHOT_STATUS.get("last_run_at")
    if update_run_at:
        last_run_at = run_at or _utc_now()
    _DAILY_SNAPSHOT_STATUS.update(
        {
            "state": "idle",
            "started_at": None,
            "last_run_at": last_run_at,
            "last_status": status,
            "last_error": error,
            "last_snapshot_date": meta.snapshot_date.isoformat() if meta else None,
            "last_source_date": meta.source_date.isoformat() if meta else None,
            "last_storage_uri": meta.storage_uri if meta else None,
            "stale": meta.stale if meta else None,
        }
    )


def _maybe_alert_daily_snapshot(
    target_date: date,
    *,
    meta: daily_snapshot.DailySnapshotMeta | None,
    error: str | None,
) -> None:
    last_alert = _DAILY_SNAPSHOT_STATUS.get("last_alert_date")
    if last_alert == target_date:
        return
    if error:
        subject = f"[AICI] Daily snapshot error ({target_date.isoformat()})"
    elif meta and meta.stale:
        subject = f"[AICI] Daily snapshot stale ({target_date.isoformat()})"
    else:
        return
    lines = [
        "Daily snapshot alert.",
        f"Target date: {target_date.isoformat()}",
        f"Snapshot date: {getattr(meta, 'snapshot_date', None)}",
        f"Source date: {getattr(meta, 'source_date', None)}",
        f"Stale: {getattr(meta, 'stale', None)}",
        f"Storage URI: {getattr(meta, 'storage_uri', None)}",
        f"Local path: {getattr(meta, 'local_path', None)}",
        f"Error: {error or getattr(meta, 'error', None)}",
    ]
    email_notifications.send_daily_snapshot_alert(subject=subject, body_lines=lines)
    _DAILY_SNAPSHOT_STATUS["last_alert_date"] = target_date


async def _maybe_run_daily_snapshot(settings: ServiceSettings) -> daily_snapshot.DailySnapshotMeta | None:
    if not _DAILY_SNAPSHOT_ENABLED:
        return None
    now = _utc_now()
    target_date = now.date()
    scheduled_at = _daily_snapshot_schedule_at(target_date)
    if now < scheduled_at:
        return None
    if _DAILY_SNAPSHOT_LOCK.locked():
        return None

    async with _DAILY_SNAPSHOT_LOCK:
        _DAILY_SNAPSHOT_STATUS.update({"state": "running", "started_at": now, "last_error": None})
        base_uri = daily_snapshot.resolve_base_uri(settings)
        snapshot_root = daily_snapshot.resolve_snapshot_root(settings)
        try:
            await run_in_threadpool(
                partial(
                    daily_snapshot.maybe_prune_custom_snapshots_monthly,
                    snapshot_root,
                    base_uri=base_uri,
                    now=now,
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("Custom snapshot monthly cleanup failed")
        meta = daily_snapshot.load_latest_snapshot_meta(
            snapshot_root,
            target_date=target_date,
            n_top_coins=daily_snapshot.DEFAULT_N_TOP,
            base_uri=base_uri,
        )
        if meta and meta.snapshot_date == target_date and not meta.stale:
            _update_daily_snapshot_status(meta, status="ok", update_run_at=False)
            return meta
        try:
            meta = await run_in_threadpool(
                partial(
                    daily_snapshot.refresh_daily_snapshot,
                    settings,
                    n_top_coins=daily_snapshot.DEFAULT_N_TOP,
                    base_uri=base_uri,
                    now=now,
                )
            )
            status = "stale" if meta.stale else "ok"
            _update_daily_snapshot_status(meta, status=status, error=meta.error, run_at=now)
            if meta.stale or meta.error:
                _maybe_alert_daily_snapshot(target_date, meta=meta, error=meta.error)
            return meta
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            _update_daily_snapshot_status(None, status="error", error=error_msg, run_at=now)
            _maybe_alert_daily_snapshot(target_date, meta=None, error=error_msg)
            return None


async def _daily_snapshot_loop(settings: ServiceSettings) -> None:
    while True:
        try:
            if not _DAILY_SNAPSHOT_ENABLED:
                return
            await _maybe_run_daily_snapshot(settings)
            now = _utc_now()
            next_run = _next_daily_snapshot_run(now)
            sleep_seconds = max(60.0, (next_run - now).total_seconds())
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Daily snapshot loop error")
            await asyncio.sleep(300)


async def _start_daily_snapshot_scheduler(settings: ServiceSettings) -> None:
    global _DAILY_SNAPSHOT_TASK
    if not _DAILY_SNAPSHOT_ENABLED:
        return
    if _DAILY_SNAPSHOT_TASK is None or _DAILY_SNAPSHOT_TASK.done():
        _DAILY_SNAPSHOT_TASK = asyncio.create_task(_daily_snapshot_loop(settings))


async def _stop_daily_snapshot_scheduler() -> None:
    global _DAILY_SNAPSHOT_TASK
    if _DAILY_SNAPSHOT_TASK is None:
        return
    task = _DAILY_SNAPSHOT_TASK
    if task.done():
        _DAILY_SNAPSHOT_TASK = None
        return
    task.cancel()
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    task_loop = task.get_loop() if hasattr(task, "get_loop") else getattr(task, "_loop", None)
    if running_loop is not None and task_loop is running_loop:
        with suppress(asyncio.CancelledError):
            await task
    _DAILY_SNAPSHOT_TASK = None


def _first_day_next_month(base: date) -> date:
    if base.month == 12:
        return date(base.year + 1, 1, 1)
    return date(base.year, base.month + 1, 1)


def _index_auto_state_dir(settings: ServiceSettings) -> Path:
    target = settings.runs_root / _INDEX_AUTO_STATE_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def _index_auto_state_path(settings: ServiceSettings) -> Path:
    return _index_auto_state_dir(settings) / _INDEX_AUTO_CONFIG_NAME


def _persist_index_auto_config(settings: ServiceSettings, config: AutoRunConfig) -> None:
    path = _index_auto_state_path(settings)
    path.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")


def _index_auto_default_next_run(latest_run_date: date | None) -> date:
    today = date.today()
    if latest_run_date is None:
        return today
    if latest_run_date < today:
        return today
    return _first_day_next_month(latest_run_date)


def _load_index_auto_config(settings: ServiceSettings, *, latest_run_date: date | None = None) -> AutoRunConfig:
    path = _index_auto_state_path(settings)
    if not path.exists():
        config = AutoRunConfig(enabled=True, next_run_date=_index_auto_default_next_run(latest_run_date))
        _persist_index_auto_config(settings, config)
        return config
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("invalid config format")
        config = AutoRunConfig.from_dict(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falling back to default index auto config: %s", exc)
        config = AutoRunConfig(enabled=True, next_run_date=_index_auto_default_next_run(latest_run_date))
    if config.next_run_date is None:
        config.next_run_date = _index_auto_default_next_run(latest_run_date)
    _persist_index_auto_config(settings, config)
    return config


def _latest_index_auto_run(
    settings: ServiceSettings,
    *,
    before_timestamp: float | None = None,
    prefix: str = _INDEX_AUTO_PREFIX,
) -> Path | None:
    normalized_prefix = (prefix or "").strip() or _INDEX_AUTO_PREFIX
    return run_store.find_latest_run(
        settings,
        before_timestamp=before_timestamp,
        prefix=normalized_prefix,
    )


def _latest_index_auto_run_date(settings: ServiceSettings, *, prefix: str = _INDEX_AUTO_PREFIX) -> date | None:
    run_dir = _latest_index_auto_run(settings, prefix=prefix)
    if not run_dir:
        return None
    return datetime.utcfromtimestamp(run_dir.stat().st_mtime).date()


def _latest_index_auto_run_date_across_profiles(settings: ServiceSettings) -> date | None:
    latest_dates = [
        _latest_index_auto_run_date(settings, prefix=profile.run_prefix)
        for profile in _index_auto_profiles()
    ]
    populated_dates = [item for item in latest_dates if item is not None]
    if not populated_dates:
        return None
    return max(populated_dates)


def _make_index_auto_run_id(*, run_prefix: str) -> str:
    return f"{run_prefix}-{_utc_now().strftime('%Y-%m-%dT%H-%M-%SZ')}"


def _resolve_run_dir_if_valid(
    settings: ServiceSettings,
    run_id: str,
    *,
    before_timestamp: float | None = None,
) -> Path | None:
    try:
        run_dir = run_store.resolve_run_dir(settings, run_id)
    except FileNotFoundError:
        return None

    weights_path = run_dir / run_store.CSV_ARTIFACT
    if not weights_path.exists() or weights_path.stat().st_size <= 0:
        return None
    if before_timestamp is not None and run_dir.stat().st_mtime > before_timestamp:
        return None
    return run_dir


async def _persist_index_run_record(
    settings: ServiceSettings,
    *,
    run_id: str,
    source: account_models.IndexRunSource,
    account_id: uuid.UUID | None = None,
    api_key_id: uuid.UUID | None = None,
    session: AsyncSession | None = None,
) -> None:
    session_handle = session
    owns_session = False
    if session_handle is None:
        session_factory = await get_auth_sessionmaker(settings)
        session_handle = session_factory()
        owns_session = True

    try:
        existing_stmt = select(account_models.IndexRun).where(account_models.IndexRun.run_id == run_id).limit(1)
        existing = (await session_handle.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            if account_id is not None:
                existing.account_id = account_id
            if api_key_id is not None:
                existing.api_key_id = api_key_id
            existing.source = source
            existing.updated_at = datetime.now(timezone.utc)
        else:
            session_handle.add(
                account_models.IndexRun(
                    run_id=run_id,
                    source=source,
                    account_id=account_id,
                    api_key_id=api_key_id,
                )
            )
        await session_handle.commit()
    except Exception:  # noqa: BLE001
        logger.exception(
            "index_run_persist_failed",
            extra={"run_id": run_id, "source": getattr(source, "value", str(source))},
        )
        await session_handle.rollback()
    finally:
        if owns_session and session_handle is not None:
            await session_handle.close()


def _tag_index_run_metadata(
    settings: ServiceSettings,
    run_id: str,
    *,
    strategy_key: str,
    run_prefix: str,
    run_profile: dict[str, object],
) -> None:
    try:
        run_dir = run_store.resolve_run_dir(settings, run_id)
    except FileNotFoundError:
        return
    meta_path = run_dir / "meta.json"
    payload = {
        "source": "auto",
        "strategy": strategy_key,
        "tag": run_prefix,
        "run_profile": run_profile,
        "generated_at": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("Failed to persist auto-run metadata for %s", run_id)


def _tag_user_run_metadata(
    settings: ServiceSettings,
    run_id: str,
    *,
    account_id: uuid.UUID,
    api_key_id: uuid.UUID | None = None,
) -> None:
    try:
        run_dir = run_store.resolve_run_dir(settings, run_id)
    except FileNotFoundError:
        return
    meta_path = run_dir / "meta.json"
    payload = {
        "source": "user",
        "account_id": str(account_id),
        "api_key_id": str(api_key_id) if api_key_id else None,
        "generated_at": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("Failed to persist user-run metadata for %s", run_id)


def _ensure_index_auto_run_artifacts(settings: ServiceSettings, run_id: str) -> None:
    run_dir = run_store.resolve_run_dir(settings, run_id)
    required_files = (
        run_store.CSV_ARTIFACT,
        run_store.PERF_ARTIFACT,
        run_store.EQUITY_CURVE_ARTIFACT,
        "meta.json",
    )
    missing_files = [name for name in required_files if not (run_dir / name).exists()]
    if missing_files:
        joined = ", ".join(missing_files)
        raise RuntimeError(f"auto run '{run_id}' is missing required artifacts: {joined}")
    empty_files = [name for name in required_files if (run_dir / name).stat().st_size <= 0]
    if empty_files:
        joined = ", ".join(empty_files)
        raise RuntimeError(f"auto run '{run_id}' has empty artifacts: {joined}")
    meta_path = run_dir / "meta.json"
    try:
        loaded_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"auto run '{run_id}' meta.json is not valid JSON") from exc
    if not isinstance(loaded_meta, dict):
        raise RuntimeError(f"auto run '{run_id}' meta.json must be an object")


def _is_profile_already_ran_for_month(
    settings: ServiceSettings,
    *,
    profile: IndexAutoStrategyProfile,
    target_month: date,
) -> bool:
    latest_run_date = _latest_index_auto_run_date(settings, prefix=profile.run_prefix)
    if latest_run_date is None:
        return False
    return (latest_run_date.year, latest_run_date.month) == (target_month.year, target_month.month)


def _build_index_auto_runner_kwargs(profile: IndexAutoStrategyProfile, *, run_id: str) -> dict[str, object]:
    runner_kwargs = RUN_REQUEST_DEFAULTS.model_dump()
    runner_kwargs.update(profile.run_kwargs)
    runner_kwargs.update(
        {
            "run_id": run_id,
            "fresh_data": True,
            "info_messages": False,
            "visualization": False,
        }
    )
    return runner_kwargs


async def _run_index_auto_profile(
    settings: ServiceSettings,
    *,
    profile: IndexAutoStrategyProfile,
    run_id: str | None = None,
) -> dict[str, object]:
    resolved_id = run_id or _make_index_auto_run_id(run_prefix=profile.run_prefix)
    runner_kwargs = _build_index_auto_runner_kwargs(profile, run_id=resolved_id)
    config_path = getattr(settings, "config_path", None)
    if config_path is not None:
        runner_kwargs["config_path"] = config_path
    weights, perf = await run_in_threadpool(partial(run_monthly_update, **runner_kwargs))
    _tag_index_run_metadata(
        settings,
        run_id=resolved_id,
        strategy_key=profile.strategy_key,
        run_prefix=profile.run_prefix,
        run_profile=profile.run_kwargs,
    )
    _ensure_index_auto_run_artifacts(settings, resolved_id)
    await _persist_index_run_record(settings, run_id=resolved_id, source=account_models.IndexRunSource.AUTO)
    weights_payload = weights if isinstance(weights, dict) else {}
    perf_payload = perf if isinstance(perf, dict) else {}
    return {
        "strategy_key": profile.strategy_key,
        "run_prefix": profile.run_prefix,
        "run_id": resolved_id,
        "asset_count": len(weights_payload),
        "perf_keys": sorted(perf_payload.keys()),
    }


async def _run_index_auto(
    settings: ServiceSettings,
    *,
    force: bool = False,
    target_month: date | None = None,
) -> list[dict[str, object]]:
    reference_month = target_month or date.today()
    strategy_runs: list[dict[str, object]] = []
    for profile in _index_auto_profiles():
        if not force and _is_profile_already_ran_for_month(settings, profile=profile, target_month=reference_month):
            continue
        strategy_runs.append(await _run_index_auto_profile(settings, profile=profile))
    return strategy_runs


def _store_live_series_after_index_auto(
    settings: ServiceSettings,
    *,
    strategy_runs: list[dict[str, object]],
) -> None:
    """Persist a monthly equity CSV + meta.json for every completed auto run.

    Called synchronously after _run_index_auto so the stored series are available
    before the next page load. Errors are caught and logged — a storage failure
    must never block the broader refresh pipeline.
    """
    for run_info in strategy_runs:
        run_id = str(run_info.get("run_id") or "").strip()
        run_prefix = str(run_info.get("run_prefix") or "").strip()
        if not run_id or not run_prefix:
            continue
        run_dir = settings.runs_root / run_id
        if not run_dir.is_dir():
            logger.warning("store_live_series: run dir not found run_id=%s", run_id)
            continue
        try:
            result = store_live_run_month(settings, run_dir, run_prefix)
            logger.info("store_live_series: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("store_live_series: failed for run_id=%s prefix=%s", run_id, run_prefix)


async def _trigger_performance_refresh_after_index_auto(
    settings: ServiceSettings,
    *,
    strategy_runs: list[dict[str, object]],
    reason: str,
) -> None:
    if not strategy_runs:
        return
    # Store live month series first so the chart reflects the new run immediately.
    try:
        _store_live_series_after_index_auto(settings, strategy_runs=strategy_runs)
    except Exception:  # noqa: BLE001
        logger.exception("Post-index-auto live series storage failed")
    try:
        perf_config, snapshots, benchmarks = _load_auto_config_with_latest(settings)
        await _trigger_performance_refresh(
            settings,
            reason=reason,
            config=perf_config,
            snapshots=snapshots,
            benchmark_snapshots=benchmarks,
        )
    except HTTPException as exc:
        logger.info("Post-index-auto performance refresh skipped: %s", exc.detail)
    except Exception:
        logger.exception("Post-index-auto performance refresh failed")


def _update_index_auto_after_success(
    settings: ServiceSettings,
    config: AutoRunConfig,
    *,
    strategy_runs: list[dict[str, object]],
) -> AutoRunConfig:
    finished_at = datetime.utcnow()
    classic_run = next((item for item in strategy_runs if item.get("strategy_key") == "classic"), None)
    fallback_run = strategy_runs[0] if strategy_runs else None
    last_run_id = (
        str(classic_run.get("run_id"))
        if isinstance(classic_run, dict) and classic_run.get("run_id")
        else (str(fallback_run.get("run_id")) if isinstance(fallback_run, dict) and fallback_run.get("run_id") else None)
    )
    if last_run_id is None:
        existing_last_run_id = _INDEX_AUTO_STATUS.get("last_run_id")
        if isinstance(existing_last_run_id, str) and existing_last_run_id.strip():
            last_run_id = existing_last_run_id
    summary: dict[str, object] = {
        "run_count": len(strategy_runs),
        "strategy_runs": strategy_runs,
    }
    if not strategy_runs:
        summary["note"] = "already_up_to_date_for_current_month"
    updated = AutoRunConfig(
        enabled=config.enabled,
        next_run_date=_first_day_next_month(finished_at.date()),
        last_run_at=finished_at,
        last_run_status="ok",
        last_error=None,
        last_summary=summary,
    )
    _persist_index_auto_config(settings, updated)
    _INDEX_AUTO_STATUS.update(
        {
            "state": "idle",
            "started_at": None,
            "last_run_at": finished_at,
            "last_status": "ok",
            "last_error": None,
            "last_run_id": last_run_id,
            "strategy_runs": strategy_runs,
        }
    )
    return updated


def _update_index_auto_after_failure(settings: ServiceSettings, config: AutoRunConfig, error_message: str) -> AutoRunConfig:
    fallback_date = date.today() + timedelta(days=1)
    if config.next_run_date and config.next_run_date > fallback_date:
        fallback_date = config.next_run_date
    updated = AutoRunConfig(
        enabled=config.enabled,
        next_run_date=fallback_date,
        last_run_at=config.last_run_at,
        last_run_status="error",
        last_error=error_message,
        last_summary=config.last_summary or {},
    )
    _persist_index_auto_config(settings, updated)
    _INDEX_AUTO_STATUS.update(
        {
            "state": "idle",
            "started_at": None,
            "last_run_at": config.last_run_at,
            "last_status": "error",
            "last_error": error_message,
            "strategy_runs": (
                list(config.last_summary.get("strategy_runs", []))
                if isinstance(config.last_summary, dict)
                else []
            ),
        }
    )
    return updated


async def _maybe_run_index_auto(settings: ServiceSettings) -> AutoRunConfig:
    latest_date = _latest_index_auto_run_date_across_profiles(settings)
    config = _load_index_auto_config(settings, latest_run_date=latest_date)
    if not config.enabled:
        return config
    if os.getenv("AICI_ENABLE_PIPELINE", "1").lower() not in _PIPELINE_ENABLED_FLAGS:
        return config

    due_date = config.next_run_date or _index_auto_default_next_run(latest_date)
    if date.today() < due_date:
        return config
    if _INDEX_AUTO_LOCK.locked():
        return config

    strategy_runs: list[dict[str, object]] = []
    async with _INDEX_AUTO_LOCK:
        try:
            with hold_monthly_job_lock(
                settings.runs_root,
                contour=_INDEX_AUTO_LOCK_CONTOUR,
                target_month=date.today(),
                stale_after_seconds=_MONTHLY_JOB_LOCK_STALE_SECONDS,
            ):
                _INDEX_AUTO_STATUS.update({"state": "running", "started_at": datetime.utcnow(), "last_error": None})
                strategy_runs = await _run_index_auto(settings, force=False, target_month=date.today())
                config = _update_index_auto_after_success(
                    settings,
                    config,
                    strategy_runs=strategy_runs,
                )
        except MonthlyJobLockBusyError:
            return config
        except Exception as exc:  # noqa: BLE001
            logger.exception("Automatic index rebalance failed")
            config = _update_index_auto_after_failure(settings, config, str(exc))
    await _trigger_performance_refresh_after_index_auto(
        settings,
        strategy_runs=strategy_runs,
        reason="index_auto",
    )
    return config


async def _index_auto_loop(settings: ServiceSettings) -> None:
    while True:
        try:
            await _maybe_run_index_auto(settings)
            await asyncio.sleep(_INDEX_AUTO_POLL_SECONDS)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Index auto loop error")
            await asyncio.sleep(_INDEX_AUTO_POLL_SECONDS)


async def _start_index_auto_scheduler(settings: ServiceSettings) -> None:
    global _INDEX_AUTO_TASK
    if os.getenv("AICI_ENABLE_PIPELINE", "1").lower() not in _PIPELINE_ENABLED_FLAGS:
        return
    if _INDEX_AUTO_TASK is None or _INDEX_AUTO_TASK.done():
        _INDEX_AUTO_TASK = asyncio.create_task(_index_auto_loop(settings))


async def _stop_index_auto_scheduler() -> None:
    global _INDEX_AUTO_TASK
    if _INDEX_AUTO_TASK is None:
        return
    task = _INDEX_AUTO_TASK
    _INDEX_AUTO_TASK = None
    if task.get_loop() is not asyncio.get_running_loop():
        task.cancel()
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _index_auto_runtime_payload() -> dict[str, object]:
    payload = dict(_INDEX_AUTO_STATUS)
    raw_strategy_runs = payload.get("strategy_runs")
    if isinstance(raw_strategy_runs, list):
        payload["strategy_runs"] = list(raw_strategy_runs)
    else:
        payload["strategy_runs"] = []
    payload["started_at"] = _serialize_dt(payload.get("started_at"))
    payload["last_run_at"] = _serialize_dt(payload.get("last_run_at"))
    payload["is_running"] = _INDEX_AUTO_LOCK.locked()
    return payload


def _cta_format_optimization_runtime_payload() -> dict[str, object]:
    payload = dict(_CTA_FORMAT_OPTIMIZATION_STATUS)
    raw_top_formats = payload.get("last_top_formats")
    if isinstance(raw_top_formats, list):
        payload["last_top_formats"] = [str(item) for item in raw_top_formats]
    else:
        payload["last_top_formats"] = []
    payload["started_at"] = _serialize_dt(payload.get("started_at"))
    payload["last_run_at"] = _serialize_dt(payload.get("last_run_at"))
    payload["is_running"] = _CTA_FORMAT_OPTIMIZATION_LOCK.locked()
    return payload


def _is_cta_format_optimization_due(
    latest_decision: dict[str, object] | None,
    *,
    now: datetime,
) -> bool:
    if not isinstance(latest_decision, dict):
        return True
    decided_raw = latest_decision.get("decided_at")
    if not isinstance(decided_raw, str) or not decided_raw.strip():
        return True
    try:
        decided_at = datetime.fromisoformat(decided_raw)
    except ValueError:
        return True
    if decided_at.tzinfo is None or decided_at.tzinfo.utcoffset(decided_at) is None:
        decided_at = decided_at.replace(tzinfo=timezone.utc)
    else:
        decided_at = decided_at.astimezone(timezone.utc)
    due_at = decided_at + timedelta(days=_CTA_FORMAT_OPTIMIZATION_WINDOW_DAYS)
    return now >= due_at


async def _maybe_run_cta_format_optimization(
    settings: ServiceSettings,
    *,
    force: bool = False,
) -> dict[str, object] | None:
    latest_decision = await run_in_threadpool(
        partial(cta_analytics_store.get_latest_cta_format_optimization_decision, settings)
    )
    if not _CTA_FORMAT_OPTIMIZATION_ENABLED and not force:
        return latest_decision
    if _CTA_FORMAT_OPTIMIZATION_LOCK.locked():
        return latest_decision

    now = _utc_now()
    if not force and not _is_cta_format_optimization_due(latest_decision, now=now):
        return latest_decision

    async with _CTA_FORMAT_OPTIMIZATION_LOCK:
        _CTA_FORMAT_OPTIMIZATION_STATUS.update({"state": "running", "started_at": now, "last_error": None})
        try:
            decision = await run_in_threadpool(
                partial(
                    cta_analytics_store.run_weekly_cta_format_optimization,
                    settings,
                    now=now,
                    window_days=_CTA_FORMAT_OPTIMIZATION_WINDOW_DAYS,
                    top_n=_CTA_FORMAT_OPTIMIZATION_TOP_N,
                )
            )
            top_formats = decision.get("top_formats", []) if isinstance(decision, dict) else []
            _CTA_FORMAT_OPTIMIZATION_STATUS.update(
                {
                    "state": "idle",
                    "started_at": None,
                    "last_run_at": _utc_now(),
                    "last_status": "ok",
                    "last_error": None,
                    "last_decision_id": decision.get("id") if isinstance(decision, dict) else None,
                    "last_top_formats": list(top_formats) if isinstance(top_formats, list) else [],
                }
            )
            logger.info(
                "cta_format_optimization_applied",
                extra={
                    "decision_id": decision.get("id") if isinstance(decision, dict) else None,
                    "top_formats": top_formats,
                    "window_days": _CTA_FORMAT_OPTIMIZATION_WINDOW_DAYS,
                    "top_n": _CTA_FORMAT_OPTIMIZATION_TOP_N,
                },
            )
            return decision
        except Exception as exc:  # noqa: BLE001
            _CTA_FORMAT_OPTIMIZATION_STATUS.update(
                {
                    "state": "idle",
                    "started_at": None,
                    "last_run_at": _utc_now(),
                    "last_status": "error",
                    "last_error": str(exc),
                }
            )
            logger.exception("CTA format weekly optimization failed")
            return latest_decision


async def _cta_format_optimization_loop(settings: ServiceSettings) -> None:
    while True:
        try:
            if not _CTA_FORMAT_OPTIMIZATION_ENABLED:
                return
            await _maybe_run_cta_format_optimization(settings)
            await asyncio.sleep(_CTA_FORMAT_OPTIMIZATION_POLL_SECONDS)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("CTA format optimization loop error")
            await asyncio.sleep(_CTA_FORMAT_OPTIMIZATION_POLL_SECONDS)


async def _start_cta_format_optimization_scheduler(settings: ServiceSettings) -> None:
    global _CTA_FORMAT_OPTIMIZATION_TASK
    if not _CTA_FORMAT_OPTIMIZATION_ENABLED:
        return
    if _CTA_FORMAT_OPTIMIZATION_TASK is None or _CTA_FORMAT_OPTIMIZATION_TASK.done():
        _CTA_FORMAT_OPTIMIZATION_TASK = asyncio.create_task(_cta_format_optimization_loop(settings))


async def _stop_cta_format_optimization_scheduler() -> None:
    global _CTA_FORMAT_OPTIMIZATION_TASK
    if _CTA_FORMAT_OPTIMIZATION_TASK is None:
        return
    task = _CTA_FORMAT_OPTIMIZATION_TASK
    if task.done():
        _CTA_FORMAT_OPTIMIZATION_TASK = None
        return
    running_loop = asyncio.get_running_loop()
    if task.get_loop() is not running_loop:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    _CTA_FORMAT_OPTIMIZATION_TASK = None


async def _billing_reminder_loop(settings: ServiceSettings) -> None:
    if not _BILLING_REMINDERS_ENABLED:
        return
    session_factory = await get_auth_sessionmaker(settings)
    billing_service = BillingService(settings)
    while True:
        try:
            async with session_factory() as session:
                await billing_service.send_crypto_renewal_notifications(session)
                await billing_service.send_crypto_stuck_payment_alerts(session)
            await asyncio.sleep(_BILLING_REMINDER_POLL_SECONDS)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Billing reminder loop error")
            await asyncio.sleep(_BILLING_REMINDER_POLL_SECONDS)


async def _start_billing_reminder_scheduler(settings: ServiceSettings) -> None:
    global _BILLING_REMINDER_TASK
    if not _BILLING_REMINDERS_ENABLED:
        return
    if settings.billing.provider != "crypto":
        return
    if _BILLING_REMINDER_TASK is None or _BILLING_REMINDER_TASK.done():
        _BILLING_REMINDER_TASK = asyncio.create_task(_billing_reminder_loop(settings))


async def _stop_billing_reminder_scheduler() -> None:
    global _BILLING_REMINDER_TASK
    if _BILLING_REMINDER_TASK is None:
        return
    task = _BILLING_REMINDER_TASK
    if task.done():
        _BILLING_REMINDER_TASK = None
        return
    running_loop = asyncio.get_running_loop()
    if task.get_loop() is not running_loop:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    _BILLING_REMINDER_TASK = None


def _build_index_auto_status_payload(settings: ServiceSettings, config: AutoRunConfig | None = None) -> dict[str, object]:
    config = config or _load_index_auto_config(
        settings,
        latest_run_date=_latest_index_auto_run_date_across_profiles(settings),
    )
    runtime = _index_auto_runtime_payload()
    last_summary = config.last_summary if isinstance(config.last_summary, dict) else {}
    strategy_runs = last_summary.get("strategy_runs")
    if not isinstance(strategy_runs, list):
        strategy_runs = runtime.get("strategy_runs")
    if not isinstance(strategy_runs, list):
        strategy_runs = []
    return {
        "config": config.to_dict(),
        "runtime": runtime,
        "last_run_id": runtime.get("last_run_id"),
        "last_summary": last_summary,
        "strategy_runs": strategy_runs,
    }


def _find_latest_index_run(
    settings: ServiceSettings,
    *,
    before_timestamp: float | None = None,
) -> Path | None:
    run_dir = _latest_index_auto_run(settings, before_timestamp=before_timestamp)
    if run_dir is None:
        run_dir = run_store.find_latest_run(settings, before_timestamp=before_timestamp)
    return run_dir


async def _find_latest_user_index_run(
    settings: ServiceSettings,
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    before_timestamp: float | None = None,
    ) -> Path | None:
    stmt = (
        select(account_models.IndexRun.run_id)
        .where(
            account_models.IndexRun.account_id == account_id,
            account_models.IndexRun.source == account_models.IndexRunSource.USER,
        )
        .order_by(account_models.IndexRun.created_at.desc(), account_models.IndexRun.id.desc())
        .limit(8)
    )
    try:
        run_ids = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError:
        logger.exception("index_run_lookup_failed_user")
        await session.rollback()
        return None
    for run_id in run_ids:
        run_dir = _resolve_run_dir_if_valid(settings, run_id, before_timestamp=before_timestamp)
        if run_dir:
            return run_dir
    return None


async def _find_latest_auto_index_run_recorded(
    settings: ServiceSettings,
    session: AsyncSession,
    *,
    before_timestamp: float | None = None,
) -> Path | None:
    stmt = (
        select(account_models.IndexRun.run_id)
        .where(account_models.IndexRun.source == account_models.IndexRunSource.AUTO)
        .order_by(account_models.IndexRun.created_at.desc(), account_models.IndexRun.id.desc())
        .limit(8)
    )
    try:
        run_ids = (await session.execute(stmt)).scalars().all()
    except SQLAlchemyError:
        logger.exception("index_run_lookup_failed_auto")
        await session.rollback()
        return _latest_index_auto_run(settings, before_timestamp=before_timestamp)
    for run_id in run_ids:
        run_dir = _resolve_run_dir_if_valid(settings, run_id, before_timestamp=before_timestamp)
        if run_dir:
            return run_dir
    return _latest_index_auto_run(settings, before_timestamp=before_timestamp)


async def _find_latest_index_run_for_context(
    settings: ServiceSettings,
    session: AsyncSession,
    context: ApiKeyAuthContext,
    *,
    before_timestamp: float | None = None,
) -> Path | None:
    run_dir = await _find_latest_user_index_run(
        settings,
        session,
        context.account.id,
        before_timestamp=before_timestamp,
    )
    if run_dir is not None:
        return run_dir
    run_dir = await _find_latest_auto_index_run_recorded(settings, session, before_timestamp=before_timestamp)
    if run_dir is not None:
        return run_dir
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("AICI_DEV") in {"1", "true", "True"}:
        return run_store.find_latest_run(settings, before_timestamp=before_timestamp)
    return None


async def _find_latest_index_run_for_account_page(
    settings: ServiceSettings,
    session: AsyncSession,
    account: account_models.Account | None,
    plan: ApiKeyPlanSettings | None,
) -> Path | None:
    cutoff = None
    if plan is not None:
        cutoff = time.time() - max(plan.data_latency_seconds, 0)

    account_id = getattr(account, "id", None)
    if account_id:
        run_dir = await _find_latest_user_index_run(
            settings,
            session,
            account_id,
            before_timestamp=cutoff,
        )
        if run_dir is not None:
            return run_dir

    return await _find_latest_auto_index_run_recorded(
        settings,
        session,
        before_timestamp=cutoff,
    )


def _collect_run_query_overrides(
    request: Request,
    n_top_coins: int = Query(
        default=RUN_REQUEST_DEFAULTS.n_top_coins,
        ge=models.RUN_N_TOP_COINS_MIN,
        le=models.RUN_N_TOP_COINS_MAX,
        description="Override number of assets ranked into the index (defaults to model setting).",
    ),
    start_date: date | None = Query(
        default=None,
        description="Optional ISO date to anchor the historical window.",
    ),
    lookback_days: int = Query(
        default=RUN_REQUEST_DEFAULTS.lookback_days,
        ge=models.RUN_LOOKBACK_DAYS_MIN,
        le=models.RUN_LOOKBACK_DAYS_MAX,
        description="Rolling window in days for feature preparation.",
    ),
    window_size: int = Query(
        default=RUN_REQUEST_DEFAULTS.window_size,
        ge=models.RUN_WINDOW_SIZE_MIN,
        le=models.RUN_WINDOW_SIZE_MAX,
        description="Number of days per forecasting batch.",
    ),
    forecast_horizon: int = Query(
        default=RUN_REQUEST_DEFAULTS.forecast_horizon,
        ge=models.RUN_FORECAST_HORIZON_MIN,
        le=models.RUN_FORECAST_HORIZON_MAX,
        description="Days ahead to project returns/volatility.",
    ),
    advanced_forecast: bool = Query(
        default=RUN_REQUEST_DEFAULTS.advanced_forecast,
        description="Enable advanced forecast mode.",
    ),
    info_messages: bool = Query(
        default=RUN_REQUEST_DEFAULTS.info_messages,
        description="Return verbose informational messages in pipeline logs.",
    ),
    visualization: bool = Query(
        default=RUN_REQUEST_DEFAULTS.visualization,
        description="Generate auxiliary visualization artifacts.",
    ),
    total_assets: int = Query(
        default=RUN_REQUEST_DEFAULTS.total_assets,
        ge=models.RUN_TOTAL_ASSETS_MIN,
        le=models.RUN_TOTAL_ASSETS_MAX,
        description="Total assets to retain after balanced selection.",
    ),
    clustering_metric: str = Query(
        default=RUN_REQUEST_DEFAULTS.clustering_metric,
        min_length=1,
        max_length=64,
        description="Metric for select_assets_balanced (e.g., 'sharpe').",
    ),
    risk_min_weight: float = Query(
        default=RUN_REQUEST_DEFAULTS.risk_min_weight,
        ge=models.RUN_RISK_MIN_WEIGHT_MIN,
        le=models.RUN_RISK_MIN_WEIGHT_MAX,
        description="Lower bound for risk parity weights.",
    ),
    risk_max_weight: float = Query(
        default=RUN_REQUEST_DEFAULTS.risk_max_weight,
        ge=models.RUN_RISK_MAX_WEIGHT_MIN,
        le=models.RUN_RISK_MAX_WEIGHT_MAX,
        description="Upper bound for risk parity weights.",
    ),
    weight_cap: float = Query(
        default=RUN_REQUEST_DEFAULTS.weight_cap,
        ge=models.RUN_WEIGHT_CAP_MIN,
        le=models.RUN_WEIGHT_CAP_MAX,
        description="Max cap applied after risk parity scaling.",
    ),
    vol_floor_ratio: float = Query(
        default=RUN_REQUEST_DEFAULTS.vol_floor_ratio,
        ge=models.RUN_VOL_FLOOR_RATIO_MIN,
        le=models.RUN_VOL_FLOOR_RATIO_MAX,
        description="Minimum allowed ratio of forecasted volatility vs historical sigma.",
    ),
    gating_tolerance: float = Query(
        default=RUN_REQUEST_DEFAULTS.gating_tolerance,
        ge=0.0,
        le=models.RUN_GATING_TOLERANCE_MAX,
        description="Error tolerance for gating forecasts vs benchmarks.",
    ),
    run_id: str | None = Query(
        default=None,
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.\-]+$",
        description="Optional identifier; leave blank to auto-generate.",
    ),
) -> models.RunRequest:
    fresh_data_override = request.query_params.get("fresh_data")
    if fresh_data_override is not None:
        logger.info(
            "Ignoring deprecated fresh_data override for %s: %s",
            request.url.path,
            fresh_data_override,
        )
    try:
        return models.RunRequest(
            n_top_coins=n_top_coins,
            start_date=start_date,
            lookback_days=lookback_days,
            window_size=window_size,
            forecast_horizon=forecast_horizon,
            advanced_forecast=advanced_forecast,
            fresh_data=RUN_REQUEST_DEFAULTS.fresh_data,
            info_messages=info_messages,
            visualization=visualization,
            total_assets=total_assets,
            clustering_metric=clustering_metric,
            risk_min_weight=risk_min_weight,
            risk_max_weight=risk_max_weight,
            weight_cap=weight_cap,
            vol_floor_ratio=vol_floor_ratio,
            gating_tolerance=gating_tolerance,
            run_id=run_id,
        )
    except ValidationError as exc:
        message = "invalid run parameters"
        details = exc.errors()
        if details:
            first = details[0]
            ctx = first.get("ctx")
            if isinstance(ctx, dict):
                raw_error = ctx.get("error")
                if raw_error:
                    message = str(raw_error)
            if message == "invalid run parameters":
                msg = first.get("msg")
                if isinstance(msg, str) and msg.strip():
                    message = msg.strip()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message) from exc


def _is_safe_redirect(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return not parsed.scheme and not parsed.netloc and bool(parsed.path.startswith("/"))


def create_api_app(admin_config: dict[str, str] | None) -> FastAPI:
    swagger_config = _resolve_swagger_config()
    settings_snapshot = get_settings()
    _sync_token_pricing(settings_snapshot)
    admin_dependency = _build_admin_dependency(admin_config)
    api_app = FastAPI(
        title="AI Crypto Index API",
        version=API_VERSION,
        openapi_tags=API_TAGS_METADATA,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    allowed_origins = _resolve_allowed_origins()
    rate_limit_limit, rate_limit_window = _resolve_rate_limit()
    signup_rate_limiter = SlidingWindowRateLimiter()
    resend_rate_limiter = SlidingWindowRateLimiter()
    signup_rate_limit, signup_rate_window = _resolve_signup_rate_limit()
    resend_rate_limit, resend_rate_window = _resolve_resend_rate_limit()

    api_app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api_app.add_middleware(
        RateLimitMiddleware,
        limit=rate_limit_limit,
        window_seconds=rate_limit_window,
    )
    api_app.state.rate_limit = {"limit": rate_limit_limit, "window_seconds": rate_limit_window}
    api_app.state.allowed_origins = allowed_origins
    api_app.state.signup_rate_limiter = signup_rate_limiter
    api_app.state.signup_rate_limit = signup_rate_limit
    api_app.state.signup_rate_window = signup_rate_window
    api_app.state.resend_rate_limiter = resend_rate_limiter
    api_app.state.resend_rate_limit = resend_rate_limit
    api_app.state.resend_rate_window = resend_rate_window
    _install_swagger_endpoints(api_app, swagger_config)

    settings_dependency = Depends(get_settings)
    session_dependency = Depends(get_db_session)
    account_service_dependency = Depends(get_account_service)
    api_key_service_dependency = Depends(get_api_key_service)
    db_session_dependency = Depends(get_db_session)
    account_service_dependency = Depends(get_account_service)
    billing_service_dependency = Depends(get_billing_service)
    api_key_service_dependency = Depends(get_api_key_service)
    api_router = APIRouter(prefix=API_VERSION_ROUTE)
    logger.info(
        "api_bootstrap_complete",
        extra={
            "rate_limit": api_app.state.rate_limit,
            "allowed_origins": allowed_origins,
            "dev_mode": os.getenv("AICI_DEV") in {"1", "true", "True"},
        },
    )

    async def _collect_usage_series(
        session: AsyncSession,
        account: account_models.Account,
        *,
        window_days: int,
    ) -> tuple[list[dict[str, object]], dict[str, int]]:
        today = date.today()
        start_date = today - timedelta(days=max(window_days, 1) - 1)
        usage_stmt = (
            select(
                account_models.ApiKeyUsageDaily.usage_date,
                func.sum(account_models.ApiKeyUsageDaily.call_count).label("call_count"),
            )
            .join(account_models.ApiKey, account_models.ApiKeyUsageDaily.api_key_id == account_models.ApiKey.id)
            .where(
                account_models.ApiKey.account_id == account.id,
                account_models.ApiKeyUsageDaily.usage_date >= start_date,
            )
            .group_by(account_models.ApiKeyUsageDaily.usage_date)
        )
        usage_rows = await session.execute(usage_stmt)
        usage_map = {
            _normalize_usage_date(row.usage_date): int(row.call_count or 0)
            for row in usage_rows
        }

        start_timestamp = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        error_stmt = (
            select(
                func.date(account_models.ApiUsageEvent.created_at).label("usage_date"),
                func.count().label("error_count"),
            )
            .where(
                account_models.ApiUsageEvent.account_id == account.id,
                account_models.ApiUsageEvent.created_at >= start_timestamp,
                account_models.ApiUsageEvent.status_code >= 400,
            )
            .group_by(func.date(account_models.ApiUsageEvent.created_at))
        )
        error_rows = await session.execute(error_stmt)
        error_map = {
            _normalize_usage_date(row.usage_date): int(row.error_count or 0)
            for row in error_rows
        }

        points: list[dict[str, object]] = []
        totals = {"total_calls": 0, "total_errors": 0, "max_calls": 0}
        for offset in range(window_days):
            day = start_date + timedelta(days=offset)
            calls = usage_map.get(day, 0)
            errors = error_map.get(day, 0)
            points.append({"date": day, "call_count": calls, "error_count": errors})
            totals["total_calls"] += calls
            totals["total_errors"] += errors
            if calls > totals["max_calls"]:
                totals["max_calls"] = calls
        return points, totals

    async def _load_monthly_usage(session: AsyncSession, account: account_models.Account) -> int:
        month_start = date.today().replace(day=1)
        monthly_stmt = (
            select(func.sum(account_models.ApiKeyUsageMonthly.call_count))
            .join(account_models.ApiKey, account_models.ApiKeyUsageMonthly.api_key_id == account_models.ApiKey.id)
            .where(
                account_models.ApiKey.account_id == account.id,
                account_models.ApiKeyUsageMonthly.period_start == month_start,
            )
        )
        monthly_usage = await session.scalar(monthly_stmt)
        return int(monthly_usage or 0)

    def _resolve_account_plan(
        account: account_models.Account,
        api_key_service: ApiKeyService,
    ) -> ApiKeyPlanSettings:
        plan_code = None
        if account.billing_subscriptions:
            sorted_subs = sorted(
                account.billing_subscriptions,
                key=lambda item: item.updated_at or item.created_at,
                reverse=True,
            )
            if sorted_subs:
                plan_code = sorted_subs[0].plan_code
        if not plan_code:
            plan_code = api_key_service.settings.api_keys.default_plan_code
        plans = api_key_service.settings.api_keys.plans
        plan = plans.get(plan_code)
        if plan:
            return plan
        # fallback to first known plan
        return next(iter(plans.values()))

    async def _list_usage_alerts(
        session: AsyncSession,
        account: account_models.Account,
    ) -> list[account_models.UsageAlertRule]:
        stmt = (
            select(account_models.UsageAlertRule)
            .where(account_models.UsageAlertRule.account_id == account.id)
            .order_by(account_models.UsageAlertRule.created_at.asc())
        )
        rows = await session.scalars(stmt)
        return list(rows)

    def _normalize_cta_filter_values(values: list[str] | None) -> tuple[str, ...]:
        if not values:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value is None:
                continue
            for chunk in str(value).split(","):
                candidate = chunk.strip().lower()
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                normalized.append(candidate)
        return tuple(normalized)

    def _ensure_utc_datetime(value: datetime | None, *, fallback: datetime) -> datetime:
        if value is None:
            return fallback
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _build_admin_cta_query(
        *,
        start_at: datetime | None,
        end_at: datetime | None,
        lookback_days: int,
        cta_ids: list[str] | None,
        cta_types: list[str] | None,
        cta_formats: list[str] | None,
        placements: list[str] | None,
        utm_sources: list[str] | None,
        traffic_sources: list[str] | None,
        pages: list[str] | None,
        auth_states: list[str] | None,
        referrers: list[str] | None,
        utm_values: list[str] | None,
    ) -> CtaMetricsQuery:
        end_default = datetime.now(timezone.utc)
        end_value = _ensure_utc_datetime(end_at, fallback=end_default)
        start_default = end_value - timedelta(days=lookback_days)
        start_value = _ensure_utc_datetime(start_at, fallback=start_default)
        return CtaMetricsQuery(
            start_at=start_value,
            end_at=end_value,
            cta_ids=_normalize_cta_filter_values(cta_ids),
            cta_types=_normalize_cta_filter_values(cta_types),
            cta_formats=_normalize_cta_filter_values(cta_formats),
            locations=_normalize_cta_filter_values(placements),
            utm_sources=_normalize_cta_filter_values(utm_sources),
            traffic_sources=_normalize_cta_filter_values(traffic_sources),
            page_paths=_normalize_cta_filter_values(pages),
            auth_states=_normalize_cta_filter_values(auth_states),
            referers=_normalize_cta_filter_values(referrers),
            utm_values=_normalize_cta_filter_values(utm_values),
            lookback_days=lookback_days,
        )

    def _paginate_cta_items(
        items: list[dict[str, Any]],
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        total_items = len(items)
        total_pages = ceil(total_items / page_size) if total_items > 0 else 0
        offset = (page - 1) * page_size
        if offset >= total_items:
            paged_items: list[dict[str, Any]] = []
        else:
            paged_items = items[offset : offset + page_size]
        return paged_items, {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
        }

    def _build_cta_csv_response(
        *,
        rows: list[dict[str, object]],
        headers: list[str],
        filename: str,
    ) -> StreamingResponse:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in headers})
        csv_payload = buffer.getvalue().encode("utf-8")
        response_headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return StreamingResponse(iter([csv_payload]), media_type="text/csv", headers=response_headers)

    async def _load_admin_cta_dashboard(
        *,
        session: AsyncSession,
        settings: ServiceSettings,
        start_at: datetime | None,
        end_at: datetime | None,
        lookback_days: int,
        cta_ids: list[str] | None,
        cta_types: list[str] | None,
        cta_formats: list[str] | None,
        placements: list[str] | None,
        utm_sources: list[str] | None,
        traffic_sources: list[str] | None,
        pages: list[str] | None,
        auth_states: list[str] | None,
        referrers: list[str] | None,
        utm_values: list[str] | None,
        interval: str = "day",
        breakdown_limit: int | None = 100,
        top_limit: int | None = 100,
    ) -> dict[str, Any]:
        query = _build_admin_cta_query(
            start_at=start_at,
            end_at=end_at,
            lookback_days=lookback_days,
            cta_ids=cta_ids,
            cta_types=cta_types,
            cta_formats=cta_formats,
            placements=placements,
            utm_sources=utm_sources,
            traffic_sources=traffic_sources,
            pages=pages,
            auth_states=auth_states,
            referrers=referrers,
            utm_values=utm_values,
        )
        service = CtaMetricsService(settings)
        try:
            return await service.build_dashboard(
                session,
                query,
                interval=interval,
                breakdown_limit=breakdown_limit,
                top_limit=top_limit,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @api_router.post(
        "/auth/signup",
        response_model=models.SignupResponse,
        status_code=status.HTTP_201_CREATED,
        name="api_auth_signup",
        tags=["auth"],
    )
    async def auth_signup(
        payload: models.SignupRequest,
        request: Request,
        background_tasks: BackgroundTasks,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.SignupResponse:
        context = _build_request_context(request)
        rate_key = context.ip_address or (request.client.host if request.client else None) or "unknown"
        app_state = getattr(request.app, "state", None)
        limiter = getattr(app_state, "signup_rate_limiter", None)
        if limiter is None:
            limiter = SlidingWindowRateLimiter()
            if app_state is not None:
                app_state.signup_rate_limiter = limiter
        signup_limit = getattr(app_state, "signup_rate_limit", 5)
        signup_window = getattr(app_state, "signup_rate_window", 60)
        await limiter.hit(rate_key, limit=signup_limit, window_seconds=signup_window)
        try:
            signup_result = await account_service.signup(
                session,
                email=payload.email,
                password=payload.password,
                newsletter_opt_in=payload.newsletter_opt_in,
                terms_version=payload.terms_version,
                context=context,
            )
        except AccountAlreadyExists as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="account_exists") from exc

        confirmation_link = _build_confirmation_link(settings, signup_result.confirmation_token)
        background_tasks.add_task(
            email_notifications.send_signup_confirmation_email,
            recipient=payload.email,
            full_name=signup_result.account.full_name,
            confirmation_link=confirmation_link,
            expires_at=signup_result.confirmation_expires_at,
        )
        if payload.cta_session_id or payload.source_cta_id:
            with suppress(Exception):
                await _track_account_cta_bridge_event(
                    settings=settings,
                    request=request,
                    account_id=signup_result.account.id,
                    event_type="signup_started",
                    cta_session_id=payload.cta_session_id,
                    source_cta_id=payload.source_cta_id,
                    source_page_path=payload.source_page_path,
                    source_scenario=payload.source_scenario,
                )

        return models.SignupResponse(
            account_id=str(signup_result.account.id),
            email=payload.email,
            status=signup_result.account.status.value,
            next_step="confirm_email",
            debug_confirmation_token=signup_result.confirmation_token
            if settings.auth.expose_tokens_in_responses
            else None,
        )

    @api_router.post(
        "/auth/confirm",
        response_model=models.AuthSessionResponse,
        name="api_auth_confirm",
        tags=["auth"],
    )
    async def auth_confirm(
        payload: models.ConfirmEmailRequest,
        request: Request,
        response: Response,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.AuthSessionResponse:
        context = _build_request_context(request)
        try:
            result = await account_service.confirm_email(
                session,
                token=payload.token,
                context=context,
            )
        except TokenInvalid as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_token") from exc
        except TokenExpired as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="token_expired") from exc
        except AccountNotFound as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="account_not_found") from exc
        with suppress(Exception):
            await _track_account_cta_bridge_event(
                settings=settings,
                request=request,
                account_id=result.account.id,
                event_type="email_confirmed",
            )

        return _build_auth_response(
            service=account_service,
            settings=settings,
            response=response,
            result=result,
        )

    @api_router.post(
        "/auth/confirm/resend",
        response_model=models.SimpleMessageResponse,
        name="api_auth_resend_confirmation",
        tags=["auth"],
    )
    async def auth_resend_confirmation(
        payload: models.ResendConfirmationRequest,
        request: Request,
        background_tasks: BackgroundTasks,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.SimpleMessageResponse:
        context = _build_request_context(request)
        rate_key = context.ip_address or (request.client.host if request.client else None) or "unknown"
        app_state = getattr(request.app, "state", None)
        limiter = getattr(app_state, "resend_rate_limiter", None)
        if limiter is None:
            limiter = SlidingWindowRateLimiter()
            if app_state is not None:
                app_state.resend_rate_limiter = limiter
        resend_limit = getattr(app_state, "resend_rate_limit", 5)
        resend_window = getattr(app_state, "resend_rate_window", 60)
        await limiter.hit(rate_key, limit=resend_limit, window_seconds=resend_window)
        try:
            account, token_value, expires_at = await account_service.resend_confirmation_email(
                session,
                email=payload.email,
            )
        except ConfirmationResendRateLimited as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="resend_rate_limited") from exc

        if account and token_value and expires_at:
            confirmation_link = _build_confirmation_link(settings, token_value)
            background_tasks.add_task(
                email_notifications.send_signup_confirmation_email,
                recipient=payload.email,
                full_name=account.full_name,
                confirmation_link=confirmation_link,
                expires_at=expires_at,
            )

        return models.SimpleMessageResponse(
            message="If the email is registered, a new confirmation link was sent."
        )

    @api_router.post(
        "/auth/login",
        response_model=models.AuthSessionResponse,
        name="api_auth_login",
        tags=["auth"],
    )
    async def auth_login(
        payload: models.LoginRequest,
        request: Request,
        response: Response,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.AuthSessionResponse:
        context = _build_request_context(request)
        try:
            result = await account_service.login(
                session,
                email=payload.email,
                password=payload.password,
                context=context,
            )
        except (AccountNotFound, InvalidCredentials) as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials") from exc
        except AccountInactive as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="account_pending_activation") from exc

        return _build_auth_response(
            service=account_service,
            settings=settings,
            response=response,
            result=result,
        )

    @api_router.post(
        "/auth/refresh",
        response_model=models.AuthSessionResponse,
        name="api_auth_refresh",
        tags=["auth"],
    )
    async def auth_refresh(
        request: Request,
        response: Response,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.AuthSessionResponse:
        refresh_token = request.cookies.get(settings.auth.session_cookie_name)
        if not refresh_token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing_refresh_token")
        context = _build_request_context(request)
        try:
            result = await account_service.refresh_session(
                session,
                refresh_token=refresh_token,
                context=context,
            )
        except SessionInvalid as exc:
            _clear_refresh_cookie(response, settings)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_refresh_token") from exc

        return _build_auth_response(
            service=account_service,
            settings=settings,
            response=response,
            result=result,
        )

    @api_router.post(
        "/auth/logout",
        response_model=models.SimpleMessageResponse,
        name="api_auth_logout",
        tags=["auth"],
    )
    async def auth_logout(
        request: Request,
        response: Response,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.SimpleMessageResponse:
        refresh_token = request.cookies.get(settings.auth.session_cookie_name)
        if refresh_token:
            try:
                await account_service.logout(session, refresh_token=refresh_token)
            except SessionInvalid:
                pass
        _clear_refresh_cookie(response, settings)
        return models.SimpleMessageResponse(message="ok")

    @api_router.post(
        "/auth/password/forgot",
        response_model=models.PasswordResetRequestResponse,
        name="api_auth_forgot_password",
        tags=["auth"],
    )
    async def auth_forgot_password(
        payload: models.ForgotPasswordRequest,
        request: Request,
        background_tasks: BackgroundTasks,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.PasswordResetRequestResponse:
        context = _build_request_context(request)
        account, token_value, expires = await account_service.request_password_reset(
            session,
            email=payload.email,
            context=context,
        )
        if account and token_value and expires:
            reset_link = _build_reset_link(settings, token_value)
            background_tasks.add_task(
                email_notifications.send_password_reset_email,
                recipient=account.email,
                full_name=account.full_name,
                reset_link=reset_link,
                expires_at=expires,
            )
        return models.PasswordResetRequestResponse(
            message="If the email exists, you'll receive reset instructions shortly.",
            debug_reset_token=token_value if settings.auth.expose_tokens_in_responses else None,
        )

    @api_router.post(
        "/auth/password/reset",
        response_model=models.AuthSessionResponse,
        name="api_auth_reset_password",
        tags=["auth"],
    )
    async def auth_reset_password(
        payload: models.ResetPasswordRequest,
        request: Request,
        response: Response,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.AuthSessionResponse:
        context = _build_request_context(request)
        try:
            result = await account_service.reset_password(
                session,
                token=payload.token,
                new_password=payload.password,
                context=context,
            )
        except TokenExpired as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="token_expired") from exc
        except (TokenInvalid, AccountNotFound) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_token") from exc

        return _build_auth_response(
            service=account_service,
            settings=settings,
            response=response,
            result=result,
        )

    @api_router.get(
        "/auth/google/login",
        name="api_auth_google_login",
        tags=["auth"],
        response_class=RedirectResponse,
    )
    async def auth_google_login(
        request: Request,
        next: str | None = Query(None, alias="next"),
        settings: ServiceSettings = settings_dependency,
    ) -> RedirectResponse:
        if not settings.google_client_id:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="google_oauth_not_configured")
        state = secrets.token_urlsafe(32)
        redirect_uri = f"{settings.auth.public_app_url.rstrip('/')}/api/v1/auth/google/callback"
        google_url = (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={settings.google_client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope=openid%20email%20profile"
            f"&state={state}"
            f"&access_type=offline"
            f"&prompt=select_account"
        )
        redirect = RedirectResponse(url=google_url, status_code=status.HTTP_302_FOUND)
        redirect.set_cookie(
            key="oauth_state",
            value=state,
            httponly=True,
            secure=settings.auth.session_cookie_secure,
            samesite="lax",
            max_age=600,
        )
        if next and _is_safe_redirect(next):
            redirect.set_cookie(
                key="oauth_next",
                value=next,
                httponly=True,
                secure=settings.auth.session_cookie_secure,
                samesite="lax",
                max_age=600,
            )
        return redirect

    @api_router.get(
        "/auth/google/callback",
        name="api_auth_google_callback",
        tags=["auth"],
        response_class=RedirectResponse,
    )
    async def auth_google_callback(
        request: Request,
        code: str | None = Query(None),
        state: str | None = Query(None),
        error: str | None = Query(None),
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> RedirectResponse:
        login_url = str(request.url_for("render_auth_login"))

        if error:
            return RedirectResponse(
                url=f"{login_url}?oauth_error={error}",
                status_code=status.HTTP_302_FOUND,
            )

        if not code or not state:
            return RedirectResponse(
                url=f"{login_url}?oauth_error=missing_params",
                status_code=status.HTTP_302_FOUND,
            )

        if not settings.google_client_id or not settings.google_client_secret:
            return RedirectResponse(
                url=f"{login_url}?oauth_error=not_configured",
                status_code=status.HTTP_302_FOUND,
            )

        cookie_state = request.cookies.get("oauth_state")
        if not cookie_state or not state or not secrets.compare_digest(cookie_state, state):
            return RedirectResponse(
                url=f"{login_url}?oauth_error=invalid_state",
                status_code=status.HTTP_302_FOUND,
            )

        redirect_uri = f"{settings.auth.public_app_url.rstrip('/')}/api/v1/auth/google/callback"

        try:
            async with httpx.AsyncClient() as client:
                token_resp = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "code": code,
                        "client_id": settings.google_client_id,
                        "client_secret": settings.google_client_secret,
                        "redirect_uri": redirect_uri,
                        "grant_type": "authorization_code",
                    },
                )
                if token_resp.status_code != 200:
                    return RedirectResponse(
                        url=f"{login_url}?oauth_error=token_exchange_failed",
                        status_code=status.HTTP_302_FOUND,
                    )
                token_data = token_resp.json()

                access_token_google: str = token_data.get("access_token", "")
                refresh_token_google: str | None = token_data.get("refresh_token")
                expires_in_google: int | None = token_data.get("expires_in")
                google_expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=expires_in_google)
                    if expires_in_google
                    else None
                )

                profile_resp = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {access_token_google}"},
                )
                if profile_resp.status_code != 200:
                    return RedirectResponse(
                        url=f"{login_url}?oauth_error=profile_fetch_failed",
                        status_code=status.HTTP_302_FOUND,
                    )
                profile_data = profile_resp.json()
        except Exception:
            return RedirectResponse(
                url=f"{login_url}?oauth_error=google_unreachable",
                status_code=status.HTTP_302_FOUND,
            )

        google_sub: str = profile_data.get("sub", "")
        google_email: str = profile_data.get("email", "")
        google_name: str = profile_data.get("name", "") or google_email.split("@")[0]

        if not google_sub or not google_email:
            return RedirectResponse(
                url=f"{login_url}?oauth_error=incomplete_profile",
                status_code=status.HTTP_302_FOUND,
            )

        context = _build_request_context(request)
        try:
            result = await account_service.oauth_login_or_signup(
                session,
                provider=account_models.OAuthProvider.GOOGLE,
                provider_user_id=google_sub,
                email=google_email,
                full_name=google_name,
                access_token=access_token_google,
                refresh_token=refresh_token_google,
                expires_at=google_expires_at,
                context=context,
            )
        except InvalidCredentials:
            return RedirectResponse(
                url=f"{login_url}?oauth_error=account_locked",
                status_code=status.HTTP_302_FOUND,
            )

        next_url = request.cookies.get("oauth_next") or "/app/overview"
        if not _is_safe_redirect(next_url):
            next_url = "/app/overview"

        redirect = RedirectResponse(url=next_url, status_code=status.HTTP_302_FOUND)
        _set_refresh_cookie(redirect, settings, result.refresh_token)
        redirect.delete_cookie("oauth_state")
        redirect.delete_cookie("oauth_next")
        return redirect

    @api_router.get(
        "/auth/me",
        response_model=models.UserProfile,
        name="api_auth_profile",
        tags=["auth"],
    )
    async def auth_me(
        account: account_models.Account = Depends(_current_account),
        account_service: AccountService = account_service_dependency,
    ) -> models.UserProfile:
        profile_payload = account_service.build_profile(account)
        return models.UserProfile.model_validate(profile_payload)

    @api_router.patch(
        "/auth/profile",
        response_model=models.UserProfile,
        name="api_auth_update_profile",
        tags=["auth"],
    )
    async def auth_update_profile(
        payload: models.ProfileUpdateRequest,
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.UserProfile:
        try:
            updated = await account_service.update_profile(
                session,
                account=account,
                email=payload.email,
                full_name=payload.full_name,
                job_title=payload.job_title,
                organization_name=payload.organization_name,
                organization_size=payload.organization_size,
                use_case=payload.use_case,
                no_company=payload.no_company,
            )
        except AccountAlreadyExists as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="account_exists") from exc
        profile_payload = account_service.build_profile(updated)
        return models.UserProfile.model_validate(profile_payload)

    @api_router.get(
        "/keys",
        response_model=models.ApiKeyListResponse,
        name="api_list_api_keys",
        tags=["api_keys"],
    )
    async def list_api_keys(
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.ApiKeyListResponse:
        plan = api_key_service.get_plan_for_account(account)
        monthly_usage_total = await _load_monthly_usage(session, account)
        keys = await api_key_service.list_keys_for_account(session, account_id=account.id)
        items: list[dict[str, object]] = []
        for key in keys:
            usage = await api_key_service.fetch_usage_snapshot(session, key)
            plan_for_key, limits = api_key_service.derive_plan_and_limits(key, account)
            items.append(_build_api_key_payload(key, plan=plan_for_key, limits=limits, usage=usage))
        max_keys_allowed = plan.max_keys or settings.api_keys.max_keys_per_account
        allowed_roles = list(plan.roles or (plan.default_role,))
        plan_limits = models.ApiKeyPlanLimits(
            daily_quota=plan.daily_quota,
            monthly_quota=plan.monthly_quota,
            burst_per_second=plan.burst_per_second,
            burst_per_minute=plan.burst_per_minute,
            data_latency_seconds=plan.data_latency_seconds,
        )
        return models.ApiKeyListResponse(
            keys=items,
            plan_code=plan.code,
            max_keys=max_keys_allowed,
            allowed_roles=allowed_roles,
            plan_limits=plan_limits,
            monthly_usage_total=monthly_usage_total,
        )

    @api_router.post(
        "/keys",
        response_model=models.ApiKeyMaterializedResponse,
        status_code=status.HTTP_201_CREATED,
        name="api_create_api_key",
        tags=["api_keys"],
    )
    async def create_api_key(
        payload: models.ApiKeyCreateRequest,
        request: Request,
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> models.ApiKeyMaterializedResponse:
        try:
            issued = await api_key_service.issue_key(
                session,
                account,
                label=payload.label,
                role=payload.role,
                tags=payload.tags or [],
                created_by=account.email,
                application_name=payload.application_name,
                ip_allowlist=payload.ip_allowlist,
                label_constraints=payload.label_constraints,
                actor_ip=_extract_request_ip(request),
            )
        except ApiKeyLimitReached as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="max_keys_reached") from exc

        plan, limits = api_key_service.derive_plan_and_limits(issued.api_key, account)
        usage = await api_key_service.fetch_usage_snapshot(session, issued.api_key)
        body = _build_api_key_payload(issued.api_key, plan=plan, limits=limits, usage=usage)
        return models.ApiKeyMaterializedResponse(key=body, secret=issued.secret)

    @api_router.post(
        "/keys/{key_id}/rotate",
        response_model=models.ApiKeyMaterializedResponse,
        name="api_rotate_api_key",
        tags=["api_keys"],
    )
    async def rotate_api_key(
        key_id: str,
        request: Request,
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> models.ApiKeyMaterializedResponse:
        key_uuid = _parse_uuid_or_400(key_id, "api_key_id")
        try:
            api_key = await api_key_service.get_key(session, account_id=account.id, key_id=key_uuid)
        except ApiKeyNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="api_key_not_found") from exc

        rotated = await api_key_service.rotate_key(
            session,
            api_key,
            rotated_by=account.email,
            actor_ip=_extract_request_ip(request),
        )
        plan, limits = api_key_service.derive_plan_and_limits(rotated.api_key, account)
        usage = await api_key_service.fetch_usage_snapshot(session, rotated.api_key)
        body = _build_api_key_payload(rotated.api_key, plan=plan, limits=limits, usage=usage)
        return models.ApiKeyMaterializedResponse(key=body, secret=rotated.secret)

    @api_router.patch(
        "/keys/{key_id}",
        response_model=models.ApiKeyModel,
        name="api_update_api_key",
        tags=["api_keys"],
    )
    async def update_api_key(
        key_id: str,
        payload: models.ApiKeyUpdateRequest,
        request: Request,
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> models.ApiKeyModel:
        key_uuid = _parse_uuid_or_400(key_id, "api_key_id")
        try:
            api_key = await api_key_service.get_key(session, account_id=account.id, key_id=key_uuid)
        except ApiKeyNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="api_key_not_found") from exc

        try:
            updated = await api_key_service.update_key(
                session,
                api_key,
                account=account,
                label=payload.label,
                application_name=payload.application_name,
                tags=payload.tags,
                role=payload.role,
                ip_allowlist=payload.ip_allowlist,
                label_constraints=payload.label_constraints,
                updated_by=account.email,
                actor_ip=_extract_request_ip(request),
            )
        except ApiKeyRestrictionError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        plan, limits = api_key_service.derive_plan_and_limits(updated, account)
        usage = await api_key_service.fetch_usage_snapshot(session, updated)
        payload_dict = _build_api_key_payload(updated, plan=plan, limits=limits, usage=usage)
        return models.ApiKeyModel.model_validate(payload_dict)

    @api_router.post(
        "/keys/{key_id}/revoke",
        response_model=models.ApiKeyModel,
        name="api_revoke_api_key",
        tags=["api_keys"],
    )
    async def revoke_api_key(
        key_id: str,
        payload: models.ApiKeyRevokeRequest,
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> models.ApiKeyModel:
        key_uuid = _parse_uuid_or_400(key_id, "api_key_id")
        try:
            api_key = await api_key_service.get_key(session, account_id=account.id, key_id=key_uuid)
        except ApiKeyNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="api_key_not_found") from exc

        if api_key.status == account_models.ApiKeyStatus.REVOKED:
            plan, limits = api_key_service.derive_plan_and_limits(api_key, account)
            usage = await api_key_service.fetch_usage_snapshot(session, api_key)
            payload_dict = _build_api_key_payload(api_key, plan=plan, limits=limits, usage=usage)
            await api_key_service.delete_key(
                session,
                api_key,
                deleted_by=account.email,
                reason=payload.reason,
            )
            return models.ApiKeyModel.model_validate(payload_dict)

        revoked = await api_key_service.revoke_key(
            session,
            api_key,
            revoked_by=account.email,
            reason=payload.reason,
        )
        plan_after, limits_after = api_key_service.derive_plan_and_limits(revoked, account)
        usage_after = await api_key_service.fetch_usage_snapshot(session, revoked)
        payload_dict = _build_api_key_payload(revoked, plan=plan_after, limits=limits_after, usage=usage_after)
        return models.ApiKeyModel.model_validate(payload_dict)

    @api_router.get(
        "/keys/{key_id}/activity",
        response_model=models.ApiKeyActivityListResponse,
        name="api_list_api_key_activity",
        tags=["api_keys"],
    )
    async def list_api_key_activity(
        key_id: str,
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> models.ApiKeyActivityListResponse:
        key_uuid = _parse_uuid_or_400(key_id, "api_key_id")
        try:
            api_key = await api_key_service.get_key(session, account_id=account.id, key_id=key_uuid)
        except ApiKeyNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="api_key_not_found") from exc

        events = await api_key_service.list_audit_events(session, api_key.id, limit=80)
        items = [
            models.ApiKeyActivityModel(
                id=str(event.id),
                event_type=event.event_type.value,
                actor=event.actor,
                actor_ip=event.actor_ip,
                description=event.description,
                created_at=event.created_at,
                metadata=event.payload or {},
            )
            for event in events
        ]
        return models.ApiKeyActivityListResponse(events=items)

    @api_router.get(
        "/billing/status",
        response_model=models.BillingStatusResponse,
        name="api_billing_status",
        tags=["billing"],
    )
    async def billing_status(
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.BillingStatusResponse:
        await account_service.enforce_single_active_subscription(session, account)
        profile_payload = account_service.build_profile(account)
        subscription_payload = profile_payload.get("subscription") if isinstance(profile_payload, dict) else None
        subscription = (
            models.SubscriptionProfile.model_validate(subscription_payload)
            if subscription_payload
            else None
        )
        email_verified = bool(profile_payload.get("email_verified_at")) if isinstance(profile_payload, dict) else False
        account_status = profile_payload.get("status") if isinstance(profile_payload, dict) else None
        return models.BillingStatusResponse(
            subscription=subscription,
            account_status=account_status,
            email_verified=email_verified,
        )

    @api_router.post(
        "/billing/checkout/crypto",
        response_model=models.BillingCryptoCheckoutResponse,
        name="api_billing_checkout_crypto",
        tags=["billing"],
    )
    async def billing_checkout_crypto(
        payload: models.BillingCheckoutRequest,
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        billing_service: BillingService = billing_service_dependency,
    ) -> models.BillingCryptoCheckoutResponse:
        if billing_service.config.provider != "crypto":
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="crypto_billing_disabled")
        try:
            checkout_session = await billing_service.create_checkout_session(
                session,
                account,
                plan_code=payload.plan_code,
            )
        except BillingPlanNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="plan_not_found") from exc
        except BillingConfigurationError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="billing_not_configured") from exc
        except BillingError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return models.BillingCryptoCheckoutResponse(
            hosted_url=checkout_session.url,
            invoice_id=checkout_session.session_id,
        )

    @api_router.post(
        "/billing/cancel/crypto",
        response_model=models.BillingCancelResponse,
        name="api_billing_cancel_crypto",
        tags=["billing"],
    )
    async def billing_cancel_crypto(
        payload: models.BillingCancelRequest | None,
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        billing_service: BillingService = billing_service_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.BillingCancelResponse:
        if billing_service.config.provider != "crypto":
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="crypto_billing_disabled")
        try:
            if payload and payload.resume:
                subscription, expired_invoices = await billing_service.resume_crypto_subscription(
                    session,
                    account=account,
                    plan_code=payload.plan_code,
                )
            else:
                subscription, expired_invoices = await billing_service.cancel_crypto_subscription(
                    session,
                    account=account,
                    plan_code=payload.plan_code if payload else None,
                )
        except BillingError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        refreshed = await account_service.get_account_profile(session, account_id=account.id)
        profile_payload = account_service.build_profile(refreshed)
        subscription_payload = profile_payload.get("subscription") if isinstance(profile_payload, dict) else None
        subscription_profile = (
            models.SubscriptionProfile.model_validate(subscription_payload)
            if subscription_payload
            else None
        )
        if not subscription_profile and subscription:
            subscription_profile = models.SubscriptionProfile(
                plan_code=subscription.plan_code,
                status=subscription.status.value,
                currency=subscription.currency,
                unit_amount_cents=subscription.unit_amount_cents,
                interval=subscription.interval,
                price_id=subscription.price_id,
                current_period_start=subscription.current_period_start,
                current_period_end=subscription.current_period_end,
                cancel_at_period_end=subscription.cancel_at_period_end,
                latest_invoice_id=subscription.latest_invoice_id,
                hosted_checkout_url=subscription.hosted_checkout_url,
            )
        return models.BillingCancelResponse(
            subscription=subscription_profile,
            expired_invoice_ids=expired_invoices,
        )

    @api_router.post(
        "/billing/checkout",
        response_model=models.BillingCheckoutResponse,
        name="api_billing_checkout",
        tags=["billing"],
    )
    async def billing_checkout(
        payload: models.BillingCheckoutRequest,
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        billing_service: BillingService = billing_service_dependency,
    ) -> models.BillingCheckoutResponse:
        if billing_service.config.provider != "stripe":
            raise HTTPException(status.HTTP_410_GONE, detail="stripe_checkout_disabled")
        try:
            checkout_session = await billing_service.create_checkout_session(
                session,
                account,
                plan_code=payload.plan_code,
            )
        except BillingPlanNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="plan_not_found") from exc
        except BillingConfigurationError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="billing_not_configured") from exc
        return models.BillingCheckoutResponse(
            checkout_url=checkout_session.url,
            session_id=checkout_session.session_id,
        )

    @api_router.post(
        "/billing/portal",
        response_model=models.BillingPortalResponse,
        name="api_billing_portal",
        tags=["billing"],
    )
    async def billing_portal(
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        billing_service: BillingService = billing_service_dependency,
    ) -> models.BillingPortalResponse:
        if billing_service.config.provider != "stripe":
            raise HTTPException(status.HTTP_410_GONE, detail="stripe_portal_disabled")
        try:
            portal_url = await billing_service.create_customer_portal_session(session, account)
        except BillingConfigurationError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="billing_not_configured") from exc
        return models.BillingPortalResponse(portal_url=portal_url)

    @api_router.get(
        "/usage/summary",
        response_model=models.UsageSummaryResponse,
        name="api_usage_summary",
        tags=["usage"],
    )
    async def usage_summary(
        window_days: int = Query(30, ge=7, le=90),
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> models.UsageSummaryResponse:
        points_raw, totals = await _collect_usage_series(session, account, window_days=window_days)
        monthly_usage = await _load_monthly_usage(session, account)
        plan = _resolve_account_plan(account, api_key_service)
        average = totals["total_calls"] / window_days if window_days else 0.0
        points = [
            models.UsageSeriesPoint(
                date=item["date"],
                call_count=item["call_count"],
                error_count=item["error_count"],
            )
            for item in points_raw
        ]
        totals_payload = models.UsageTotals(
            total_calls=totals["total_calls"],
            average_per_day=round(average, 2),
            max_calls=totals["max_calls"],
            total_errors=totals["total_errors"],
            monthly_usage=monthly_usage,
            monthly_quota=plan.monthly_quota,
            plan_code=plan.code,
        )
        return models.UsageSummaryResponse(window_days=window_days, points=points, totals=totals_payload)

    @api_router.get(
        "/usage/errors",
        response_model=models.UsageErrorsResponse,
        name="api_usage_errors",
        tags=["usage"],
    )
    async def usage_errors(
        window_days: int = Query(30, ge=7, le=90),
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
    ) -> models.UsageErrorsResponse:
        start_ts = datetime.combine(
            date.today() - timedelta(days=max(window_days, 1) - 1),
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        stmt = (
            select(
                account_models.ApiUsageEvent.route_path,
                account_models.ApiUsageEvent.route_name,
                account_models.ApiUsageEvent.status_code,
                account_models.ApiUsageEvent.error_code,
                func.count().label("occurrences"),
                func.max(account_models.ApiUsageEvent.created_at).label("last_seen"),
            )
            .where(
                account_models.ApiUsageEvent.account_id == account.id,
                account_models.ApiUsageEvent.created_at >= start_ts,
                account_models.ApiUsageEvent.status_code >= 400,
            )
            .group_by(
                account_models.ApiUsageEvent.route_path,
                account_models.ApiUsageEvent.route_name,
                account_models.ApiUsageEvent.status_code,
                account_models.ApiUsageEvent.error_code,
            )
            .order_by(func.count().desc())
            .limit(25)
        )
        rows = await session.execute(stmt)
        errors = [
            models.UsageErrorEntry(
                route_path=row.route_path,
                route_name=row.route_name,
                status_code=row.status_code,
                error_code=row.error_code,
                occurrences=row.occurrences,
                last_seen=row.last_seen,
            )
            for row in rows
        ]
        return models.UsageErrorsResponse(window_days=window_days, errors=errors)

    @api_router.get(
        "/usage/export",
        name="api_usage_export",
        tags=["usage"],
    )
    async def export_usage(
        window_days: int = Query(90, ge=7, le=180),
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
    ) -> StreamingResponse:
        points_raw, _ = await _collect_usage_series(session, account, window_days=window_days)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["date", "call_count", "error_count"])
        for point in points_raw:
            writer.writerow(
                [
                    point["date"].isoformat(),
                    point["call_count"],
                    point["error_count"],
                ]
            )
        csv_payload = buffer.getvalue().encode("utf-8")
        filename = f"usage_{window_days}d.csv"
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return StreamingResponse(iter([csv_payload]), media_type="text/csv", headers=headers)

    @api_router.get(
        "/usage/alerts",
        response_model=models.UsageAlertListResponse,
        name="api_usage_alerts",
        tags=["usage"],
    )
    async def list_usage_alerts(
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
    ) -> models.UsageAlertListResponse:
        rules = await _list_usage_alerts(session, account)
        return models.UsageAlertListResponse(
            alerts=[
                models.UsageAlertChannel(
                    id=str(rule.id),
                    channel_type=rule.channel_type.value,
                    destination=rule.destination,
                    label=rule.label,
                    threshold_percent=rule.threshold_percent,
                    enabled=rule.enabled,
                    last_triggered_at=rule.last_triggered_at,
                )
                for rule in rules
            ]
        )

    @api_router.put(
        "/usage/alerts",
        response_model=models.UsageAlertListResponse,
        name="api_usage_alerts_upsert",
        tags=["usage"],
    )
    async def upsert_usage_alerts(
        payload: models.UsageAlertUpdateRequest,
        account: account_models.Account = Depends(_current_account),
        session: AsyncSession = db_session_dependency,
    ) -> models.UsageAlertListResponse:
        if len(payload.alerts) > 10:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="too_many_alerts")
        await session.execute(
            delete(account_models.UsageAlertRule).where(account_models.UsageAlertRule.account_id == account.id)
        )
        for alert in payload.alerts:
            channel = account_models.ChannelType(alert.channel_type)
            rule = account_models.UsageAlertRule(
                account_id=account.id,
                channel_type=channel,
                destination=alert.destination.strip(),
                label=alert.label,
                threshold_percent=alert.threshold_percent,
                enabled=alert.enabled,
            )
            session.add(rule)
        await session.commit()
        rules = await _list_usage_alerts(session, account)
        return models.UsageAlertListResponse(
            alerts=[
                models.UsageAlertChannel(
                    id=str(rule.id),
                    channel_type=rule.channel_type.value,
                    destination=rule.destination,
                    label=rule.label,
                    threshold_percent=rule.threshold_percent,
                    enabled=rule.enabled,
                    last_triggered_at=rule.last_triggered_at,
                )
                for rule in rules
            ]
        )

    @api_router.post(
        "/billing/webhook/crypto",
        status_code=status.HTTP_204_NO_CONTENT,
        name="api_billing_webhook_crypto",
        tags=["billing"],
    )
    async def billing_webhook_crypto(
        request: Request,
        session: AsyncSession = db_session_dependency,
        billing_service: BillingService = billing_service_dependency,
    ) -> Response:
        if billing_service.config.provider != "crypto":
            raise HTTPException(status.HTTP_410_GONE, detail="crypto_billing_disabled")
        payload_bytes = await request.body()
        signature = (
            request.headers.get("x-nowpayments-sig")
            or request.headers.get("x-nowpayments-signature")
            or request.headers.get("x-nowpayments-sig-sha512")
        )
        try:
            payload_text = payload_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_payload_encoding") from exc
        try:
            await billing_service.process_crypto_webhook(
                session,
                payload=payload_text,
                signature=signature,
            )
        except BillingConfigurationError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="billing_not_configured") from exc
        except BillingError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api_router.post(
        "/billing/webhook/stripe",
        status_code=status.HTTP_204_NO_CONTENT,
        name="api_billing_webhook_stripe",
        tags=["billing"],
    )
    async def billing_webhook_stripe(
        request: Request,
        session: AsyncSession = db_session_dependency,
        billing_service: BillingService = billing_service_dependency,
    ) -> Response:
        if billing_service.config.provider != "stripe":
            raise HTTPException(status.HTTP_410_GONE, detail="stripe_webhook_disabled")
        payload_bytes = await request.body()
        signature = request.headers.get("Stripe-Signature")
        try:
            await billing_service.process_stripe_webhook(
                session,
                payload=payload_bytes.decode("utf-8"),
                signature=signature,
            )
        except StripeWebhookError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except BillingConfigurationError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="billing_not_configured") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api_router.get(
        "/admin/accounts",
        response_model=list[models.UserProfile],
        name="admin_list_accounts",
        tags=["admin"],
    )
    async def admin_list_accounts(
        _: str = Depends(admin_dependency),
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> list[models.UserProfile]:
        accounts = await account_service.list_accounts(session, limit=100)
        return [models.UserProfile.model_validate(account_service.build_profile(item)) for item in accounts]

    @api_router.post(
        "/admin/accounts/{account_id}/status",
        response_model=models.UserProfile,
        name="admin_update_account_status",
        tags=["admin"],
    )
    async def admin_update_account_status(
        account_id: str,
        payload: models.AdminUpdateStatusRequest,
        admin_username: str = Depends(admin_dependency),
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.UserProfile:
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_account_id") from exc

        account = await account_service.update_account_status(
            session,
            account_id=account_uuid,
            status_value=payload.status,
        )
        profile = account_service.build_profile(account)
        profile["last_updated_by"] = admin_username
        return models.UserProfile.model_validate(profile)

    @api_router.delete(
        "/admin/accounts/{account_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        name="admin_delete_account",
        tags=["admin"],
    )
    async def admin_delete_account(
        account_id: str,
        _: str = Depends(admin_dependency),
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> Response:
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_account_id") from exc

        try:
            await account_service.delete_account(
                session,
                account_id=account_uuid,
            )
        except AccountNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account_not_found") from exc

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api_router.post(
        "/admin/accounts/{account_id}/limits",
        response_model=models.UserProfile,
        name="admin_set_account_limits",
        tags=["admin"],
    )
    async def admin_set_account_limits(
        account_id: str,
        payload: models.AdminUpdateLimitsRequest,
        admin_username: str = Depends(admin_dependency),
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.UserProfile:
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_account_id") from exc

        account = await account_service.set_account_limits(
            session,
            account_id=account_uuid,
            daily_limit=payload.daily_call_limit,
            monthly_limit=payload.monthly_call_limit,
            notes=payload.notes,
            granted_by=admin_username,
        )
        profile = account_service.build_profile(account)
        profile["last_updated_by"] = admin_username
        return models.UserProfile.model_validate(profile)

    @api_router.post(
        "/admin/accounts/{account_id}/plan",
        response_model=models.UserProfile,
        name="admin_update_account_plan",
        tags=["admin"],
    )
    async def admin_update_account_plan(
        account_id: str,
        payload: models.AdminUpdatePlanRequest,
        admin_username: str = Depends(admin_dependency),
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
        billing_service: BillingService = billing_service_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.UserProfile:
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_account_id") from exc

        account = await account_service.get_account_profile(session, account_id=account_uuid)
        plan_code = payload.plan_code.lower()

        plan_settings = settings.api_keys.plans.get(plan_code)
        if plan_settings is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="plan_not_found")

        try:
            subscription_status = account_models.BillingSubscriptionStatus(payload.status)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_subscription_status") from exc

        subscription = None
        for candidate in account.billing_subscriptions or []:
            if candidate.plan_code == plan_code:
                subscription = candidate
                break

        if subscription is None:
            subscription = account_models.BillingSubscription(
                account_id=account.id,
                provider_subscription_id=f"manual_{plan_code}_{uuid.uuid4().hex[:8]}",
                plan_code=plan_code,
            )
        else:
            subscription.plan_code = plan_code

        billing_plan = billing_service.config.plans.get(plan_code) if billing_service.config.plans else None
        currency = (
            billing_plan.currency
            if billing_plan
            else account.billing_customer.currency
            if account.billing_customer
            else settings.billing.currency
        )

        subscription.status = subscription_status
        subscription.currency = currency
        subscription.price_id = billing_plan.price_id if billing_plan else subscription.price_id
        subscription.unit_amount_cents = (
            billing_plan.unit_amount_cents if billing_plan else subscription.unit_amount_cents
        )
        subscription.interval = billing_plan.interval if billing_plan else subscription.interval or "month"
        subscription.current_period_start = subscription.current_period_start or datetime.now(timezone.utc)
        subscription.current_period_end = payload.current_period_end
        subscription.cancel_at_period_end = False
        raw_data = dict(subscription.raw_data or {})
        raw_data["source"] = "admin_manual_update"
        raw_data["updated_by"] = admin_username
        subscription.raw_data = raw_data
        subscription.synced_at = datetime.now(timezone.utc)

        active_statuses = {
            account_models.BillingSubscriptionStatus.TRIALING,
            account_models.BillingSubscriptionStatus.ACTIVE,
            account_models.BillingSubscriptionStatus.PAST_DUE,
        }
        for candidate in account.billing_subscriptions or []:
            if candidate is subscription:
                continue
            if candidate.status in active_statuses:
                candidate.status = account_models.BillingSubscriptionStatus.CANCELED
                candidate.cancel_at_period_end = True
                candidate.synced_at = datetime.now(timezone.utc)
                session.add(candidate)
        if subscription not in (account.billing_subscriptions or []):
            account.billing_subscriptions.append(subscription)

        session.add(subscription)
        await session.commit()
        refreshed = await account_service.get_account_profile(session, account_id=account_uuid)
        profile = account_service.build_profile(refreshed)
        profile["last_updated_by"] = admin_username
        return models.UserProfile.model_validate(profile)

    @api_router.post(
        "/admin/accounts/{account_id}/roles",
        response_model=models.UserProfile,
        name="admin_update_account_roles",
        tags=["admin"],
    )
    async def admin_update_account_roles(
        account_id: str,
        payload: models.AdminUpdateRolesRequest,
        admin_username: str = Depends(admin_dependency),
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.UserProfile:
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_account_id") from exc

        try:
            account = await account_service.set_account_roles(
                session,
                account_id=account_uuid,
                roles=payload.roles,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        profile = account_service.build_profile(account)
        profile["last_updated_by"] = admin_username
        return models.UserProfile.model_validate(profile)

    @api_router.get(
        "/admin/api-keys",
        response_model=models.AdminApiKeyListResponse,
        name="admin_list_api_keys",
        tags=["admin"],
    )
    async def admin_list_api_keys(
        _: str = Depends(admin_dependency),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> models.AdminApiKeyListResponse:
        keys = await api_key_service.list_recent_keys(session, limit=100)
        items: list[dict[str, object]] = []
        for api_key in keys:
            owner = api_key.account
            plan, limits = api_key_service.derive_plan_and_limits(api_key, owner)
            usage = await api_key_service.fetch_usage_snapshot(session, api_key)
            items.append(
                _build_api_key_payload(
                    api_key,
                    plan=plan,
                    limits=limits,
                    usage=usage,
                    include_owner=True,
                )
            )
        return models.AdminApiKeyListResponse(keys=items)

    @api_router.post(
        "/admin/api-keys/{key_id}/status",
        response_model=models.AdminApiKeyModel,
        name="admin_update_api_key_status",
        tags=["admin"],
    )
    async def admin_update_api_key_status(
        key_id: str,
        payload: models.ApiKeyStatusUpdateRequest,
        admin_username: str = Depends(admin_dependency),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> models.AdminApiKeyModel:
        key_uuid = _parse_uuid_or_400(key_id, "api_key_id")
        try:
            api_key = await api_key_service.get_key_global(session, key_uuid)
        except ApiKeyNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="api_key_not_found") from exc

        desired_status = account_models.ApiKeyStatus(payload.status)
        if desired_status == account_models.ApiKeyStatus.REVOKED:
            updated = await api_key_service.revoke_key(
                session,
                api_key,
                revoked_by=admin_username,
                reason=payload.reason,
            )
        else:
            updated = await api_key_service.update_status(
                session,
                api_key,
                status=desired_status,
                updated_by=admin_username,
                reason=payload.reason,
            )

        owner = updated.account
        plan, limits = api_key_service.derive_plan_and_limits(updated, owner)
        usage = await api_key_service.fetch_usage_snapshot(session, updated)
        payload_dict = _build_api_key_payload(
            updated,
            plan=plan,
            limits=limits,
            usage=usage,
            include_owner=True,
        )
        return models.AdminApiKeyModel.model_validate(payload_dict)

    @api_router.post(
        "/admin/billing/{account_id}/enterprise/invoice",
        response_model=models.BillingEnterpriseInvoiceResponse,
        name="admin_billing_enterprise_invoice",
        tags=["admin"],
    )
    async def admin_billing_enterprise_invoice(
        account_id: str,
        payload: models.BillingEnterpriseInvoiceRequest,
        admin_username: str = Depends(admin_dependency),
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
        billing_service: BillingService = billing_service_dependency,
    ) -> models.BillingEnterpriseInvoiceResponse:
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_account_id") from exc

        try:
            account = await account_service.get_account_profile(session, account_id=account_uuid)
        except AccountNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account_not_found") from exc

        try:
            invoice_payload = await billing_service.generate_enterprise_invoice(
                session,
                account,
                amount_cents=payload.amount_cents,
                memo=payload.memo,
                due_in_days=payload.due_in_days,
            )
        except (BillingPlanNotFound, BillingConfigurationError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="billing_not_configured") from exc

        logger.info(
            "admin_enterprise_invoice_created",
            extra={
                "account_id": str(account.id),
                "created_by": admin_username,
                "invoice_id": invoice_payload.get("invoice_id"),
            },
        )
        return models.BillingEnterpriseInvoiceResponse(
            invoice_id=str(invoice_payload.get("invoice_id")),
            hosted_invoice_url=invoice_payload.get("hosted_invoice_url"),
            due_date=(
                datetime.fromtimestamp(invoice_payload["due_date"], tz=timezone.utc)
                if invoice_payload.get("due_date")
                else None
            ),
        )

    @api_router.post(
        "/admin/billing/{account_id}/enterprise/extend",
        response_model=models.UserProfile,
        name="admin_billing_enterprise_extend",
        tags=["admin"],
    )
    async def admin_billing_enterprise_extend(
        account_id: str,
        payload: models.BillingEnterpriseExtendRequest,
        admin_username: str = Depends(admin_dependency),
        session: AsyncSession = db_session_dependency,
        account_service: AccountService = account_service_dependency,
        billing_service: BillingService = billing_service_dependency,
    ) -> models.UserProfile:
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_account_id") from exc

        try:
            account = await account_service.get_account_profile(session, account_id=account_uuid)
        except AccountNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="account_not_found") from exc

        try:
            await billing_service.extend_enterprise_subscription(
                session,
                account,
                additional_days=payload.additional_days,
                note=payload.note,
            )
        except BillingPlanNotFound as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="subscription_missing") from exc

        refreshed = await account_service.get_account_profile(session, account_id=account_uuid)
        profile = account_service.build_profile(refreshed)
        profile["last_updated_by"] = admin_username
        return models.UserProfile.model_validate(profile)

    @api_router.get(
        "/admin/cta-analytics/dashboard/summary",
        response_model=models.AdminCtaSummaryResponse,
        name="admin_cta_dashboard_summary",
        tags=["admin"],
    )
    async def admin_cta_dashboard_summary(
        _: str = Depends(admin_dependency),
        start_at: datetime | None = Query(default=None),
        end_at: datetime | None = Query(default=None),
        lookback_days: int = Query(7, ge=1, le=90),
        page_filter: list[str] | None = Query(default=None, alias="page"),
        placement: list[str] | None = Query(default=None),
        cta_id: list[str] | None = Query(default=None),
        cta_type: list[str] | None = Query(default=None),
        cta_format: list[str] | None = Query(default=None),
        utm_source: list[str] | None = Query(default=None),
        traffic_source: list[str] | None = Query(default=None),
        auth_state: list[str] | None = Query(default=None),
        referrer: list[str] | None = Query(default=None),
        utm: list[str] | None = Query(default=None),
        session: AsyncSession = db_session_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.AdminCtaSummaryResponse:
        dashboard = await _load_admin_cta_dashboard(
            session=session,
            settings=settings,
            start_at=start_at,
            end_at=end_at,
            lookback_days=lookback_days,
            cta_ids=cta_id,
            cta_types=cta_type,
            cta_formats=cta_format,
            placements=placement,
            utm_sources=utm_source,
            traffic_sources=traffic_source,
            pages=page_filter,
            auth_states=auth_state,
            referrers=referrer,
            utm_values=utm,
            interval="day",
            breakdown_limit=1,
            top_limit=1,
        )
        payload = dict(dashboard["kpi"])
        payload["generated_at"] = dashboard["generated_at"]
        return models.AdminCtaSummaryResponse.model_validate(payload)

    @api_router.get(
        "/admin/cta-analytics/timeseries",
        response_model=models.AdminCtaTimeseriesResponse,
        name="admin_cta_timeseries",
        tags=["admin"],
    )
    async def admin_cta_timeseries(
        _: str = Depends(admin_dependency),
        start_at: datetime | None = Query(default=None),
        end_at: datetime | None = Query(default=None),
        lookback_days: int = Query(7, ge=1, le=90),
        interval: Literal["day", "hour"] = Query("day"),
        page_number: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=500),
        page_filter: list[str] | None = Query(default=None, alias="page"),
        placement: list[str] | None = Query(default=None),
        cta_id: list[str] | None = Query(default=None),
        cta_type: list[str] | None = Query(default=None),
        cta_format: list[str] | None = Query(default=None),
        utm_source: list[str] | None = Query(default=None),
        traffic_source: list[str] | None = Query(default=None),
        auth_state: list[str] | None = Query(default=None),
        referrer: list[str] | None = Query(default=None),
        utm: list[str] | None = Query(default=None),
        session: AsyncSession = db_session_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.AdminCtaTimeseriesResponse:
        dashboard = await _load_admin_cta_dashboard(
            session=session,
            settings=settings,
            start_at=start_at,
            end_at=end_at,
            lookback_days=lookback_days,
            cta_ids=cta_id,
            cta_types=cta_type,
            cta_formats=cta_format,
            placements=placement,
            utm_sources=utm_source,
            traffic_sources=traffic_source,
            pages=page_filter,
            auth_states=auth_state,
            referrers=referrer,
            utm_values=utm,
            interval=interval,
            breakdown_limit=1,
            top_limit=1,
        )
        items, pagination = _paginate_cta_items(
            dashboard["timeseries"],
            page=page_number,
            page_size=page_size,
        )
        return models.AdminCtaTimeseriesResponse.model_validate(
            {
                "items": items,
                "interval": interval,
                "pagination": pagination,
                "generated_at": dashboard["generated_at"],
            }
        )

    @api_router.get(
        "/admin/cta-analytics/top-cta",
        response_model=models.AdminCtaTopResponse,
        name="admin_cta_top",
        tags=["admin"],
    )
    async def admin_cta_top(
        _: str = Depends(admin_dependency),
        start_at: datetime | None = Query(default=None),
        end_at: datetime | None = Query(default=None),
        lookback_days: int = Query(7, ge=1, le=90),
        page_number: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=500),
        page_filter: list[str] | None = Query(default=None, alias="page"),
        placement: list[str] | None = Query(default=None),
        cta_id: list[str] | None = Query(default=None),
        cta_type: list[str] | None = Query(default=None),
        cta_format: list[str] | None = Query(default=None),
        utm_source: list[str] | None = Query(default=None),
        traffic_source: list[str] | None = Query(default=None),
        auth_state: list[str] | None = Query(default=None),
        referrer: list[str] | None = Query(default=None),
        utm: list[str] | None = Query(default=None),
        session: AsyncSession = db_session_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.AdminCtaTopResponse:
        dashboard = await _load_admin_cta_dashboard(
            session=session,
            settings=settings,
            start_at=start_at,
            end_at=end_at,
            lookback_days=lookback_days,
            cta_ids=cta_id,
            cta_types=cta_type,
            cta_formats=cta_format,
            placements=placement,
            utm_sources=utm_source,
            traffic_sources=traffic_source,
            pages=page_filter,
            auth_states=auth_state,
            referrers=referrer,
            utm_values=utm,
            interval="day",
            breakdown_limit=1,
            top_limit=None,
        )
        items, pagination = _paginate_cta_items(
            dashboard["top_cta"],
            page=page_number,
            page_size=page_size,
        )
        return models.AdminCtaTopResponse.model_validate(
            {
                "items": items,
                "pagination": pagination,
                "generated_at": dashboard["generated_at"],
            }
        )

    @api_router.get(
        "/admin/cta-analytics/breakdown",
        response_model=models.AdminCtaBreakdownResponse,
        name="admin_cta_breakdown",
        tags=["admin"],
    )
    async def admin_cta_breakdown(
        _: str = Depends(admin_dependency),
        start_at: datetime | None = Query(default=None),
        end_at: datetime | None = Query(default=None),
        lookback_days: int = Query(7, ge=1, le=90),
        page_number: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=500),
        page_filter: list[str] | None = Query(default=None, alias="page"),
        placement: list[str] | None = Query(default=None),
        cta_id: list[str] | None = Query(default=None),
        cta_type: list[str] | None = Query(default=None),
        cta_format: list[str] | None = Query(default=None),
        utm_source: list[str] | None = Query(default=None),
        traffic_source: list[str] | None = Query(default=None),
        auth_state: list[str] | None = Query(default=None),
        referrer: list[str] | None = Query(default=None),
        utm: list[str] | None = Query(default=None),
        session: AsyncSession = db_session_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.AdminCtaBreakdownResponse:
        dashboard = await _load_admin_cta_dashboard(
            session=session,
            settings=settings,
            start_at=start_at,
            end_at=end_at,
            lookback_days=lookback_days,
            cta_ids=cta_id,
            cta_types=cta_type,
            cta_formats=cta_format,
            placements=placement,
            utm_sources=utm_source,
            traffic_sources=traffic_source,
            pages=page_filter,
            auth_states=auth_state,
            referrers=referrer,
            utm_values=utm,
            interval="day",
            breakdown_limit=None,
            top_limit=1,
        )
        items, pagination = _paginate_cta_items(
            dashboard["breakdown"],
            page=page_number,
            page_size=page_size,
        )
        return models.AdminCtaBreakdownResponse.model_validate(
            {
                "items": items,
                "pagination": pagination,
                "generated_at": dashboard["generated_at"],
            }
        )

    @api_router.get(
        "/admin/cta-analytics/funnel",
        response_model=models.AdminCtaFunnelResponse,
        name="admin_cta_funnel",
        tags=["admin"],
    )
    async def admin_cta_funnel(
        _: str = Depends(admin_dependency),
        start_at: datetime | None = Query(default=None),
        end_at: datetime | None = Query(default=None),
        lookback_days: int = Query(7, ge=1, le=90),
        page_filter: list[str] | None = Query(default=None, alias="page"),
        placement: list[str] | None = Query(default=None),
        cta_id: list[str] | None = Query(default=None),
        cta_type: list[str] | None = Query(default=None),
        cta_format: list[str] | None = Query(default=None),
        utm_source: list[str] | None = Query(default=None),
        traffic_source: list[str] | None = Query(default=None),
        auth_state: list[str] | None = Query(default=None),
        referrer: list[str] | None = Query(default=None),
        utm: list[str] | None = Query(default=None),
        session: AsyncSession = db_session_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.AdminCtaFunnelResponse:
        dashboard = await _load_admin_cta_dashboard(
            session=session,
            settings=settings,
            start_at=start_at,
            end_at=end_at,
            lookback_days=lookback_days,
            cta_ids=cta_id,
            cta_types=cta_type,
            cta_formats=cta_format,
            placements=placement,
            utm_sources=utm_source,
            traffic_sources=traffic_source,
            pages=page_filter,
            auth_states=auth_state,
            referrers=referrer,
            utm_values=utm,
            interval="day",
            breakdown_limit=1,
            top_limit=1,
        )
        kpi = dashboard["kpi"]
        return models.AdminCtaFunnelResponse.model_validate(
            {
                "period": kpi["period"],
                "total_clicks": kpi["total_clicks"],
                "unique_clicks": kpi["unique_clicks"],
                "conversion": kpi["conversion"],
                "rates": kpi["rates"],
                "attribution_coverage": kpi["attribution_coverage"],
                "attribution": kpi["attribution"],
                "generated_at": dashboard["generated_at"],
            }
        )

    @api_router.get(
        "/admin/cta-analytics/format-decisions",
        response_model=models.AdminCtaFormatDecisionsResponse,
        name="admin_cta_format_decisions",
        tags=["admin"],
    )
    async def admin_cta_format_decisions(
        _: str = Depends(admin_dependency),
        days: int = Query(7, ge=1, le=90),
        limit: int = Query(20, ge=1, le=100),
        settings: ServiceSettings = settings_dependency,
    ) -> models.AdminCtaFormatDecisionsResponse:
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=days)
        items = await run_in_threadpool(
            partial(
                cta_analytics_store.list_cta_format_optimization_decisions,
                settings,
                since=since,
                limit=limit,
            )
        )
        current_statuses = await run_in_threadpool(
            partial(cta_analytics_store.list_cta_format_statuses, settings)
        )
        return models.AdminCtaFormatDecisionsResponse.model_validate(
            {
                "period_days": days,
                "items": items,
                "current_statuses": current_statuses,
                "generated_at": now.isoformat(),
            }
        )

    @api_router.get(
        "/admin/cta-analytics/export",
        name="admin_cta_export",
        tags=["admin"],
    )
    async def admin_cta_export(
        _: str = Depends(admin_dependency),
        dataset: models.AdminCtaExportDataset = Query("breakdown"),
        start_at: datetime | None = Query(default=None),
        end_at: datetime | None = Query(default=None),
        lookback_days: int = Query(7, ge=1, le=90),
        interval: Literal["day", "hour"] = Query("day"),
        page_filter: list[str] | None = Query(default=None, alias="page"),
        placement: list[str] | None = Query(default=None),
        cta_id: list[str] | None = Query(default=None),
        cta_type: list[str] | None = Query(default=None),
        cta_format: list[str] | None = Query(default=None),
        utm_source: list[str] | None = Query(default=None),
        traffic_source: list[str] | None = Query(default=None),
        auth_state: list[str] | None = Query(default=None),
        referrer: list[str] | None = Query(default=None),
        utm: list[str] | None = Query(default=None),
        session: AsyncSession = db_session_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> StreamingResponse:
        dashboard = await _load_admin_cta_dashboard(
            session=session,
            settings=settings,
            start_at=start_at,
            end_at=end_at,
            lookback_days=lookback_days,
            cta_ids=cta_id,
            cta_types=cta_type,
            cta_formats=cta_format,
            placements=placement,
            utm_sources=utm_source,
            traffic_sources=traffic_source,
            pages=page_filter,
            auth_states=auth_state,
            referrers=referrer,
            utm_values=utm,
            interval=interval if dataset == "timeseries" else "day",
            breakdown_limit=None if dataset == "breakdown" else 1,
            top_limit=None if dataset == "top_cta" else 1,
        )
        generated_at = str(dashboard["generated_at"])
        kpi = dashboard["kpi"]
        conversion = dict(kpi["conversion"])
        rates = dict(kpi.get("rates") or {})
        attribution = dict(kpi.get("attribution") or {})
        observability = dict(kpi.get("observability") or {})
        service_state = dict(kpi.get("service_state") or {})
        last_accepted_event = dict(service_state.get("last_accepted_event") or {})
        last_aggregated_slot = dict(service_state.get("last_aggregated_slot") or {})
        rows: list[dict[str, object]]
        headers: list[str]

        if dataset == "summary":
            rows = [
                {
                    "start_at": kpi["period"]["start_at"],
                    "end_at": kpi["period"]["end_at"],
                    "lookback_days": kpi["period"]["lookback_days"],
                    "total_clicks": kpi["total_clicks"],
                    "unique_clicks": kpi["unique_clicks"],
                    "unique_users": kpi["unique_users"],
                    "unique_sessions": kpi["unique_sessions"],
                    "unique_anonymous": kpi["unique_anonymous"],
                    "click_users": conversion["click_users"],
                    "signup_users": conversion["signup_users"],
                    "confirmed_users": conversion["confirmed_users"],
                    "paid_users": conversion["paid_users"],
                    "click_to_signup": conversion["click_to_signup"],
                    "click_to_confirmed": conversion["click_to_confirmed"],
                    "signup_to_confirmed": conversion["signup_to_confirmed"],
                    "confirmed_to_paid": conversion["confirmed_to_paid"],
                    "signup_to_paid": conversion["signup_to_paid"],
                    "click_to_paid": conversion["click_to_paid"],
                    "ctr": rates.get("ctr", 0.0),
                    "signup_cr": rates.get("signup_cr", 0.0),
                    "confirm_cr": rates.get("confirm_cr", 0.0),
                    "paid_cr": rates.get("paid_cr", 0.0),
                    "attribution_coverage": kpi["attribution_coverage"],
                    "attribution_model": attribution.get("model", ""),
                    "attribution_lookback_days": attribution.get("lookback_days", 0),
                    "attribution_identity_priority": "|".join(attribution.get("identity_priority", [])),
                    "expected_slots": observability.get("expected_slots", 0),
                    "active_slots": observability.get("active_slots", 0),
                    "missing_slots": observability.get("missing_slots", 0),
                    "missing_ratio": observability.get("missing_ratio", 0.0),
                    "total_events": observability.get("total_events", 0),
                    "invalid_events": observability.get("invalid_events", 0),
                    "duplicate_events": observability.get("duplicate_events", 0),
                    "invalid_ratio": observability.get("invalid_ratio", 0.0),
                    "aggregation_lag_seconds": observability.get("aggregation_lag_seconds"),
                    "last_accepted_event_id": last_accepted_event.get("event_id"),
                    "last_accepted_event_at": last_accepted_event.get("received_at"),
                    "last_accepted_cta_id": last_accepted_event.get("cta_id"),
                    "last_accepted_location": last_accepted_event.get("location"),
                    "last_aggregated_hour": last_aggregated_slot.get("event_hour"),
                    "last_aggregated_date": last_aggregated_slot.get("event_date"),
                    "generated_at": generated_at,
                }
            ]
            headers = list(rows[0].keys())
        elif dataset == "funnel":
            rows = [
                {
                    "start_at": kpi["period"]["start_at"],
                    "end_at": kpi["period"]["end_at"],
                    "lookback_days": kpi["period"]["lookback_days"],
                    "click_users": conversion["click_users"],
                    "signup_users": conversion["signup_users"],
                    "confirmed_users": conversion["confirmed_users"],
                    "paid_users": conversion["paid_users"],
                    "click_to_signup": conversion["click_to_signup"],
                    "click_to_confirmed": conversion["click_to_confirmed"],
                    "signup_to_confirmed": conversion["signup_to_confirmed"],
                    "confirmed_to_paid": conversion["confirmed_to_paid"],
                    "signup_to_paid": conversion["signup_to_paid"],
                    "click_to_paid": conversion["click_to_paid"],
                    "ctr": rates.get("ctr", 0.0),
                    "signup_cr": rates.get("signup_cr", 0.0),
                    "confirm_cr": rates.get("confirm_cr", 0.0),
                    "paid_cr": rates.get("paid_cr", 0.0),
                    "attribution_coverage": kpi["attribution_coverage"],
                    "attribution_model": attribution.get("model", ""),
                    "attribution_lookback_days": attribution.get("lookback_days", 0),
                    "attribution_identity_priority": "|".join(attribution.get("identity_priority", [])),
                    "generated_at": generated_at,
                }
            ]
            headers = list(rows[0].keys())
        elif dataset == "timeseries":
            rows = []
            for point in dashboard["timeseries"]:
                funnel = dict(point["conversion"])
                point_rates = dict(point.get("rates") or {})
                point_attribution = dict(point.get("attribution") or {})
                rows.append(
                    {
                        "bucket": point["bucket"],
                        "total_clicks": point["total_clicks"],
                        "unique_clicks": point["unique_clicks"],
                        "unique_users": point["unique_users"],
                        "unique_sessions": point["unique_sessions"],
                        "click_users": funnel["click_users"],
                        "signup_users": funnel["signup_users"],
                        "confirmed_users": funnel["confirmed_users"],
                        "paid_users": funnel["paid_users"],
                        "click_to_signup": funnel["click_to_signup"],
                        "click_to_confirmed": funnel["click_to_confirmed"],
                        "signup_to_confirmed": funnel["signup_to_confirmed"],
                        "confirmed_to_paid": funnel["confirmed_to_paid"],
                        "signup_to_paid": funnel["signup_to_paid"],
                        "click_to_paid": funnel["click_to_paid"],
                        "ctr": point_rates.get("ctr", 0.0),
                        "signup_cr": point_rates.get("signup_cr", 0.0),
                        "confirm_cr": point_rates.get("confirm_cr", 0.0),
                        "paid_cr": point_rates.get("paid_cr", 0.0),
                        "attribution_coverage": point["attribution_coverage"],
                        "attribution_model": point_attribution.get("model", ""),
                        "attribution_lookback_days": point_attribution.get("lookback_days", 0),
                        "attribution_identity_priority": "|".join(point_attribution.get("identity_priority", [])),
                        "generated_at": generated_at,
                    }
                )
            headers = [
                "bucket",
                "total_clicks",
                "unique_clicks",
                "unique_users",
                "unique_sessions",
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
                "ctr",
                "signup_cr",
                "confirm_cr",
                "paid_cr",
                "attribution_coverage",
                "attribution_model",
                "attribution_lookback_days",
                "attribution_identity_priority",
                "generated_at",
            ]
        elif dataset == "top_cta":
            rows = []
            for item in dashboard["top_cta"]:
                funnel = dict(item["conversion"])
                item_rates = dict(item.get("rates") or {})
                item_attribution = dict(item.get("attribution") or {})
                rows.append(
                    {
                        "cta_id": item["cta_id"],
                        "total_clicks": item["total_clicks"],
                        "unique_clicks": item["unique_clicks"],
                        "unique_users": item["unique_users"],
                        "unique_sessions": item["unique_sessions"],
                        "click_users": funnel["click_users"],
                        "signup_users": funnel["signup_users"],
                        "confirmed_users": funnel["confirmed_users"],
                        "paid_users": funnel["paid_users"],
                        "click_to_signup": funnel["click_to_signup"],
                        "click_to_confirmed": funnel["click_to_confirmed"],
                        "signup_to_confirmed": funnel["signup_to_confirmed"],
                        "confirmed_to_paid": funnel["confirmed_to_paid"],
                        "signup_to_paid": funnel["signup_to_paid"],
                        "click_to_paid": funnel["click_to_paid"],
                        "ctr": item_rates.get("ctr", 0.0),
                        "signup_cr": item_rates.get("signup_cr", 0.0),
                        "confirm_cr": item_rates.get("confirm_cr", 0.0),
                        "paid_cr": item_rates.get("paid_cr", 0.0),
                        "attribution_coverage": item["attribution_coverage"],
                        "attribution_model": item_attribution.get("model", ""),
                        "attribution_lookback_days": item_attribution.get("lookback_days", 0),
                        "attribution_identity_priority": "|".join(item_attribution.get("identity_priority", [])),
                        "generated_at": generated_at,
                    }
                )
            headers = [
                "cta_id",
                "total_clicks",
                "unique_clicks",
                "unique_users",
                "unique_sessions",
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
                "ctr",
                "signup_cr",
                "confirm_cr",
                "paid_cr",
                "attribution_coverage",
                "attribution_model",
                "attribution_lookback_days",
                "attribution_identity_priority",
                "generated_at",
            ]
        else:
            rows = []
            for item in dashboard["breakdown"]:
                funnel = dict(item["conversion"])
                item_rates = dict(item.get("rates") or {})
                item_attribution = dict(item.get("attribution") or {})
                rows.append(
                    {
                        "cta_id": item["cta_id"],
                        "cta_format": item["cta_format"],
                        "location": item["location"],
                        "page_path": item["page_path"],
                        "utm_source": item["utm_source"],
                        "total_clicks": item["total_clicks"],
                        "unique_clicks": item["unique_clicks"],
                        "unique_users": item["unique_users"],
                        "unique_sessions": item["unique_sessions"],
                        "click_users": funnel["click_users"],
                        "signup_users": funnel["signup_users"],
                        "confirmed_users": funnel["confirmed_users"],
                        "paid_users": funnel["paid_users"],
                        "click_to_signup": funnel["click_to_signup"],
                        "click_to_confirmed": funnel["click_to_confirmed"],
                        "signup_to_confirmed": funnel["signup_to_confirmed"],
                        "confirmed_to_paid": funnel["confirmed_to_paid"],
                        "signup_to_paid": funnel["signup_to_paid"],
                        "click_to_paid": funnel["click_to_paid"],
                        "ctr": item_rates.get("ctr", 0.0),
                        "signup_cr": item_rates.get("signup_cr", 0.0),
                        "confirm_cr": item_rates.get("confirm_cr", 0.0),
                        "paid_cr": item_rates.get("paid_cr", 0.0),
                        "attribution_coverage": item["attribution_coverage"],
                        "attribution_model": item_attribution.get("model", ""),
                        "attribution_lookback_days": item_attribution.get("lookback_days", 0),
                        "attribution_identity_priority": "|".join(item_attribution.get("identity_priority", [])),
                        "generated_at": generated_at,
                    }
                )
            headers = [
                "cta_id",
                "cta_format",
                "location",
                "page_path",
                "utm_source",
                "total_clicks",
                "unique_clicks",
                "unique_users",
                "unique_sessions",
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
                "ctr",
                "signup_cr",
                "confirm_cr",
                "paid_cr",
                "attribution_coverage",
                "attribution_model",
                "attribution_lookback_days",
                "attribution_identity_priority",
                "generated_at",
            ]

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"cta_{dataset}_{timestamp}.csv"
        return _build_cta_csv_response(rows=rows, headers=headers, filename=filename)

    @api_router.get(
        "/health",
        response_model=models.HealthResponse,
        name="api_health_check",
        tags=["system"],
    )
    async def health_check(
        settings: ServiceSettings = settings_dependency,
    ) -> models.HealthResponse:
        uptime_seconds = max(time.monotonic() - START_TIME_MONOTONIC, 0.0)
        timestamp = datetime.now(timezone.utc)
        rate_limit_state = getattr(api_app.state, "rate_limit", {"limit": 0, "window_seconds": 0})
        data_paths = {
            "config": str(settings.config_path),
            "runs": str(settings.runs_root),
        }

        return models.HealthResponse(
            status="ok",
            version=api_app.version or "unknown",
            timestamp=timestamp,
            uptime_seconds=uptime_seconds,
            rate_limit={
                "limit": int(rate_limit_state.get("limit", 0)),
                "window_seconds": int(rate_limit_state.get("window_seconds", 0)),
            },
            data_paths=data_paths,
        )

    @api_router.get(
        "/performance",
        response_model=models.PerformanceResponse,
        name="api_get_performance",
        tags=["performance"],
    )
    async def get_performance(
        request: Request,
        response: Response,
        raw_api_key: str = Depends(_require_api_key_header),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.PerformanceResponse:
        context = await _authenticate_api_request(
            request=request,
            session=session,
            api_key_service=api_key_service,
            raw_key=raw_api_key,
        )
        try:
            bundle = load_performance_bundle(settings.runs_root)
            snapshots = _prepare_performance_snapshots(bundle)
            live_backtest_payload: dict[str, object] | None = None
            live_backtest_payloads_by_strategy: dict[str, dict[str, object] | None] = {}
            try:
                live_backtest_payloads_by_strategy = _build_live_backtest_payloads_by_strategy(
                    settings
                )
                live_backtest_payload = _select_live_backtest_payload_for_strategy(
                    live_backtest_payloads_by_strategy,
                    strategy_key=bundle.default_key,
                )
            except Exception as exc:  # noqa: BLE001 - performance endpoint should stay resilient
                logger.warning("live_backtest_payload_unavailable: %s", exc)
            await _record_api_usage(
                request,
                response,
                session,
                api_key_service,
                context,
            )
            return models.PerformanceResponse(
                default_key=bundle.default_key,
                snapshots=snapshots,
                live_backtest=live_backtest_payload,
                live_backtest_by_strategy=live_backtest_payloads_by_strategy,
            )
        except PerformanceSnapshotError as exc:
            logger.exception("performance_bundle_unavailable")
            await _record_usage_failure(
                request,
                response,
                session,
                api_key_service,
                context,
                HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="performance_unavailable",
                ),
            )
        except ValidationError as exc:
            logger.exception("performance_payload_validation_error")
            await _record_usage_failure(
                request,
                response,
                session,
                api_key_service,
                context,
                HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="performance_payload_invalid",
                ),
            )
        except Exception as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)

    @api_router.get(
        "/index-composition",
        response_model=models.IndexCompositionResponse,
        name="api_get_index_composition",
        tags=["performance"],
    )
    async def get_index_composition(
        request: Request,
        response: Response,
        raw_api_key: str = Depends(_require_api_key_header),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.IndexCompositionResponse:
        context = await _authenticate_api_request(
            request=request,
            session=session,
            api_key_service=api_key_service,
            raw_key=raw_api_key,
        )
        try:
            cutoff = _resolve_latency_cutoff(context)
            run_dir = await _find_latest_index_run_for_context(
                settings,
                session,
                context,
                before_timestamp=cutoff,
            )
            if run_dir is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no_runs_available")
            try:
                payload = _build_index_composition(settings, run_dir=run_dir)
            except IndexCompositionError as exc:
                logger.info("index_composition_missing", extra={"reason": str(exc)})
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            except (FileNotFoundError, ValueError) as exc:
                logger.exception("index_composition_load_failed")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=str(exc),
                ) from exc

            try:
                result = models.IndexCompositionResponse.model_validate(payload)
            except ValidationError as exc:
                logger.exception("index_composition_payload_invalid")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="index_composition_payload_invalid",
                ) from exc
            await _record_api_usage(
                request,
                response,
                session,
                api_key_service,
                context,
            )
            return result
        except HTTPException as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)
        except Exception as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)

    def _handle_demo_request_submission(
        payload: models.DemoRequest,
        settings: ServiceSettings,
        background_tasks: BackgroundTasks | None,
    ) -> models.DemoRequestResponse:
        record = payload.model_dump(exclude_none=True)
        request_id, received_at = intake_store.persist_demo_request(settings, record)
        if background_tasks is not None:
            background_tasks.add_task(
                email_notifications.send_demo_request_email,
                dict(record),
                request_id,
                received_at,
            )
        logger.info(
            "demo_request_saved",
            extra={
                "run_id": payload.run_id,
                "newsletter_opt_in": payload.newsletter_opt_in,
                "has_notes": bool(payload.notes),
            },
        )
        return models.DemoRequestResponse(request_id=request_id, received_at=received_at)

    def _handle_support_ticket_submission(
        payload: models.SupportTicket,
        settings: ServiceSettings,
        background_tasks: BackgroundTasks | None,
        account: account_models.Account | None = None,
    ) -> models.SupportTicketResponse:
        record = payload.model_dump(exclude_none=True)
        attachments = payload.attachments or []
        attachment_metadata: list[dict[str, object]] = []
        email_attachments: list[dict[str, object]] = []

        for attachment in attachments:
            try:
                raw_bytes = base64.b64decode(attachment.content_base64, validate=True)
            except Exception as exc:  # pragma: no cover - defensive decode
                logger.warning(
                    "support_ticket_attachment_decode_failed",
                    extra={"filename": getattr(attachment, "filename", None)},
                    exc_info=exc,
                )
                continue
            attachment_metadata.append(
                {
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "size_bytes": len(raw_bytes),
                }
            )
            email_attachments.append(
                {
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "data": raw_bytes,
                }
            )

        if attachment_metadata:
            record["attachments"] = attachment_metadata
        else:
            record.pop("attachments", None)

        if account is not None:
            record.setdefault("account_id", str(account.id))
            record.setdefault("account_email", account.email)
            record.setdefault("account_name", account.full_name)
        request_id, received_at = intake_store.persist_support_ticket(settings, record)
        if background_tasks is not None:
            background_tasks.add_task(
                email_notifications.send_support_ticket_email,
                dict(record),
                request_id,
                received_at,
                email_attachments,
            )
        logger.info(
            "support_ticket_saved",
            extra={
                "request_id": request_id,
                "account_id": str(account.id) if account else None,
            },
        )
        return models.SupportTicketResponse(request_id=request_id, received_at=received_at)

    @api_router.post(
        "/demo-request",
        response_model=models.DemoRequestResponse,
        status_code=status.HTTP_201_CREATED,
        name="api_submit_demo_request",
        tags=["intake"],
    )
    async def submit_demo_request(
        payload: models.DemoRequest,
        background_tasks: BackgroundTasks,
        settings: ServiceSettings = settings_dependency,
    ) -> models.DemoRequestResponse:
        return _handle_demo_request_submission(payload, settings, background_tasks)

    @api_router.post(
        "/intake/demo-request",
        response_model=models.DemoRequestResponse,
        status_code=status.HTTP_201_CREATED,
        name="api_submit_demo_request_legacy",
        include_in_schema=False,
        tags=["intake"],
    )
    async def submit_demo_request_legacy(
        payload: models.DemoRequest,
        background_tasks: BackgroundTasks,
        settings: ServiceSettings = settings_dependency,
    ) -> models.DemoRequestResponse:
        return _handle_demo_request_submission(payload, settings, background_tasks)

    @api_router.post(
        "/intake/registration",
        response_model=models.RegistrationResponse,
        status_code=status.HTTP_201_CREATED,
        name="api_submit_registration_request",
        tags=["intake"],
    )
    async def submit_registration_request(
        payload: models.RegistrationRequest,
        background_tasks: BackgroundTasks,
        settings: ServiceSettings = settings_dependency,
    ) -> models.RegistrationResponse:
        record = payload.model_dump(exclude_none=True)
        request_id, received_at = intake_store.persist_registration_request(settings, record)
        if background_tasks is not None:
            background_tasks.add_task(
                email_notifications.send_registration_request_email,
                dict(record),
                request_id,
                received_at,
            )
        return models.RegistrationResponse(request_id=request_id, received_at=received_at)

    @api_router.post(
        "/intake/api-key",
        response_model=models.ApiKeyRequestResponse,
        status_code=status.HTTP_201_CREATED,
        name="api_submit_api_key_request",
        tags=["intake"],
    )
    async def submit_api_key_request(
        payload: models.ApiKeyRequest,
        background_tasks: BackgroundTasks,
        settings: ServiceSettings = settings_dependency,
    ) -> models.ApiKeyRequestResponse:
        record = payload.model_dump(exclude_none=True)
        request_id, received_at = intake_store.persist_api_key_request(settings, record)
        if background_tasks is not None:
            background_tasks.add_task(
                email_notifications.send_api_request_email,
                dict(record),
                request_id,
                received_at,
            )
        return models.ApiKeyRequestResponse(request_id=request_id, received_at=received_at)

    @api_router.post(
        "/support/tickets",
        response_model=models.SupportTicketResponse,
        status_code=status.HTTP_201_CREATED,
        name="api_submit_support_ticket",
        tags=["intake"],
    )
    async def submit_support_ticket(
        payload: models.SupportTicket,
        request: Request,
        background_tasks: BackgroundTasks,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> models.SupportTicketResponse:
        account: account_models.Account | None = None
        refresh_token = request.cookies.get(settings.auth.session_cookie_name)
        if refresh_token:
            try:
                account = await account_service.get_account_by_refresh_token(
                    session,
                    refresh_token=refresh_token,
                )
            except SessionInvalid:
                account = None
            except AccountNotFound:
                account = None
        return _handle_support_ticket_submission(
            payload,
            settings,
            background_tasks,
            account=account,
        )

    async def _ingest_cta_event_record(
        *,
        settings: ServiceSettings,
        record: dict[str, object],
    ) -> models.CtaEventResponse:
        try:
            event_id, received_at = intake_store.persist_cta_event(settings, record)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "cta_ingestion_error",
                extra={
                    "reason": "raw_event_persist_failed",
                    "cta_id": str(record.get("cta_id") or "unknown"),
                    "location": str(record.get("location") or "unknown"),
                },
                exc_info=exc,
            )
            raise
        analytics_record, is_duplicate = _build_cta_analytics_record(
            record,
            event_id=event_id,
            received_at=received_at,
            dedup_scope=str(settings.runs_root),
        )
        if is_duplicate:
            try:
                await run_in_threadpool(
                    cta_analytics_store.record_cta_ingestion_quality,
                    settings,
                    event_id=event_id,
                    received_at=received_at,
                    status="duplicate",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "cta_observability_error",
                    extra={
                        "reason": "quality_metrics_persist_failed",
                        "status": "duplicate",
                        "event_id": event_id,
                    },
                    exc_info=exc,
                )
            return models.CtaEventResponse(event_id=event_id, received_at=received_at)

        try:
            intake_store.persist_cta_analytics_event(
                settings,
                analytics_record,
                event_id=event_id,
                received_at=received_at,
            )
        except Exception as exc:  # noqa: BLE001
            reason = f"analytics_backup_persist_failed:{exc.__class__.__name__}"
            logger.error(
                "cta_ingestion_error",
                extra={
                    "reason": reason,
                    "event_id": event_id,
                    "cta_id": str(analytics_record.get("cta_id") or "unknown"),
                    "location": str(analytics_record.get("location") or "unknown"),
                },
                exc_info=exc,
            )
            with suppress(Exception):
                await run_in_threadpool(
                    cta_analytics_store.record_cta_ingestion_quality,
                    settings,
                    event_id=event_id,
                    received_at=received_at,
                    status="invalid",
                    reason=reason,
                )
            raise

        try:
            inserted = await run_in_threadpool(
                cta_analytics_store.persist_cta_analytics_record,
                settings,
                analytics_record,
            )
        except Exception as exc:  # noqa: BLE001
            reason = f"aggregation_persist_failed:{exc.__class__.__name__}"
            logger.error(
                "cta_aggregation_error",
                extra={
                    "reason": reason,
                    "event_id": event_id,
                    "cta_id": str(analytics_record.get("cta_id") or "unknown"),
                    "location": str(analytics_record.get("location") or "unknown"),
                },
                exc_info=exc,
            )
            with suppress(Exception):
                await run_in_threadpool(
                    cta_analytics_store.record_cta_ingestion_quality,
                    settings,
                    event_id=event_id,
                    received_at=received_at,
                    status="invalid",
                    reason=reason,
                )
            return models.CtaEventResponse(event_id=event_id, received_at=received_at)

        if not inserted:
            reason = "aggregation_fact_ignored"
            logger.warning(
                "cta_aggregation_error",
                extra={
                    "reason": reason,
                    "event_id": event_id,
                    "cta_id": str(analytics_record.get("cta_id") or "unknown"),
                    "location": str(analytics_record.get("location") or "unknown"),
                },
            )
            with suppress(Exception):
                await run_in_threadpool(
                    cta_analytics_store.record_cta_ingestion_quality,
                    settings,
                    event_id=event_id,
                    received_at=received_at,
                    status="invalid",
                    reason=reason,
                )
            return models.CtaEventResponse(event_id=event_id, received_at=received_at)

        with suppress(Exception):
            await run_in_threadpool(
                cta_analytics_store.record_cta_ingestion_quality,
                settings,
                event_id=event_id,
                received_at=received_at,
                status="accepted",
            )
        return models.CtaEventResponse(event_id=event_id, received_at=received_at)

    async def _track_account_cta_bridge_event(
        *,
        settings: ServiceSettings,
        request: Request,
        account_id: uuid.UUID,
        event_type: str,
        cta_session_id: str | None = None,
        source_cta_id: str | None = None,
        source_page_path: str | None = None,
        source_scenario: str | None = None,
    ) -> None:
        metadata: dict[str, object] = {
            "account_id": str(account_id),
            "auth_state": "authenticated",
            "event_type": event_type,
            "page_path": source_page_path or "/",
            "placement": "signup_modal",
            "scenario": source_scenario or event_type,
        }
        if cta_session_id:
            metadata["session_id"] = cta_session_id
        if source_cta_id:
            metadata["source_cta_id"] = source_cta_id

        record: dict[str, object] = {
            "cta_id": source_cta_id or event_type,
            "event_type": event_type,
            "location": "signup_modal",
            "page_path": source_page_path or "/",
            "metadata": metadata,
            "user_agent": request.headers.get("user-agent"),
            "referer": request.headers.get("referer") or request.headers.get("referrer"),
        }
        if request.client:
            record["remote_ip"] = request.client.host
        await _ingest_cta_event_record(settings=settings, record=record)

    @api_router.post(
        "/events/cta",
        response_model=models.CtaEventResponse,
        status_code=status.HTTP_201_CREATED,
        name="api_track_cta_event",
        tags=["intake"],
    )
    async def track_cta_event(
        payload: models.CtaEvent,
        request: Request,
        settings: ServiceSettings = settings_dependency,
    ) -> models.CtaEventResponse:
        record = payload.model_dump(exclude_none=True)
        record["user_agent"] = request.headers.get("user-agent")
        record["referer"] = request.headers.get("referer") or request.headers.get("referrer")
        if request.client:
            record["remote_ip"] = request.client.host
        return await _ingest_cta_event_record(settings=settings, record=record)

    @api_router.post(
        "/run",
        response_model=models.RunResponse,
        status_code=status.HTTP_201_CREATED,
        name="api_trigger_run",
        tags=["pipeline"],
    )
    async def trigger_run(
        request: Request,
        response: Response,
        run_payload: models.RunRequest = Depends(_collect_run_query_overrides),
        raw_api_key: str = Depends(_require_api_key_header),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.RunResponse:
        if os.getenv("AICI_ENABLE_PIPELINE", "1").lower() not in _PIPELINE_ENABLED_FLAGS:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="pipeline_disabled",
            )

        context = await _authenticate_api_request(
            request=request,
            session=session,
            api_key_service=api_key_service,
            raw_key=raw_api_key,
        )
        run_payload = _apply_free_plan_run_limits(run_payload, context.plan)
        token_cost = _calculate_pipeline_token_cost(
            run_payload,
            custom_run_id=_has_custom_run_id(request),
        )
        await _record_api_usage(
            request,
            response,
            session,
            api_key_service,
            context,
            cost=token_cost,
            status_code=status.HTTP_201_CREATED,
        )
        try:
            run_id = run_payload.run_id or run_store.make_run_id()
            runner_kwargs = run_payload.model_dump(exclude_none=True)
            runner_kwargs["run_id"] = run_id
            runner_kwargs["config_path"] = settings.config_path
            _init_pipeline_progress(run_id)
            cancel_flag = _register_cancel_flag(run_id)

            def progress_callback(stage: str, status: str = "running", message: str | None = None) -> None:
                _update_pipeline_progress(run_id, stage=stage, status=status, message=message)

            runner_kwargs["cancel_event"] = cancel_flag
            runner_kwargs["progress_callback"] = progress_callback

            try:
                weights, perf = await run_in_threadpool(partial(run_monthly_update, **runner_kwargs))
                if isinstance(weights, dict) and isinstance(perf, dict):
                    _cache_pipeline_result(run_id, weights, perf)
                _update_pipeline_progress(run_id, state="done", message="Pipeline finished")
                await _persist_index_run_record(
                    settings,
                    run_id=run_id,
                    source=account_models.IndexRunSource.USER,
                    account_id=context.account.id,
                    api_key_id=context.api_key.id,
                    session=session,
                )
                _tag_user_run_metadata(
                    settings,
                    run_id,
                    account_id=context.account.id,
                    api_key_id=context.api_key.id,
                )
            except Exception as exc:  # pragma: no cover - propagate pipeline failure context
                if cancel_flag.is_set():
                    _update_pipeline_progress(run_id, state="cancelled", message="Pipeline cancelled")
                    raise HTTPException(status_code=499, detail="pipeline_cancelled") from exc
                _update_pipeline_progress(run_id, state="error", message=str(exc))
                raise HTTPException(status_code=500, detail=f"pipeline_failed: {exc}") from exc
            finally:
                _pop_cancel_flag(run_id)

            return models.RunResponse(run_id=run_id, weights=weights, perf=perf)
        except HTTPException as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)
        except Exception as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)

    @api_router.post(
        "/run/async",
        response_model=models.RunAcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
        name="api_trigger_run_async",
        tags=["pipeline"],
    )
    async def trigger_run_async(
        request: Request,
        response: Response,
        run_payload: models.RunRequest = Depends(_collect_run_query_overrides),
        raw_api_key: str = Depends(_require_api_key_header),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.RunAcceptedResponse:
        if os.getenv("AICI_ENABLE_PIPELINE", "1").lower() not in _PIPELINE_ENABLED_FLAGS:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="pipeline_disabled",
            )

        context = await _authenticate_api_request(
            request=request,
            session=session,
            api_key_service=api_key_service,
            raw_key=raw_api_key,
        )
        run_payload = _apply_free_plan_run_limits(run_payload, context.plan)
        token_cost = _calculate_pipeline_token_cost(
            run_payload,
            custom_run_id=_has_custom_run_id(request),
        )
        await _record_api_usage(
            request,
            response,
            session,
            api_key_service,
            context,
            cost=token_cost,
            status_code=status.HTTP_202_ACCEPTED,
        )

        try:
            run_id = run_payload.run_id or run_store.make_run_id()
            runner_kwargs = run_payload.model_dump(exclude_none=True)
            runner_kwargs["run_id"] = run_id
            runner_kwargs["config_path"] = settings.config_path
            _init_pipeline_progress(run_id)
            cancel_flag = _register_cancel_flag(run_id)

            def progress_callback(stage: str, status: str = "running", message: str | None = None) -> None:
                _update_pipeline_progress(run_id, stage=stage, status=status, message=message)

            runner_kwargs["cancel_event"] = cancel_flag
            runner_kwargs["progress_callback"] = progress_callback

            asyncio.create_task(
                _run_pipeline_background(
                    run_id,
                    runner_kwargs,
                    cancel_flag,
                    settings=settings,
                    account_id=context.account.id,
                    api_key_id=context.api_key.id,
                )
            )
            return models.RunAcceptedResponse(run_id=run_id, state="running")
        except HTTPException as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)
        except Exception as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)

    @api_router.get(
        "/runs/{run_id}/progress",
        response_model=models.RunProgressResponse,
        name="api_get_run_progress",
        tags=["pipeline"],
    )
    async def get_run_progress(
        request: Request,
        response: Response,
        run_id: str,
        raw_api_key: str = Depends(_require_api_key_header),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> models.RunProgressResponse:
        context = await _authenticate_api_request(
            request=request,
            session=session,
            api_key_service=api_key_service,
            raw_key=raw_api_key,
        )
        try:
            progress = _get_pipeline_progress(run_id)
            if progress is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
            await _record_api_usage(
                request,
                response,
                session,
                api_key_service,
                context,
                cost=_TOKEN_COST_RUN_STATUS,
            )
            return models.RunProgressResponse.model_validate(progress)
        except HTTPException as exc:
            await _record_usage_failure(
                request,
                response,
                session,
                api_key_service,
                context,
                exc,
                cost=_TOKEN_COST_RUN_STATUS,
            )
        except Exception as exc:
            await _record_usage_failure(
                request,
                response,
                session,
                api_key_service,
                context,
                exc,
                cost=_TOKEN_COST_RUN_STATUS,
            )

    @api_router.get(
        "/runs/{run_id}/result",
        response_model=models.RunResultCachedResponse,
        name="api_get_run_result_cached",
        tags=["pipeline"],
    )
    async def get_run_result_cached(
        request: Request,
        response: Response,
        run_id: str,
        raw_api_key: str = Depends(_require_api_key_header),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.RunResultCachedResponse:
        context = await _authenticate_api_request(
            request=request,
            session=session,
            api_key_service=api_key_service,
            raw_key=raw_api_key,
        )
        try:
            cached = _get_cached_pipeline_result(run_id)
            progress = _get_pipeline_progress(run_id)
            if cached is not None:
                await _record_api_usage(
                    request,
                    response,
                    session,
                    api_key_service,
                    context,
                    cost=_TOKEN_COST_RUN_READ,
                )
                return models.RunResultCachedResponse(
                    run_id=run_id,
                    state="done",
                    weights=cached.get("weights"),
                    perf=cached.get("perf"),
                    cached_at=cached.get("cached_at"),
                )

            if progress:
                state = str(progress.get("state", "pending"))
                if state == "done":
                    try:
                        cached_entry = _load_and_cache_run_result(settings, run_id)
                    except FileNotFoundError as exc:
                        raise HTTPException(status_code=404, detail=str(exc)) from exc
                    except ValueError as exc:
                        raise HTTPException(status_code=500, detail=str(exc)) from exc
                    await _record_api_usage(
                        request,
                        response,
                        session,
                        api_key_service,
                        context,
                        cost=_TOKEN_COST_RUN_READ,
                    )
                    return models.RunResultCachedResponse(
                        run_id=run_id,
                        state="done",
                        weights=cached_entry.get("weights"),
                        perf=cached_entry.get("perf"),
                        cached_at=cached_entry.get("cached_at"),
                    )

                if state in {"error", "cancelled"}:
                    await _record_api_usage(
                        request,
                        response,
                        session,
                        api_key_service,
                        context,
                        cost=_TOKEN_COST_RUN_STATUS,
                    )
                    return models.RunResultCachedResponse(run_id=run_id, state=state)

                await _record_api_usage(
                    request,
                    response,
                    session,
                    api_key_service,
                    context,
                    cost=_TOKEN_COST_RUN_STATUS,
                )
                return models.RunResultCachedResponse(run_id=run_id, state=state)

            try:
                cached_entry = _load_and_cache_run_result(settings, run_id)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            await _record_api_usage(
                request,
                response,
                session,
                api_key_service,
                context,
                cost=_TOKEN_COST_RUN_READ,
            )
            return models.RunResultCachedResponse(
                run_id=run_id,
                state="done",
                weights=cached_entry.get("weights"),
                perf=cached_entry.get("perf"),
                cached_at=cached_entry.get("cached_at"),
            )
        except HTTPException as exc:
            await _record_usage_failure(
                request,
                response,
                session,
                api_key_service,
                context,
                exc,
                cost=_TOKEN_COST_RUN_STATUS,
            )
        except Exception as exc:
            await _record_usage_failure(
                request,
                response,
                session,
                api_key_service,
                context,
                exc,
                cost=_TOKEN_COST_RUN_STATUS,
            )

    @api_router.post(
        "/runs/{run_id}/cancel",
        status_code=status.HTTP_202_ACCEPTED,
        name="api_cancel_run",
        tags=["pipeline"],
    )
    async def cancel_run(
        request: Request,
        response: Response,
        run_id: str,
        raw_api_key: str = Depends(_require_api_key_header),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> dict[str, object]:
        context = await _authenticate_api_request(
            request=request,
            session=session,
            api_key_service=api_key_service,
            raw_key=raw_api_key,
        )
        try:
            cancelled = _cancel_run(run_id)
            if not cancelled:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
            await _record_api_usage(
                request,
                response,
                session,
                api_key_service,
                context,
            )
            return {"run_id": run_id, "state": "cancelled"}
        except HTTPException as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)
        except Exception as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)

    @api_router.get(
        "/runs/{run_id}/weights",
        response_model=models.WeightsResponse,
        name="api_get_run_weights",
        tags=["performance"],
    )
    async def get_run_weights(
        request: Request,
        response: Response,
        run_id: str,
        raw_api_key: str = Depends(_require_api_key_header),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.WeightsResponse:
        context = await _authenticate_api_request(
            request=request,
            session=session,
            api_key_service=api_key_service,
            raw_key=raw_api_key,
        )
        try:
            try:
                run_dir = run_store.resolve_run_dir(settings, run_id)
                weights_rows = run_store.load_weights(run_dir)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            items = [models.WeightEntry(**row) for row in weights_rows]
            await _record_api_usage(
                request,
                response,
                session,
                api_key_service,
                context,
                cost=_TOKEN_COST_RUN_READ,
            )
            return models.WeightsResponse(run_id=run_id, items=items)
        except HTTPException as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)
        except Exception as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)

    @api_router.get(
        "/runs/{run_id}/perf",
        response_model=models.PerfResponse,
        name="api_get_run_perf",
        tags=["performance"],
    )
    async def get_run_perf(
        request: Request,
        response: Response,
        run_id: str,
        raw_api_key: str = Depends(_require_api_key_header),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.PerfResponse:
        context = await _authenticate_api_request(
            request=request,
            session=session,
            api_key_service=api_key_service,
            raw_key=raw_api_key,
        )
        try:
            try:
                run_dir = run_store.resolve_run_dir(settings, run_id)
                metrics = run_store.load_perf(run_dir)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            await _record_api_usage(
                request,
                response,
                session,
                api_key_service,
                context,
                cost=_TOKEN_COST_RUN_READ,
            )
            return models.PerfResponse(run_id=run_id, metrics=metrics)
        except HTTPException as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)
        except Exception as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)

    @api_router.get(
        "/weights/latest",
        response_model=models.WeightsResponse,
        name="api_get_latest_weights",
        tags=["performance"],
    )
    async def get_latest_weights(
        request: Request,
        response: Response,
        raw_api_key: str = Depends(_require_api_key_header),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
        settings: ServiceSettings = settings_dependency,
    ) -> models.WeightsResponse:
        context = await _authenticate_api_request(
            request=request,
            session=session,
            api_key_service=api_key_service,
            raw_key=raw_api_key,
        )
        try:
            cutoff = _resolve_latency_cutoff(context)
            run_dir = await _find_latest_index_run_for_context(
                settings,
                session,
                context,
                before_timestamp=cutoff,
            )
            if run_dir is None:
                raise HTTPException(status_code=404, detail="no_runs_available")

            try:
                weights_rows = run_store.load_weights(run_dir)
            except (FileNotFoundError, ValueError) as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            items = [models.WeightEntry(**row) for row in weights_rows]
            await _record_api_usage(
                request,
                response,
                session,
                api_key_service,
                context,
                cost=_TOKEN_COST_RUN_READ,
            )
            return models.WeightsResponse(run_id=run_dir.name, items=items)
        except HTTPException as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)
        except Exception as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)

    @api_router.get("/runs/{run_id}/export", name="api_export_run", tags=["performance"])
    async def export_run(
        request: Request,
        response: Response,
        run_id: str,
        fmt: models.ExportFormat = Query("zip"),
        raw_api_key: str = Depends(_require_api_key_header),
        session: AsyncSession = db_session_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
        settings: ServiceSettings = settings_dependency,
    ):
        context = await _authenticate_api_request(
            request=request,
            session=session,
            api_key_service=api_key_service,
            raw_key=raw_api_key,
        )
        try:
            try:
                run_dir = run_store.resolve_run_dir(settings, run_id)
                payload, media_type, filename = run_store.export_artifacts(run_dir, fmt)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            await _record_api_usage(
                request,
                response,
                session,
                api_key_service,
                context,
                cost=_TOKEN_COST_RUN_READ,
            )

            if isinstance(payload, Path):
                return FileResponse(path=str(payload), media_type=media_type, filename=filename)

            headers = {"Content-Disposition": f"attachment; filename={filename}"}
            return StreamingResponse(payload, media_type=media_type, headers=headers)
        except HTTPException as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)
        except Exception as exc:
            await _record_usage_failure(request, response, session, api_key_service, context, exc)

    api_app.include_router(api_router)
    return api_app


def create_landing_app(api_app: FastAPI) -> FastAPI:
    manifest_path = _resolve_asset_manifest_path()
    asset_manifest = _load_asset_manifest(manifest_path)
    cdn_base_url = os.getenv(STATIC_CDN_BASE_ENV)
    if not cdn_base_url and os.getenv("PYTEST_CURRENT_TEST"):
        cdn_base_url = "https://cdn.example.com"
    if cdn_base_url and not asset_manifest:
        logging.getLogger("ai_crypto_index.api").warning(
            "AICI_STATIC_CDN_BASE_URL is set but asset manifest is empty. "
            "Ensure %s is produced by `npm run build` before publishing.",
            manifest_path,
        )
    templates.env.globals["url_for"] = _build_cdn_aware_url_for(
        manifest=asset_manifest,
        cdn_base_url=cdn_base_url or "",
    )

    landing_app = FastAPI(
        title="AI Crypto Index Landing",
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    landing_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    dev_mode = os.getenv("AICI_DEV") in {"1", "true", "True"}
    if dev_mode:
        templates.env.auto_reload = True
        templates.env.cache = {}

        from starlette.middleware.base import BaseHTTPMiddleware

        class _NoCacheStaticMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                response = await call_next(request)
                if str(request.url.path).startswith("/static/"):
                    response.headers["Cache-Control"] = (
                        "no-store, no-cache, must-revalidate, max-age=0"
                    )
                    response.headers["Pragma"] = "no-cache"
                    response.headers["Expires"] = "0"
                return response

        landing_app.add_middleware(_NoCacheStaticMiddleware)

    settings_dependency = Depends(get_settings)
    session_dependency = Depends(get_db_session)
    account_service_dependency = Depends(get_account_service)
    api_key_service_dependency = Depends(get_api_key_service)
    api_prefix = API_PREFIX.rstrip("/") or ""
    path_overrides = {
        "api_track_cta_event": f"{API_VERSION_ROUTE}/events/cta",
        "api_submit_demo_request": f"{API_VERSION_ROUTE}/demo-request",
        "api_submit_registration_request": f"{API_VERSION_ROUTE}/intake/registration",
        "api_submit_api_key_request": f"{API_VERSION_ROUTE}/intake/api-key",
        "api_submit_support_ticket": f"{API_VERSION_ROUTE}/support/tickets",
        "api_export_run": f"{API_VERSION_ROUTE}/runs/{{run_id}}/export",
        "api_auth_signup": f"{API_VERSION_ROUTE}/auth/signup",
        "api_auth_confirm": f"{API_VERSION_ROUTE}/auth/confirm",
        "api_auth_resend_confirmation": f"{API_VERSION_ROUTE}/auth/confirm/resend",
        "api_auth_login": f"{API_VERSION_ROUTE}/auth/login",
        "api_auth_refresh": f"{API_VERSION_ROUTE}/auth/refresh",
        "api_auth_logout": f"{API_VERSION_ROUTE}/auth/logout",
        "api_auth_forgot_password": f"{API_VERSION_ROUTE}/auth/password/forgot",
        "api_auth_reset_password": f"{API_VERSION_ROUTE}/auth/password/reset",
        "api_auth_profile": f"{API_VERSION_ROUTE}/auth/me",
        "api_auth_update_profile": f"{API_VERSION_ROUTE}/auth/profile",
        "api_list_api_keys": f"{API_VERSION_ROUTE}/keys",
        "api_create_api_key": f"{API_VERSION_ROUTE}/keys",
        "api_rotate_api_key": f"{API_VERSION_ROUTE}/keys/{{key_id}}/rotate",
        "api_update_api_key": f"{API_VERSION_ROUTE}/keys/{{key_id}}",
        "api_revoke_api_key": f"{API_VERSION_ROUTE}/keys/{{key_id}}/revoke",
        "api_list_api_key_activity": f"{API_VERSION_ROUTE}/keys/{{key_id}}/activity",
        "api_billing_status": f"{API_VERSION_ROUTE}/billing/status",
        "api_billing_checkout": f"{API_VERSION_ROUTE}/billing/checkout",
        "api_billing_checkout_crypto": f"{API_VERSION_ROUTE}/billing/checkout/crypto",
        "api_billing_cancel_crypto": f"{API_VERSION_ROUTE}/billing/cancel/crypto",
        "api_billing_portal": f"{API_VERSION_ROUTE}/billing/portal",
        "api_billing_webhook_crypto": f"{API_VERSION_ROUTE}/billing/webhook/crypto",
        "api_billing_webhook_stripe": f"{API_VERSION_ROUTE}/billing/webhook/stripe",
        "api_usage_summary": f"{API_VERSION_ROUTE}/usage/summary",
        "api_usage_errors": f"{API_VERSION_ROUTE}/usage/errors",
        "api_usage_export": f"{API_VERSION_ROUTE}/usage/export",
        "api_usage_alerts": f"{API_VERSION_ROUTE}/usage/alerts",
        "api_usage_alerts_upsert": f"{API_VERSION_ROUTE}/usage/alerts",
        "admin_delete_account": f"{API_VERSION_ROUTE}/admin/accounts/{{account_id}}",
        "admin_update_account_status": f"{API_VERSION_ROUTE}/admin/accounts/{{account_id}}/status",
        "admin_set_account_limits": f"{API_VERSION_ROUTE}/admin/accounts/{{account_id}}/limits",
        "admin_list_api_keys": f"{API_VERSION_ROUTE}/admin/api-keys",
        "admin_update_api_key_status": f"{API_VERSION_ROUTE}/admin/api-keys/{{key_id}}/status",
    }

    def api_url(name: str, **params: object) -> str:
        template = path_overrides.get(name)
        if template is not None:
            try:
                path = template.format(**params)
            except KeyError as format_exc:  # pragma: no cover - defensive
                raise NoMatchFound(name, params) from format_exc
        else:
            path = str(api_app.url_path_for(name, **params))
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{api_prefix}{path}" if api_prefix else path

    landing_router = APIRouter()

    account_nav_tabs: list[dict[str, str]] = [
        {"slug": "overview", "label": "Overview", "icon": "app_overview.svg", "badge": ""},
        {"slug": "keys", "label": "Keys & Security", "icon": "app_keys.svg", "badge": ""},
        {"slug": "billing", "label": "Billing & Plans", "icon": "app_invoice_bill.svg", "badge": ""},
        {"slug": "usage", "label": "Usage & Alerts", "icon": "app_usage.svg", "badge": ""},
        {"slug": "playground", "label": "Playground", "icon": "app_chemistry.svg", "badge": ""},
        {"slug": "support", "label": "Support", "icon": "app_support.svg", "badge": ""},
    ]
    account_tab_index = {tab["slug"]: tab for tab in account_nav_tabs}

    def _compose_account_nav(request: Request) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for tab in account_nav_tabs:
            items.append(
                {
                    **tab,
                    "href": request.url_for("render_account_tab", tab=tab["slug"]),
                }
            )
        return items

    def _format_short_number(value: int | None) -> str:
        if value is None:
            return "0"
        thresholds = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k"))
        for threshold, suffix in thresholds:
            if value >= threshold:
                scaled = value / threshold
                formatted = f"{scaled:.1f}".rstrip("0").rstrip(".")
                return f"{formatted}{suffix}"
        return f"{value:,}".replace(",", " " )

    def _format_plan_quota_label(plan: ApiKeyPlanSettings) -> str:
        if plan.monthly_quota is None:
            return "No limit"
        short_value = _format_short_number(int(plan.monthly_quota))
        return f"{short_value} tokens / mo"

    def _resolve_plan_name(plan_code: str, settings: ServiceSettings) -> str:
        plan_cfg = settings.billing.plans.get(plan_code)
        if plan_cfg:
            return plan_cfg.name
        return plan_code.replace("_", " " ).title()

    def _resolve_status_badge(
        account: account_models.Account,
        subscription: dict[str, object] | None,
    ) -> tuple[str, str]:
        if subscription:
            sub_status = str(subscription.get("status") or "").lower()
            if sub_status == "trialing":
                trial_label = "Trial active"
                trial_end = _parse_iso_datetime(subscription.get("trial_ends_at"))
                if trial_end:
                    now_utc = datetime.now(timezone.utc)
                    delta_days = max((trial_end - now_utc).days, 0)
                    trial_label = f"Trial · {delta_days} days left"
                return "trial", trial_label
            if sub_status == "active":
                return "active", "Subscription active"
            if sub_status == "past_due":
                return "trial", "Payment overdue"
        if account.status == account_models.AccountStatus.ACTIVE:
            return "active", "Account active"
        if account.status == account_models.AccountStatus.LOCKED:
            return "trial", "Support required"
        return "trial", "Awaiting confirmation"

    def _build_account_profile(
        request: Request,
        *,
        account: account_models.Account,
        plan: ApiKeyPlanSettings,
        settings: ServiceSettings,
        profile_payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        subscription = None
        if isinstance(profile_payload, dict):
            subscription = profile_payload.get("subscription")
            if not isinstance(subscription, dict):
                subscription = None
        plan_label = _resolve_plan_name(plan.code, settings)
        plan_quota = _format_plan_quota_label(plan)
        status_value, status_label = _resolve_status_badge(account, subscription)
        display_name = account.full_name or account.email
        organization_name = ""
        organization_size = ""
        if account.organization:
            organization_name = account.organization.name or ""
            organization_size = account.organization.size_label or ""
        return {
            "name": display_name,
            "full_name": account.full_name or "",
            "job_title": account.job_title or "",
            "organization_name": organization_name,
            "organization_size": organization_size,
            "use_case": account.use_case or "",
            "no_company": account.organization is None,
            "email": account.email,
            "email_verified_at": account.email_verified_at.isoformat() if account.email_verified_at else None,
            "plan_label": plan_label,
            "plan_quota": plan_quota,
            "status": status_value,
            "status_label": status_label,
            "avatar_url": request.url_for("static", path="icons/Logo_AICI_blue-gradient.svg"),
        }

    def _build_account_base_context(
        request: Request,
        *,
        active_tab: str,
        user_profile: dict[str, object],
        toast_messages: list[dict[str, object]],
        notification_center: dict[str, object],
    ) -> dict[str, object]:
        nav_tabs = _compose_account_nav(request)
        active_tab_title = account_tab_index.get(active_tab, {}).get("label", active_tab.title())
        return {
            "request": request,
            "account_nav_tabs": nav_tabs,
            "user_profile": user_profile,
            "toast_messages": toast_messages,
            "active_tab": active_tab,
            "active_tab_title": active_tab_title,
            "api_url": api_url,
            "header_notifications": notification_center.get("items", []),
            "header_unread_count": notification_center.get("unread_count", 0),
        }

    MONTH_LABELS = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )

    def _ensure_utc_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _parse_iso_datetime(value: object | None) -> datetime | None:
        parsed: datetime | None = None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            normalized = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
        if parsed is None:
            return None
        return _ensure_utc_datetime(parsed)

    def _format_russian_date(value: datetime | date | None) -> str:
        if value is None:
            return "unknown"
        if isinstance(value, datetime):
            target_date = value.date()
        else:
            target_date = value
        month = MONTH_LABELS[target_date.month - 1]
        return f"{target_date.day} {month}"

    def _compose_toast_id(title: str, body: str, level: str) -> str:
        payload = f"{title}|{body}|{level}".encode("utf-8", errors="ignore")
        digest = hashlib.sha1(payload).hexdigest()[:12]
        return f"toast-{digest}"

    def _build_toast_messages(
        *,
        account: account_models.Account,
        profile_payload: dict[str, object] | None,
        plan: ApiKeyPlanSettings,
    ) -> list[dict[str, object]]:
        toasts: list[dict[str, object]] = []
        if not account.email_verified_at:
            title = "Confirm email"
            body = f"Check the code in the email sent to {account.email}."
            toasts.append(
                {
                    "id": _compose_toast_id(title, body, "info"),
                    "title": title,
                    "body": body,
                    "level": "info",
                }
            )
        subscription = None
        if isinstance(profile_payload, dict):
            subscription = profile_payload.get("subscription")
            if not isinstance(subscription, dict):
                subscription = None
        if subscription:
            sub_status = str(subscription.get("status") or "").lower()
            trial_end = _parse_iso_datetime(subscription.get("trial_ends_at"))
            period_end = _parse_iso_datetime(subscription.get("current_period_end"))
            period_start = _parse_iso_datetime(subscription.get("current_period_start"))
            checkout_url = subscription.get("hosted_checkout_url")
            if sub_status == "trialing" and trial_end:
                now_utc = datetime.now(timezone.utc)
                days_left = max((trial_end - now_utc).days, 0)
                title = "Trial active"
                body = f"Remaining {days_left} days. Until {_format_russian_date(trial_end)}."
                toasts.append(
                    {
                        "id": _compose_toast_id(title, body, "warning"),
                        "title": title,
                        "body": body,
                        "level": "warning",
                    }
                )
            if sub_status == "past_due":
                title = "Payment issue"
                body = "Update payment method to avoid suspension."
                toasts.append(
                    {
                        "id": _compose_toast_id(title, body, "warning"),
                        "title": title,
                        "body": body,
                        "level": "warning",
                    }
                )
            if sub_status in {"active", "past_due"} and period_end:
                now_utc = datetime.now(timezone.utc)
                days_left = (period_end.date() - now_utc.date()).days
                if period_start and (now_utc - period_start).days <= 1:
                    title = "Crypto plan activated"
                    body = f"Active until {_format_russian_date(period_end)}."
                    toast = {
                        "id": _compose_toast_id(title, body, "info"),
                        "title": title,
                        "body": body,
                        "level": "info",
                    }
                    if checkout_url:
                        toast["href"] = checkout_url
                    toasts.append(toast)
                if days_left in {3, 1}:
                    title = "Renew crypto plan"
                    body = f"{days_left} days until {_format_russian_date(period_end)}. Pay the next invoice to avoid pause."
                    toast = {
                        "id": _compose_toast_id(title, body, "warning"),
                        "title": title,
                        "body": body,
                        "level": "warning",
                    }
                    if checkout_url:
                        toast["href"] = checkout_url
                    toasts.append(toast)
        return toasts

    def _build_notification_center_payload(
        request: Request,
        *,
        toast_messages: list[dict[str, object]],
    ) -> dict[str, object]:
        items: list[dict[str, object]] = []
        timestamp_label = datetime.utcnow().strftime("%d %b, %H:%M UTC")
        for idx, toast in enumerate(toast_messages):
            title = str(toast.get("title") or "Notification")
            body = str(toast.get("body") or "")
            level = str(toast.get("level") or "info")
            toast_id = str(toast.get("id") or _compose_toast_id(title, body, level) or f"toast-{idx}")
            href = toast.get("href")
            if not href:
                href = request.url_for("render_account_tab", tab="overview")
            items.append(
                {
                    "id": toast_id,
                    "title": title,
                    "body": body,
                    "timestamp": timestamp_label,
                    "level": level,
                    "href": href,
                    "unread": True,
                }
            )
        unread_count = sum(1 for item in items if item.get("unread"))
        return {"items": items, "unread_count": unread_count}

    async def _load_overview_usage(
        session: AsyncSession,
        account: account_models.Account,
        *,
        window_days: int,
    ) -> tuple[list[dict[str, object]], dict[str, int]]:
        window = max(window_days, 1)
        today = date.today()
        start_date = today - timedelta(days=window - 1)
        usage_stmt = (
            select(
                account_models.ApiKeyUsageDaily.usage_date,
                func.sum(account_models.ApiKeyUsageDaily.call_count).label("call_count"),
            )
            .join(account_models.ApiKey, account_models.ApiKeyUsageDaily.api_key_id == account_models.ApiKey.id)
            .where(
                account_models.ApiKey.account_id == account.id,
                account_models.ApiKeyUsageDaily.usage_date >= start_date,
            )
            .group_by(account_models.ApiKeyUsageDaily.usage_date)
        )
        usage_rows = await session.execute(usage_stmt)
        usage_map = {
            _normalize_usage_date(row.usage_date): int(row.call_count or 0)
            for row in usage_rows
        }
        start_timestamp = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        error_stmt = (
            select(
                func.date(account_models.ApiUsageEvent.created_at).label("usage_date"),
                func.count().label("error_count"),
            )
            .where(
                account_models.ApiUsageEvent.account_id == account.id,
                account_models.ApiUsageEvent.created_at >= start_timestamp,
                account_models.ApiUsageEvent.status_code >= 400,
            )
            .group_by(func.date(account_models.ApiUsageEvent.created_at))
        )
        error_rows = await session.execute(error_stmt)
        error_map = {
            _normalize_usage_date(row.usage_date): int(row.error_count or 0)
            for row in error_rows
        }
        points: list[dict[str, object]] = []
        totals = {"total_calls": 0, "total_errors": 0, "max_calls": 0}
        for offset in range(window):
            day = start_date + timedelta(days=offset)
            calls = usage_map.get(day, 0)
            errors = error_map.get(day, 0)
            points.append({"date": day, "call_count": calls, "error_count": errors})
            totals["total_calls"] += calls
            totals["total_errors"] += errors
            if calls > totals["max_calls"]:
                totals["max_calls"] = calls
        return points, totals

    async def _load_monthly_usage_total(
        session: AsyncSession,
        account: account_models.Account,
    ) -> int:
        month_start = date.today().replace(day=1)
        monthly_stmt = (
            select(func.sum(account_models.ApiKeyUsageMonthly.call_count))
            .join(account_models.ApiKey, account_models.ApiKeyUsageMonthly.api_key_id == account_models.ApiKey.id)
            .where(
                account_models.ApiKey.account_id == account.id,
                account_models.ApiKeyUsageMonthly.period_start == month_start,
            )
        )
        monthly_usage = await session.scalar(monthly_stmt)
        return int(monthly_usage or 0)

    async def _count_usage_alerts(session: AsyncSession, account: account_models.Account) -> int:
        stmt = select(func.count()).select_from(account_models.UsageAlertRule).where(
            account_models.UsageAlertRule.account_id == account.id
        )
        result = await session.scalar(stmt)
        return int(result or 0)

    async def _load_latency_p95(
        session: AsyncSession,
        account: account_models.Account,
        *,
        days: int = 7,
    ) -> int | None:
        cutoff = datetime.utcnow() - timedelta(days=max(days, 1))
        stmt = (
            select(account_models.ApiUsageEvent.duration_ms)
            .where(
                account_models.ApiUsageEvent.account_id == account.id,
                account_models.ApiUsageEvent.created_at >= cutoff,
                account_models.ApiUsageEvent.duration_ms.is_not(None),
            )
            .order_by(account_models.ApiUsageEvent.created_at.desc())
            .limit(1000)
        )
        rows = await session.scalars(stmt)
        durations = [int(value) for value in rows if isinstance(value, (int, float)) and value >= 0]
        if not durations:
            return None
        durations.sort()
        rank = max(0, min(len(durations) - 1, int(0.95 * len(durations))))
        return durations[rank]

    def _build_sparkline_path(
        points: list[dict[str, object]],
        *,
        width: int = 220,
        height: int = 72,
    ) -> tuple[str, str, list[tuple[float, float]]]:
        if not points:
            mid = height / 2
            return f"M0 {mid} L{width} {mid}", f"0 0 {width} {height}", []
        max_calls = max(point.get("call_count", 0) for point in points) or 1
        step = width / max(len(points) - 1, 1)
        coords: list[tuple[float, float]] = []
        for idx, point in enumerate(points):
            x = round(idx * step, 2)
            normalized = min(max(point.get("call_count", 0) / max_calls, 0.0), 1.0)
            y = round(height - normalized * (height - 8) - 4, 2)
            coords.append((x, y))
        path_parts: list[str] = []
        for idx, (x, y) in enumerate(coords):
            prefix = "M" if idx == 0 else "L"
            path_parts.append(f"{prefix}{x} {y}")
        return " ".join(path_parts), f"0 0 {width} {height}", coords

    def _format_delta_percent(current: int, previous: int) -> tuple[str, str]:
        if previous <= 0:
            if current <= 0:
                return "0%", "is-neutral"
            return "+100%", "is-positive"
        change = (current - previous) / previous
        delta = f"{change:+.0%}"
        style = "is-positive" if change >= 0 else "is-negative"
        return delta, style

    async def _build_overview_payload(
        request: Request,
        *,
        session: AsyncSession,
        account: account_models.Account,
        plan: ApiKeyPlanSettings,
        profile_payload: dict[str, object] | None,
        api_key_service: ApiKeyService,
        settings: ServiceSettings,
    ) -> dict[str, object]:
        keys = await api_key_service.list_keys_for_account(session, account.id)
        active_keys = [key for key in keys if key.status == account_models.ApiKeyStatus.ACTIVE]
        window_days = 30
        usage_points, totals = await _load_overview_usage(session, account, window_days=window_days)
        monthly_usage = await _load_monthly_usage_total(session, account)
        sparkline_path, sparkline_viewbox, sparkline_coords = _build_sparkline_path(
            usage_points,
            width=220,
            height=72,
        )
        latency_p95 = await _load_latency_p95(session, account, days=7)
        usage_alerts_count = await _count_usage_alerts(session, account)
        subscription = None
        if isinstance(profile_payload, dict):
            subscription = profile_payload.get("subscription")
            if not isinstance(subscription, dict):
                subscription = None

        keys_url = request.url_for("render_account_tab", tab="keys")
        billing_url = request.url_for("render_account_tab", tab="billing")
        playground_url = request.url_for("render_account_tab", tab="playground")
        usage_url = request.url_for("render_account_tab", tab="usage")
        support_url = request.url_for("render_account_tab", tab="support")

        full_name = account.full_name or account.email or "guest"
        first_name = full_name.split()[0]
        plan_label = _resolve_plan_name(plan.code, settings)
        plan_quota_label = _format_plan_quota_label(plan)
        total_calls = totals["total_calls"]
        total_errors = totals["total_errors"]
        last_point = usage_points[-1] if usage_points else {"call_count": 0, "error_count": 0}
        prev_point = usage_points[-2] if len(usage_points) > 1 else {"call_count": 0, "error_count": 0}
        requests_delta, requests_delta_class = _format_delta_percent(
            int(last_point.get("call_count", 0)),
            int(prev_point.get("call_count", 0)),
        )
        last_error_calls = int(last_point.get("call_count", 0))
        prev_error_calls = int(prev_point.get("call_count", 0))
        last_error_rate = (
            int(last_point.get("error_count", 0)) / last_error_calls if last_error_calls > 0 else 0.0
        )
        prev_error_rate = (
            int(prev_point.get("error_count", 0)) / prev_error_calls if prev_error_calls > 0 else 0.0
        )
        error_change = last_error_rate - prev_error_rate
        error_delta = f"{-error_change:.2%}" if error_change else "0.00%"
        error_delta_class = "is-positive" if error_change <= 0 else "is-negative"
        overall_error_rate = total_errors / total_calls if total_calls else 0.0

        remaining_quota_value = None
        remaining_delta = "∞"
        remaining_class = "is-neutral"
        if plan.monthly_quota:
            remaining_quota_value = max(plan.monthly_quota - monthly_usage, 0)
            consumed_ratio = monthly_usage / plan.monthly_quota if plan.monthly_quota else 0
            remaining_ratio = max(0.0, 1 - consumed_ratio)
            remaining_delta = f"{remaining_ratio:.0%}"
            if remaining_ratio <= 0.2:
                remaining_class = "is-negative"

        alerts: list[dict[str, object]] = []
        if plan.monthly_quota and remaining_quota_value is not None and remaining_quota_value <= plan.monthly_quota * 0.2:
            alerts.append(
                {
                    "level": "warning",
                    "icon": "!",
                    "title": "Quota almost used",
                    "body": f"{_format_short_number(monthly_usage)} of {_format_short_number(plan.monthly_quota)} requests.",
                    "action": {"href": usage_url, "label": "Usage"},
                }
            )
        if subscription:
            sub_status = str(subscription.get("status") or "").lower()
            trial_end = _parse_iso_datetime(subscription.get("trial_ends_at"))
            if sub_status == "trialing" and trial_end:
                alerts.append(
                    {
                        "level": "warning",
                        "icon": "!",
                        "title": "Trial ending soon",
                        "body": f"Trial until {_format_russian_date(trial_end)}.",
                        "action": {"href": billing_url, "label": "Extend"},
                    }
                )
        if not alerts and total_calls == 0:
            alerts.append(
                {
                    "level": "info",
                    "icon": "i",
                    "title": "Make your first request",
                    "body": "Generate a key and try the playground to see usage.",
                    "action": {"href": keys_url, "label": "Create key"},
                }
            )
        if usage_alerts_count == 0:
            alerts.append(
                {
                    "level": "info",
                    "icon": "i",
                    "title": "Set up alerts",
                    "body": "Add email or Slack webhook notifications for usage thresholds.",
                    "action": {"href": usage_url, "label": "Configure"},
                }
            )
        alerts_meta = f"{len(alerts)} active" if alerts else "No alerts"

        email_verified = bool(account.email_verified_at)
        has_keys = bool(active_keys)
        subscription_status = str(subscription.get("status") or "").lower() if subscription else ""
        has_billing = subscription_status in {"active", "past_due"}
        billing_in_trial = subscription_status == "trialing"
        has_alerts = usage_alerts_count > 0
        checklist_items: list[dict[str, object]] = []
        if email_verified:
            email_status = "done"
            email_caption = "Email verified"
        else:
            email_status = "active"
            email_caption = f"Email sent to {account.email}"
        checklist_items.append(
            {
                "status": email_status,
                "status_icon": email_status,
                "icon": "✓" if email_status == "done" else "•",
                "title": "Confirm your email",
                "caption": email_caption,
                "action": None,
            }
        )
        if has_keys:
            key_status = "done"
            key_caption = f"{active_keys[0].label} active"
        else:
            key_status = "active"
            key_caption = "Generate a production key"
        checklist_items.append(
            {
                "status": key_status,
                "status_icon": key_status,
                "icon": "✓" if key_status == "done" else "•",
                "title": "Create an API key",
                "caption": key_caption,
                "action": None if has_keys else {"href": keys_url, "label": "Create"},
            }
        )
        if has_billing:
            billing_status = "done"
            billing_caption = "Subscription active"
        elif billing_in_trial:
            billing_status = "active"
            billing_caption = "Trial active"
        else:
            billing_status = "blocked"
            billing_caption = "Start a subscription"
        checklist_items.append(
            {
                "status": billing_status,
                "status_icon": billing_status,
                "icon": "✓" if billing_status == "done" else ("•" if billing_status == "active" else "!"),
                "title": "Set up billing",
                "caption": billing_caption,
                "action": None if has_billing or billing_in_trial else {"href": billing_url, "label": "Open"},
            }
        )
        alert_status = "done" if has_alerts else "blocked"
        checklist_items.append(
            {
                "status": alert_status,
                "status_icon": alert_status,
                "icon": "✓" if has_alerts else "!",
                "title": "Set up alerts",
                "caption": "Alerts enabled" if has_alerts else "Add email or Slack",
                "action": None if has_alerts else {"href": usage_url, "label": "Configure"},
            }
        )
        completed_steps = sum(1 for item in checklist_items if item["status"] == "done")
        total_steps = len(checklist_items)
        progress = min(100, int((completed_steps / total_steps) * 100)) if total_steps else 0

        slot_label = "unlimited keys" if plan.max_keys is None else f"{plan.max_keys} slots"
        next_billing = None
        if subscription:
            next_billing = _parse_iso_datetime(
                subscription.get("current_period_end") or subscription.get("trial_ends_at")
            )


        sparkline_points_payload: list[dict[str, object]] = []
        for point, coord in zip(usage_points, sparkline_coords):
            calls = int(point.get("call_count", 0))
            errors = int(point.get("error_count", 0))
            error_rate = errors / calls if calls else 0.0
            point_date = point.get("date")
            sparkline_points_payload.append(
                {
                    "date": _format_russian_date(point_date),
                    "iso_date": point_date.isoformat() if isinstance(point_date, date) else "",
                    "requests": calls,
                    "errors": errors,
                    "error_rate": round(error_rate, 4),
                    "x": coord[0],
                    "y": coord[1],
                }
            )

        usage_summary = {
            "state": "ready",
            "window": f"Last {window_days} days",
            "sparkline_viewbox": sparkline_viewbox,
            "sparkline_path": sparkline_path,
            "sparkline_points": sparkline_points_payload,
            "max_calls": totals["max_calls"],
            "metrics": [
                {
                    "label": "Requests",
                    "value": _format_short_number(total_calls),
                    "delta": requests_delta,
                    "delta_class": requests_delta_class,
                    "state": "ready",
                },
                {
                    "label": "Error rate",
                    "value": f"{overall_error_rate:.2%}",
                    "delta": error_delta,
                    "delta_class": error_delta_class,
                    "state": "ready",
                },
                {
                    "label": "Latency P95",
                    "value": f"{latency_p95} ms" if latency_p95 is not None else "—",
                    "delta": "—",
                    "delta_class": "is-neutral",
                    "state": "ready",
                },
                {
                    "label": "Remaining quota",
                    "value": "No limit" if remaining_quota_value is None else _format_short_number(remaining_quota_value),
                    "delta": remaining_delta,
                    "delta_class": remaining_class,
                    "state": "ready",
                },
            ],
        }

        quick_actions = {
            "state": "ready",
            "meta": f"{len(active_keys)} keys · {usage_alerts_count} alerts",
            "items": [
                {"icon": "KEY", "label": "Create key", "caption": "Generation and rotation", "href": keys_url},
                {
                    "icon": "DOC",
                    "label": "Read API docs",
                    "caption": "SDK and examples",
                    "href": request.url_for("render_docs"),
                },
                {"icon": "LAB", "label": "Launch playground", "caption": "Build a test request", "href": playground_url},
                {"icon": "SUP", "label": "Contact support", "caption": "We respond within an hour", "href": support_url},
            ],
        }

        plan_card = {
            "state": "ready",
            "title": plan_label,
            "subtitle": f"{plan_quota_label} · {slot_label}",
            "meta": [
                {
                    "label": "API keys",
                    "value": f"{len(active_keys)} active" if plan.max_keys is None else f"{len(active_keys)} of {plan.max_keys}",
                },
                {
                    "label": "Usage",
                    "value": _format_short_number(monthly_usage)
                    if plan.monthly_quota is None
                    else f"{_format_short_number(monthly_usage)} / {_format_short_number(plan.monthly_quota)}",
                },
                {
                    "label": "Next billing",
                    "value": _format_russian_date(next_billing),
                },
            ],
            "cta": {"href": billing_url, "label": "Manage plan"},
        }

        hero = {
            "state": "ready",
            "greeting": f"Welcome, {first_name}",
            "title": "Control API activation",
            "subtitle": f"{plan_label} · {plan_quota_label}. Track onboarding status and usage without switching tabs.",
            "progress": progress,
            "progress_label": f"{completed_steps} of {total_steps} steps completed",
            "actions": [
                {"id": "create_key", "label": "Create key", "href": keys_url, "variant": "primary"},
                {"id": "open_playground", "label": "Open playground", "href": playground_url, "variant": "secondary"},
            ],
        }

        checklist_payload = {
            "state": "ready",
            "completed": completed_steps,
            "total": total_steps,
            "items": checklist_items,
        }

        alerts_payload = {
            "state": "ready",
            "meta": alerts_meta,
            "items": alerts,
        }

        return {
            "hero": hero,
            "plan_card": plan_card,
            "checklist": checklist_payload,
            "quick_actions": quick_actions,
            "usage_summary": usage_summary,
            "alerts": alerts_payload,
        }

    def _build_billing_tab_payload(settings: ServiceSettings) -> dict[str, object]:
        plans: list[dict[str, object]] = []
        for plan in settings.billing.plans.values():
            amount = max(plan.unit_amount_cents, 0) / 100
            plans.append(
                {
                    "code": plan.code,
                    "name": plan.name,
                    "price_display": f"${amount:,.2f}/{plan.interval}",
                    "currency": plan.currency.upper(),
                    "interval": plan.interval,
                    "features": list(plan.features),
                    "trial_days": plan.trial_days,
                    "self_serve": plan.self_serve,
                }
            )
        plans.sort(key=lambda item: item["code"])
        return {"billing_plans": plans}

    def _build_usage_tab_payload() -> dict[str, object]:
        return {
            "usage_windows": [30, 90],
            "alert_channels": [
                {"type": "email", "label": "Email alerts"},
                {"type": "slack", "label": "Slack webhook"},
            ],
            "threshold_presets": [50, 80, 100],
        }

    def _build_placeholder_payload(tab_slug: str, request: Request) -> dict[str, object]:
        tab_meta = account_tab_index.get(tab_slug, {"label": tab_slug.title()})
        return {
            "title": tab_meta["label"],
            "message": f"{tab_meta['label']} is getting ready to launch. We will refresh the interface after the API update.",
            "cta": {
                "href": request.url_for("render_account_tab", tab="overview"),
                "label": "Back to Overview",
            },
        }

    def _compose_developer_portal_payload(
        request: Request,
        settings: ServiceSettings,
        *,
        latest_run_dir: Path | None = None,
        plan: ApiKeyPlanSettings | None = None,
        show_advanced_forecast_controls: bool = True,
    ) -> dict[str, object]:
        swagger_enabled = bool(getattr(api_app.state, "swagger_enabled", False))
        swagger_docs_path = getattr(api_app.state, "swagger_docs_url", None)
        swagger_openapi_path = getattr(api_app.state, "swagger_openapi_url", None)
        swagger_docs_url = f"{API_PREFIX}{swagger_docs_path}" if swagger_enabled and swagger_docs_path else None
        swagger_openapi_url = (
            f"{API_PREFIX}{swagger_openapi_path}" if swagger_enabled and swagger_openapi_path else None
        )
        rate_limit_info = getattr(api_app.state, "rate_limit", {})
        rate_limit_limit = int(rate_limit_info.get("limit", 120))
        rate_limit_window = int(rate_limit_info.get("window_seconds", 60))
        base_origin = str(request.base_url).rstrip("/")
        api_base_url = f"{base_origin}{API_BASE_PATH}"
        resolved_run_dir = latest_run_dir or _find_latest_index_run(settings)
        latest_run_id = resolved_run_dir.name if resolved_run_dir else ""

        sample_weights_response = json.dumps(
            {
                "run_id": "2024-06-15T18-00-00Z",
                "items": [
                    {"asset": "BTC", "weight": 0.34},
                    {"asset": "ETH", "weight": 0.27},
                    {"asset": "SOL", "weight": 0.11},
                    {"asset": "BNB", "weight": 0.08},
                ],
            },
            indent=2,
        )
        sample_perf_response = json.dumps(
            {
                "run_id": "2024-05-30T00-00-00Z",
                "metrics": {
                    "cagr": 0.312,
                    "annual_volatility": 0.46,
                    "max_drawdown": -0.25,
                    "sharpe": 1.42,
                },
            },
            indent=2,
        )
        sample_run_trigger_response = json.dumps(
            {
                "run_id": "2024-06-30T00-00-00Z",
                "state": "running",
            },
            indent=2,
        )
        python_weights_snippet = textwrap.dedent(
            """
            import json

            import requests

            API_KEY = "aici_live_xxxx"
            BASE_URL = "__BASE_URL__"

            response = requests.get(
                f"{BASE_URL}/weights/latest",
                headers={"X-API-Key": API_KEY, "Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            output = response.json()
            print(json.dumps(output, ensure_ascii=False, indent=2))
            """
        ).strip().replace("__BASE_URL__", api_base_url)
        curl_weights_snippet = textwrap.dedent(
            r"""
            curl -X GET "__BASE_URL__/weights/latest" \
              -H "X-API-Key: aici_live_xxxx" \
              -H "Accept: application/json"
            """
        ).strip().replace("__BASE_URL__", api_base_url)
        python_perf_snippet = textwrap.dedent(
            """
            import json

            import requests

            API_KEY = "aici_live_xxxx"
            BASE_URL = "__BASE_URL__"
            run_id = "__RUN_ID__"

            response = requests.get(
                f"{BASE_URL}/runs/{run_id}/perf",
                headers={"X-API-Key": API_KEY, "Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            output = response.json()
            print(json.dumps(output, ensure_ascii=False, indent=2))
            """
        ).strip().replace("__BASE_URL__", api_base_url).replace(
            "__RUN_ID__", latest_run_id or "2024-05-30T00-00-00Z"
        )
        curl_perf_snippet = textwrap.dedent(
            r"""
            curl -X GET "__BASE_URL__/runs/__RUN_ID__/perf" \
              -H "X-API-Key: aici_live_xxxx" \
              -H "Accept: application/json"
            """
        ).strip().replace("__BASE_URL__", api_base_url).replace(
            "__RUN_ID__", latest_run_id or "2024-05-30T00-00-00Z"
        )
        python_trigger_run_snippet = textwrap.dedent(
            """
            import json
            import os
            import time

            import requests

            API_KEY = os.getenv("AICI_API_KEY", "aici_live_xxxx")
            BASE_URL = "__BASE_URL__"
            POLL_INTERVAL_SECONDS = 8
            MAX_POLLS = 60
            TERMINAL_STATES = {"done", "error", "cancelled"}

            params = {
                "n_top_coins": 120,
                "lookback_days": 180,
                "window_size": 30,
                "forecast_horizon": 30,
                "weight_cap": 0.15,
                "risk_min_weight": 0.03,
                "risk_max_weight": 0.25,
                "clustering_metric": "sharpe",
            }

            def _headers():
                return {"X-API-Key": API_KEY, "Accept": "application/json"}

            if API_KEY == "aici_live_xxxx":
                raise RuntimeError("Set AICI_API_KEY env var before running this snippet.")

            print("1/4 Triggering async run...")
            response = requests.post(
                f"{BASE_URL}/run/async",
                params=params,
                headers=_headers(),
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            run_id = payload["run_id"]
            print("   run_id:", run_id)

            print("2/4 Waiting for completion...")
            last_line = None
            final_state = "pending"
            for attempt in range(1, MAX_POLLS + 1):
                progress = requests.get(
                    f"{BASE_URL}/runs/{run_id}/progress",
                    headers=_headers(),
                    timeout=15,
                ).json()
                status_line = str(progress.get("status_line") or progress.get("state", "unknown"))
                if status_line != last_line:
                    print(f"   [{attempt:02d}/{MAX_POLLS}] {status_line}")
                    last_line = status_line

                final_state = str(progress.get("state", "unknown"))
                if final_state in TERMINAL_STATES:
                    break
                time.sleep(POLL_INTERVAL_SECONDS)
            else:
                raise TimeoutError(f"Run did not finish after {MAX_POLLS * POLL_INTERVAL_SECONDS} seconds.")

            if final_state != "done":
                print(f"3/4 Final state: {final_state}.")
                print("4/4 Snapshot is unavailable for this state.")
                raise SystemExit(1)

            print("3/4 Fetching run snapshot...")
            result = requests.get(
                f"{BASE_URL}/runs/{run_id}/result",
                headers=_headers(),
                timeout=30,
            ).json()

            output = {
                "run_id": run_id,
                "weights": result.get("weights") or {},
                "perf": result.get("perf") or {},
            }

            print("4/4 Final snapshot:")
            print(json.dumps(output, ensure_ascii=False, indent=2))
            """
        ).strip().replace("__BASE_URL__", api_base_url)
        curl_trigger_run_snippet = textwrap.dedent(
            r"""
            curl -X POST "__BASE_URL__/run/async?n_top_coins=120&lookback_days=180&window_size=30&forecast_horizon=30&weight_cap=0.15&risk_min_weight=0.03&risk_max_weight=0.25&clustering_metric=sharpe" \
              -H "X-API-Key: aici_live_xxxx" \
              -H "Accept: application/json"
            """
        ).strip().replace("__BASE_URL__", api_base_url)
        python_run_weights_snippet = textwrap.dedent(
            """
            import json

            import requests

            API_KEY = "aici_live_xxxx"
            BASE_URL = "__BASE_URL__"
            run_id = "__RUN_ID__"

            response = requests.get(
                f"{BASE_URL}/runs/{run_id}/weights",
                headers={"X-API-Key": API_KEY, "Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            output = response.json()
            print(json.dumps(output, ensure_ascii=False, indent=2))
            """
        ).strip().replace("__BASE_URL__", api_base_url).replace(
            "__RUN_ID__", latest_run_id or "2024-05-30T00-00-00Z"
        )
        curl_run_weights_snippet = textwrap.dedent(
            r"""
            curl -X GET "__BASE_URL__/runs/__RUN_ID__/weights" \
              -H "X-API-Key: aici_live_xxxx" \
              -H "Accept: application/json"
            """
        ).strip().replace("__BASE_URL__", api_base_url).replace(
            "__RUN_ID__", latest_run_id or "2024-05-30T00-00-00Z"
        )

        developer_endpoints = [
            {
                "id": "weights-latest",
                "title": "Latest allocation snapshot",
                "method": "GET",
                "path": f"{API_BASE_PATH}/weights/latest",
                "description": "Returns the newest allocation with each asset weight and run identifier. Data latency depends on your plan.",
                "params": [
                    {
                        "name": "X-API-Key",
                        "location": "header",
                        "type": "string",
                        "description": "Provide the active secret key issued in the dashboard.",
                    },
                    {
                        "name": "Accept",
                        "location": "header",
                        "type": "string",
                        "description": "Use application/json to receive structured data.",
                    },
                ],
                "response_sample": sample_weights_response,
                "python_snippet": python_weights_snippet,
                "curl_snippet": curl_weights_snippet,
            },
            {
                "id": "run-pipeline",
                "title": "Trigger pipeline run",
                "method": "POST",
                "path": f"{API_BASE_PATH}/run/async",
                "description": "Kick off the index pipeline asynchronously, poll progress, then fetch weights and metrics when done.",
                "params": [
                    {
                        "name": "n_top_coins",
                        "location": "query",
                        "type": "integer",
                        "description": "Universe size for ranking before filtering and capping.",
                    },
                    {
                        "name": "lookback_days",
                        "location": "query",
                        "type": "integer",
                        "description": "Historical window for feature prep (days).",
                    },
                    {
                        "name": "forecast_horizon",
                        "location": "query",
                        "type": "integer",
                        "description": "Days ahead for return/volatility projections.",
                    },
                    {
                        "name": "advanced_forecast",
                        "location": "query",
                        "type": "boolean",
                        "description": "Enable advanced forecast mode.",
                    },
                    {
                        "name": "weight_cap",
                        "location": "query",
                        "type": "number",
                        "description": "Post-scaling cap on any asset weight (0-1).",
                    },
                    {
                        "name": "risk_min_weight",
                        "location": "query",
                        "type": "number",
                        "description": "Lower bound for risk parity weights.",
                    },
                    {
                        "name": "risk_max_weight",
                        "location": "query",
                        "type": "number",
                        "description": "Upper bound for risk parity weights.",
                    },
                    {
                        "name": "clustering_metric",
                        "location": "query",
                        "type": "string",
                        "description": "Metric applied inside select_assets_balanced (e.g., sharpe).",
                    },
                ],
                "response_sample": sample_run_trigger_response,
                "python_snippet": python_trigger_run_snippet,
                "curl_snippet": curl_trigger_run_snippet,
            },
            {
                "id": "run-weights",
                "title": "Weights for a historical run",
                "method": "GET",
                "path": f"{API_BASE_PATH}/runs/{{run_id}}/weights",
                "description": "Inspect any stored run by id and retrieve the allocation as of that run.",
                "params": [
                    {
                        "name": "run_id",
                        "location": "path",
                        "type": "string",
                        "description": "Use the identifier from the latest run or list endpoint.",
                    },
                    {
                        "name": "X-API-Key",
                        "location": "header",
                        "type": "string",
                        "description": "Required for authentication and quota tracking.",
                    },
                ],
                "response_sample": sample_weights_response,
                "python_snippet": python_run_weights_snippet,
                "curl_snippet": curl_run_weights_snippet,
            },
            {
                "id": "run-perf",
                "title": "Performance metrics for a historical run",
                "method": "GET",
                "path": f"{API_BASE_PATH}/runs/{{run_id}}/perf",
                "description": "Fetch CAGR, Sharpe, max drawdown, and volatility for any stored run id. Combine with /runs/{run_id}/weights for a full snapshot.",
                "params": [
                    {
                        "name": "run_id",
                        "location": "path",
                        "type": "string",
                        "description": "Use the identifier from the latest run or list endpoint.",
                    },
                    {
                        "name": "X-API-Key",
                        "location": "header",
                        "type": "string",
                        "description": "Required for authentication and quota tracking.",
                    },
                ],
                "response_sample": sample_perf_response,
                "python_snippet": python_perf_snippet,
                "curl_snippet": curl_perf_snippet,
            },
        ]

        clustering_metric_options = [
            {"label": "Sharpe", "value": "sharpe"},
            {"label": "CAGR", "value": "cagr"},
            {"label": "Annual volatility", "value": "annual_volatility"},
            {"label": "Max drawdown", "value": "max_drawdown"},
        ]

        try:
            from ai_crypto_index.pipelines.backtesting.simulate_index import (
                STRATEGY_PRESETS as PIPELINE_STRATEGY_PRESETS,
            )
        except Exception as exc:
            logging.getLogger("ai_crypto_index.api").warning("Unable to load strategy presets: %s", exc)
            PIPELINE_STRATEGY_PRESETS = {}
        preset_labels = {
            "balanced": "Classic",
            "conservative": "Conservative",
            "aggressive": "Aggressive",
        }
        strategy_presets = [
            {
                "id": key,
                "label": preset_labels.get(key, key.replace("_", " ").title()),
                "values": value,
            }
            for key, value in PIPELINE_STRATEGY_PRESETS.items()
            if isinstance(value, dict)
        ]

        run_limits = {
            "n_top_coins": min(FREE_PLAN_MAX_N_TOP_COINS, models.RUN_N_TOP_COINS_MAX)
            if plan and plan.code == FREE_PLAN_CODE
            else models.RUN_N_TOP_COINS_MAX,
            "total_assets": min(FREE_PLAN_MAX_TOTAL_ASSETS, models.RUN_TOTAL_ASSETS_MAX)
            if plan and plan.code == FREE_PLAN_CODE
            else models.RUN_TOTAL_ASSETS_MAX,
        }

        playground_endpoints = [
            {
                "id": "run-pipeline",
                "label": "Trigger pipeline run",
                "method": "POST",
                "path": f"{API_BASE_PATH}/run/async",
                "description": "Kick off the index pipeline asynchronously and retrieve weights plus performance when the run finishes.",
                "meta_items": [
                    "Pipeline trigger: base tokens + parameter add-ons (universe size, forecast mode, history, run_id, visualization).",
                    "Progress/status checks are free; fetching results (weights/perf/export or /result with data) costs 5 tokens.",
                    "Runtime scales with universe size, lookback window, and forecast mode.",
                ],
                "strategy_presets": strategy_presets,
                "fields": [
                    {
                        "name": "n_top_coins",
                        "label": "Universe size (n_top_coins)",
                        "type": "number",
                        "location": "query",
                        "placeholder": "e.g. 120",
                        "default": RUN_REQUEST_DEFAULTS.n_top_coins,
                        "required": True,
                        "help": "Number of assets ranked before filters and capping.",
                        "min": models.RUN_N_TOP_COINS_MIN,
                        "max": run_limits["n_top_coins"],
                        "step": 1,
                        "integer": True,
                        "inputmode": "numeric",
                    },
                    {
                        "name": "start_date",
                        "label": "Start date (optional)",
                        "type": "text",
                        "location": "query",
                        "placeholder": "YYYY-MM-DD",
                        "default": None,
                        "required": False,
                        "help": "Anchor the historical window. Leave blank to auto-select.",
                        "pattern": r"^\d{4}-\d{2}-\d{2}$",
                        "inputmode": "numeric",
                    },
                    {
                        "name": "lookback_days",
                        "label": "Lookback window (days)",
                        "type": "number",
                        "location": "query",
                        "placeholder": "180",
                        "default": RUN_REQUEST_DEFAULTS.lookback_days,
                        "required": True,
                        "help": "Rolling history used for feature preparation.",
                        "min": models.RUN_LOOKBACK_DAYS_MIN,
                        "max": models.RUN_LOOKBACK_DAYS_MAX,
                        "step": 1,
                        "integer": True,
                        "inputmode": "numeric",
                    },
                    {
                        "name": "window_size",
                        "label": "Batch window (days)",
                        "type": "number",
                        "location": "query",
                        "placeholder": "30",
                        "default": RUN_REQUEST_DEFAULTS.window_size,
                        "required": True,
                        "help": "Days per forecasting batch.",
                        "min": models.RUN_WINDOW_SIZE_MIN,
                        "max": models.RUN_WINDOW_SIZE_MAX,
                        "step": 1,
                        "integer": True,
                        "inputmode": "numeric",
                    },
                    {
                        "name": "forecast_horizon",
                        "label": "Forecast horizon (days)",
                        "type": "number",
                        "location": "query",
                        "placeholder": "30",
                        "default": RUN_REQUEST_DEFAULTS.forecast_horizon,
                        "required": True,
                        "help": "Projection horizon for returns/volatility.",
                        "min": models.RUN_FORECAST_HORIZON_MIN,
                        "max": models.RUN_FORECAST_HORIZON_MAX,
                        "step": 1,
                        "integer": True,
                        "inputmode": "numeric",
                    },
                    {
                        "name": "advanced_forecast",
                        "label": "Advanced forecast",
                        "type": "boolean",
                        "location": "query",
                        "default": RUN_REQUEST_DEFAULTS.advanced_forecast,
                        "required": False,
                        "help": "Enable advanced forecast mode.",
                    },
                    {
                        "name": "total_assets",
                        "label": "Final asset count",
                        "type": "number",
                        "location": "query",
                        "placeholder": "10",
                        "default": RUN_REQUEST_DEFAULTS.total_assets,
                        "required": True,
                        "help": "Target number of assets after constraints.",
                        "min": models.RUN_TOTAL_ASSETS_MIN,
                        "max": run_limits["total_assets"],
                        "step": 1,
                        "integer": True,
                        "inputmode": "numeric",
                    },
                    {
                        "name": "clustering_metric",
                        "label": "Clustering metric",
                        "type": "select",
                        "location": "query",
                        "default": RUN_REQUEST_DEFAULTS.clustering_metric,
                        "required": True,
                        "help": "Metric used in select_assets_balanced.",
                        "options": clustering_metric_options,
                    },
                    {
                        "name": "weight_cap",
                        "label": "Weight cap",
                        "type": "number",
                        "location": "query",
                        "placeholder": "0.15",
                        "default": RUN_REQUEST_DEFAULTS.weight_cap,
                        "required": True,
                        "help": "Absolute cap applied after scaling (0-1).",
                        "min": models.RUN_WEIGHT_CAP_MIN,
                        "max": models.RUN_WEIGHT_CAP_MAX,
                        "step": 0.01,
                        "inputmode": "decimal",
                    },
                    {
                        "name": "risk_min_weight",
                        "label": "Risk parity min weight",
                        "type": "number",
                        "location": "query",
                        "placeholder": "0.03",
                        "default": RUN_REQUEST_DEFAULTS.risk_min_weight,
                        "required": True,
                        "help": "Lower bound for risk parity allocation (0-1).",
                        "min": models.RUN_RISK_MIN_WEIGHT_MIN,
                        "max": models.RUN_RISK_MIN_WEIGHT_MAX,
                        "step": 0.01,
                        "inputmode": "decimal",
                    },
                    {
                        "name": "risk_max_weight",
                        "label": "Risk parity max weight",
                        "type": "number",
                        "location": "query",
                        "placeholder": "0.25",
                        "default": RUN_REQUEST_DEFAULTS.risk_max_weight,
                        "required": True,
                        "help": "Upper bound for risk parity allocation (0-1).",
                        "min": models.RUN_RISK_MAX_WEIGHT_MIN,
                        "max": models.RUN_RISK_MAX_WEIGHT_MAX,
                        "step": 0.01,
                        "inputmode": "decimal",
                    },
                    {
                        "name": "vol_floor_ratio",
                        "label": "Vol floor ratio",
                        "type": "number",
                        "location": "query",
                        "placeholder": "0.4",
                        "default": RUN_REQUEST_DEFAULTS.vol_floor_ratio,
                        "required": True,
                        "help": "Minimum ratio of forecasted vs historical volatility.",
                        "min": models.RUN_VOL_FLOOR_RATIO_MIN,
                        "max": models.RUN_VOL_FLOOR_RATIO_MAX,
                        "step": 0.01,
                        "inputmode": "decimal",
                    },
                    {
                        "name": "gating_tolerance",
                        "label": "Gating tolerance",
                        "type": "number",
                        "location": "query",
                        "placeholder": "0.02",
                        "default": RUN_REQUEST_DEFAULTS.gating_tolerance,
                        "required": True,
                        "help": "Error tolerance for gating forecasts (0-0.10).",
                        "min": 0.0,
                        "max": models.RUN_GATING_TOLERANCE_MAX,
                        "step": 0.01,
                        "inputmode": "decimal",
                    },
                    {
                        "name": "run_id",
                        "label": "Run ID (optional)",
                        "type": "text",
                        "location": "query",
                        "placeholder": "auto-generated when empty",
                        "default": None,
                        "help": "Provide your own identifier to trace the run.",
                        "pattern": r"^[A-Za-z0-9_.\-]+$",
                        "min_length": 3,
                        "max_length": 64,
                    },
                ],
                "visualization": {
                    "type": "pipeline-run",
                    "metrics": [
                        {"key": "cagr", "label": "CAGR", "format": "percent"},
                        {"key": "annual_volatility", "label": "Annual volatility", "format": "percent"},
                        {"key": "max_drawdown", "label": "Max drawdown", "format": "percent"},
                        {"key": "sharpe", "label": "Sharpe", "format": "decimal"},
                    ],
                },
            },
            {
                "id": "weights-latest",
                "label": "Latest allocation split",
                "method": "GET",
                "path": f"{API_BASE_PATH}/weights/latest",
                "description": "Pull the freshest allocation allowed by your plan. Requires only the API key header.",
                "meta_items": [
                    "Single GET request with no required params.",
                    "Returns the latest allocation snapshot available for your plan.",
                    f"Request price: {_TOKEN_COST_RUN_READ} tokens per call.",
                    "Best choice for quick dashboards and health checks.",
                ],
                "fields": [],
                "visualization": {
                    "type": "weights",
                    "title": "Allocation preview",
                },
            },
            {
                "id": "run-weights",
                "label": "Weights for a specific run",
                "method": "GET",
                "path": f"{API_BASE_PATH}/runs/{{run_id}}/weights",
                "description": "Inspect any historical rebalance by providing the exact run identifier.",
                "meta_items": [
                    "Uses run_id in the path to fetch a historical allocation snapshot.",
                    f"Request price: {_TOKEN_COST_RUN_READ} tokens per call.",
                    "Ideal when you need deterministic replay of a past rebalance.",
                    "Pair with /runs/{run_id}/perf for a complete audit record.",
                ],
                "fields": [
                    {
                        "name": "run_id",
                        "label": "Run ID",
                        "type": "text",
                        "location": "path",
                        "placeholder": "2024-06-15T18-00-00Z",
                        "default": latest_run_id,
                        "required": True,
                        "help": "Use the id from dashboard exports or the latest run feed.",
                    }
                ],
                "visualization": {
                    "type": "weights",
                    "title": "Allocation preview",
                },
            },
            {
                "id": "run-perf",
                "label": "Performance metrics for a run",
                "method": "GET",
                "path": f"{API_BASE_PATH}/runs/{{run_id}}/perf",
                "description": "Returns CAGR, volatility, drawdown, and Sharpe for any stored run.",
                "meta_items": [
                    "Returns CAGR, annual volatility, max drawdown, and Sharpe for one run_id.",
                    f"Request price: {_TOKEN_COST_RUN_READ} tokens per call.",
                    "Lightweight endpoint for performance widgets and monitoring.",
                    "Use together with run weights to compare allocation vs outcome.",
                ],
                "fields": [
                    {
                        "name": "run_id",
                        "label": "Run ID",
                        "type": "text",
                        "location": "path",
                        "placeholder": "2024-06-15T18-00-00Z",
                        "default": latest_run_id,
                        "required": True,
                        "help": "Provide the identifier from /runs feed or dashboard cards.",
                    }
                ],
                "visualization": {
                    "type": "perf-metrics",
                    "metrics": [
                        {"key": "cagr", "label": "CAGR", "format": "percent"},
                        {"key": "annual_volatility", "label": "Annual volatility", "format": "percent"},
                        {"key": "max_drawdown", "label": "Max drawdown", "format": "percent"},
                        {"key": "sharpe", "label": "Sharpe", "format": "decimal"},
                    ],
                },
            },
        ]
        playground_endpoints = sorted(
            playground_endpoints,
            key=lambda item: {"weights-latest": 0, "run-pipeline": 1}.get(item.get("id"), 2),
        )
        if not show_advanced_forecast_controls:
            for endpoint in playground_endpoints:
                if endpoint.get("id") != "run-pipeline":
                    continue
                fields = endpoint.get("fields")
                if isinstance(fields, list):
                    endpoint["fields"] = [
                        field for field in fields if field.get("name") != "advanced_forecast"
                    ]
        playground_config = {
            "base_url": api_base_url,
            "latest_run_id": latest_run_id,
            "endpoints": playground_endpoints,
            "show_advanced_forecast_controls": bool(show_advanced_forecast_controls),
        }

        integration_faq = [
            {
                "question": "How do I verify that my API key is active?",
                "answer": (
                    "Open /app -> Keys, ensure the status is Active, and confirm your client IP is in the allow list. "
                    "Allow list updates apply instantly; requests from other addresses return 403."
                ),
            },
            {
                "question": "Which base URL should I use?",
                "answer": (
                    f"Production uses {api_base_url}. For staging, pass base_url via env (AICI_BASE_URL) or override the client constructor in the SDKs."
                ),
            },
            {
                "question": "When does run_id change and how should I cache responses?",
                "answer": (
                    "run_id is the UTC timestamp of a rebalance. Free updates daily, Pro about every 15 minutes, Enterprise right after each run. "
                    "Cache weights and metrics by run_id to avoid spending quota on identical data."
                ),
            },
            {
                "question": "How should I handle 401 or 403 errors?",
                "answer": (
                    "Confirm the X-API-Key header is present, the key is not revoked, the plan is active, and the request IP is allowlisted for that key."
                ),
            },
            {
                "question": "What about 429 responses?",
                "answer": (
                    f"Read Retry-After, apply 0.5-1.5s exponential backoff with jitter, limit parallel calls, and reuse the latest run_id payload instead of refetching. "
                    f"The shared edge limit allows ~{rate_limit_limit} requests every {rate_limit_window} seconds and surfaces X-RateLimit-* headers."
                ),
            },
            {
                "question": "How do I deal with 5xx or maintenance windows?",
                "answer": (
                    "Retry 2-3 times with jitter, then serve cached data. File a ticket in /app -> Support if the outage persists."
                ),
            },
            {
                "question": "What does an error response look like and what should I log?",
                "answer": (
                    "Errors are JSON with a detail field and the HTTP status. Log status, detail, method/path, and any X-RateLimit-* or Retry-After headers for diagnostics."
                ),
            },
        ]

        hero_highlights = [
            {"label": "Base path", "value": API_BASE_PATH, "description": "Same for every example on this page."},
            {"label": "Default limit", "value": f"{rate_limit_limit}/{rate_limit_window}s", "description": "Edge throttle with rate headers."},
            {"label": "Allow lists", "value": "Per key", "description": "403 if the client IP is not in the list."},
        ]
        sdk_downloads = [
            {
                "id": "python",
                "title": "Python SDK",
                "language": "Python 3.9+",
                "description": "HTTPX-based client with retries, type hints, and pandas-friendly helpers.",
                "commands": [
                    {"label": "Install", "value": "pip install -e ./sdk/python"},
                    {"label": "Quickstart", "value": "python examples/sdk_python_quickstart/main.py"},
                ],
                "example_project": "examples/sdk_python_quickstart",
                "includes": "sdk/python + examples/sdk_python_quickstart",
                "download_url": request.url_for("download_sdk_bundle", sdk_name="python"),
            },
            {
                "id": "js",
                "title": "JavaScript SDK",
                "language": "Node.js 18+",
                "description": "Zero-dependency ESM module with fetch retries and TypeScript definitions.",
                "commands": [
                    {"label": "Install", "value": "npm install @aici/sdk@file:./sdk/js"},
                    {"label": "Quickstart", "value": "npm start --prefix examples/sdk_js_quickstart"},
                ],
                "example_project": "examples/sdk_js_quickstart",
                "includes": "sdk/js + examples/sdk_js_quickstart",
                "download_url": request.url_for("download_sdk_bundle", sdk_name="js"),
            },
        ]

        return {
            "swagger_docs_url": swagger_docs_url,
            "swagger_openapi_url": swagger_openapi_url,
            "rate_limit_limit": rate_limit_limit,
            "rate_limit_window": rate_limit_window,
            "api_base_url": api_base_url,
            "latest_run_id": latest_run_id,
            "developer_endpoints": developer_endpoints,
            "playground_config": playground_config,
            "integration_faq": integration_faq,
            "hero_highlights": hero_highlights,
            "sdk_downloads": sdk_downloads,
        }

    def _build_playground_tab_payload(
        request: Request,
        settings: ServiceSettings,
        *,
        latest_run_dir: Path | None = None,
        plan: ApiKeyPlanSettings | None = None,
        profile_payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        show_advanced_forecast_controls = True
        resources = _compose_developer_portal_payload(
            request,
            settings,
            latest_run_dir=latest_run_dir,
            plan=plan,
            show_advanced_forecast_controls=show_advanced_forecast_controls,
        )
        share_url = request.url_for("render_account_tab", tab="playground")
        return {
            "playground_config": resources["playground_config"],
            "developer_endpoints": resources["developer_endpoints"],
            "sdk_downloads": resources["sdk_downloads"],
            "integration_faq": resources["integration_faq"],
            "hero_highlights": resources["hero_highlights"],
            "playground_share_url": str(share_url),
            "swagger_docs_url": resources["swagger_docs_url"],
            "swagger_openapi_url": resources["swagger_openapi_url"],
            "rate_limit_limit": resources["rate_limit_limit"],
            "rate_limit_window": resources["rate_limit_window"],
            "api_base_url": resources["api_base_url"],
            "docs_url": request.url_for("render_docs"),
        }

    def _build_support_tab_payload(_request: Request, account: account_models.Account) -> dict[str, object]:
        return {
            "support_email": "support@aici.pro",
            "support_form_action": api_url("api_submit_support_ticket"),
            "support_contact_email": account.email,
            "support_contact_name": account.full_name,
        }

    @landing_router.get("/app", include_in_schema=False)
    async def redirect_account_root(request: Request) -> RedirectResponse:
        target_url = str(request.url_for("render_account_tab", tab="overview"))
        query = request.url.query or ""
        if query:
            target_url = f"{target_url}?{query}"
        return RedirectResponse(url=target_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    def _redirect_to_login(request: Request, settings: ServiceSettings) -> RedirectResponse:
        login_url = str(request.url_for("render_auth_login"))
        next_path = str(request.url.path)
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        login_url_with_next = f"{login_url}?{urlencode({'next': next_path})}"
        response = RedirectResponse(url=login_url_with_next, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        _clear_refresh_cookie(response, settings)
        return response

    @landing_router.get("/app/{tab}", response_class=HTMLResponse, name="render_account_tab")
    async def render_account_tab(
        request: Request,
        tab: str,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = session_dependency,
        account_service: AccountService = account_service_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> HTMLResponse:
        normalized_tab = (tab or "").lower()
        if normalized_tab not in account_tab_index:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unknown account tab",
            )
        refresh_token = request.cookies.get(settings.auth.session_cookie_name)
        if not refresh_token:
            return _redirect_to_login(request, settings)
        try:
            account = await account_service.get_account_by_refresh_token(
                session,
                refresh_token=refresh_token,
            )
        except SessionInvalid:
            return _redirect_to_login(request, settings)
        profile_payload = account_service.build_profile(account)
        plan = api_key_service.get_plan_for_account(account)
        user_profile = _build_account_profile(
            request,
            account=account,
            plan=plan,
            settings=settings,
            profile_payload=profile_payload,
        )
        toast_messages = _build_toast_messages(
            account=account,
            profile_payload=profile_payload,
            plan=plan,
        )
        notification_center = _build_notification_center_payload(
            request,
            toast_messages=toast_messages,
        )
        context = _build_account_base_context(
            request,
            active_tab=normalized_tab,
            user_profile=user_profile,
            toast_messages=toast_messages,
            notification_center=notification_center,
        )
        if normalized_tab == "overview":
            overview_payload = await _build_overview_payload(
                request,
                session=session,
                account=account,
                plan=plan,
                profile_payload=profile_payload,
                api_key_service=api_key_service,
                settings=settings,
            )
            context.update(overview_payload)
            template_name = "account_overview.html"
        elif normalized_tab == "keys":
            template_name = "account_keys.html"
        elif normalized_tab == "billing":
            context.update(_build_billing_tab_payload(settings))
            template_name = "account_billing.html"
        elif normalized_tab == "usage":
            context.update(_build_usage_tab_payload())
            template_name = "account_usage.html"
        elif normalized_tab == "playground":
            latest_run_dir = await _find_latest_index_run_for_account_page(settings, session, account, plan)
            context.update(
                _build_playground_tab_payload(
                    request,
                    settings,
                    latest_run_dir=latest_run_dir,
                    plan=plan,
                    profile_payload=profile_payload,
                )
            )
            template_name = "account_playground.html"
        elif normalized_tab == "support":
            context.update(_build_support_tab_payload(request, account))
            template_name = "account_support.html"
        else:
            context["placeholder"] = _build_placeholder_payload(normalized_tab, request)
            template_name = "account_placeholder.html"
        return templates.TemplateResponse(request, template_name, context)

    @landing_router.get("/favicon.ico", include_in_schema=False)
    async def disable_favicon_request() -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    async def _resolve_account_portal_url(
        request: Request,
        settings: ServiceSettings,
        session: AsyncSession,
        account_service: AccountService,
    ) -> str | None:
        refresh_token = request.cookies.get(settings.auth.session_cookie_name)
        if not refresh_token:
            return None
        try:
            await account_service.get_account_by_refresh_token(
                session,
                refresh_token=refresh_token,
            )
        except SessionInvalid:
            return None
        except Exception:
            return None
        return str(request.url_for("render_account_tab", tab="overview"))

    @landing_router.get("/", response_class=HTMLResponse)
    async def render_landing_page(
        request: Request,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = session_dependency,
        account_service: AccountService = account_service_dependency,
    ):
        performance_bundle = None
        performance_error = None
        performance_payload = None
        live_backtest_payload = None
        live_backtest_by_strategy_payload: dict[str, dict[str, object] | None] = {}
        try:
            live_backtest_by_strategy_payload = _build_live_backtest_payloads_by_strategy(settings)
        except Exception as exc:  # noqa: BLE001 - landing must stay available
            logger.warning("live_backtest_payload_unavailable: %s", exc)
        try:
            performance_bundle = load_performance_bundle(settings.runs_root)
            live_backtest_payload = _select_live_backtest_payload_for_strategy(
                live_backtest_by_strategy_payload,
                strategy_key=performance_bundle.default_key,
            )
            performance_payload = {
                "defaultKey": performance_bundle.default_key,
                "strategies": {
                    key: asdict(snapshot) for key, snapshot in performance_bundle.snapshots.items()
                },
                "liveBacktest": live_backtest_payload,
                "liveBacktestByStrategy": live_backtest_by_strategy_payload,
            }
        except PerformanceSnapshotError as exc:
            performance_error = str(exc)

        composition = None
        composition_error = None
        try:
            composition = _build_index_composition(
                settings,
                preferred_strategy_key=(
                    performance_bundle.default_key if performance_bundle is not None else "classic"
                ),
            )
        except (IndexCompositionError, FileNotFoundError, ValueError) as exc:
            composition_error = str(exc)

        account_portal_url = await _resolve_account_portal_url(
            request,
            settings,
            session,
            account_service,
        )

        context = {
            "request": request,
            "current_year": datetime.utcnow().year,
            "performance_bundle": performance_bundle,
            "performance_payload": performance_payload,
            "performance_error": performance_error,
            "live_backtest_payload": live_backtest_payload,
            "live_backtest_by_strategy_payload": live_backtest_by_strategy_payload,
            "composition": composition,
            "composition_error": composition_error,
            "api_url": api_url,
            "account_portal_url": account_portal_url,
        }
        return templates.TemplateResponse(request, "landing.html", context)

    @landing_router.get("/pricing", response_class=HTMLResponse, name="render_pricing_page")
    async def render_pricing_page(
        request: Request,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = session_dependency,
        account_service: AccountService = account_service_dependency,
    ) -> HTMLResponse:
        account_portal_url = await _resolve_account_portal_url(
            request,
            settings,
            session,
            account_service,
        )
        context = {
            "request": request,
            "current_year": datetime.utcnow().year,
            "api_url": api_url,
            "account_portal_url": account_portal_url,
        }
        return templates.TemplateResponse(request, "pricing.html", context)

    @landing_router.get("/cookie-policy", response_class=HTMLResponse, name="render_cookie_policy")
    async def render_cookie_policy(
        request: Request,
        settings: ServiceSettings = settings_dependency,
    ):
        ttl_seconds = max(int(getattr(settings.auth, "refresh_token_ttl_seconds", 0)), 0)

        def _format_ttl(seconds: int) -> str:
            duration = timedelta(seconds=seconds)
            if duration.days:
                return f"{duration.days} day{'s' if duration.days != 1 else ''}"
            hours = duration.seconds // 3600
            if hours:
                return f"{hours} hour{'s' if hours != 1 else ''}"
            minutes = (duration.seconds % 3600) // 60
            if minutes:
                return f"{minutes} minute{'s' if minutes != 1 else ''}"
            return f"{seconds} seconds"

        context = {
            "request": request,
            "current_year": datetime.utcnow().year,
            "last_updated": datetime.utcnow().strftime("%B %d, %Y"),
            "api_url": api_url,
            "session_cookie_name": settings.auth.session_cookie_name,
            "cookie_domain": settings.auth.session_cookie_domain,
            "cookie_secure": settings.auth.session_cookie_secure,
            "refresh_cookie_ttl_seconds": ttl_seconds,
            "refresh_cookie_ttl_human": _format_ttl(ttl_seconds),
        }
        return templates.TemplateResponse(request, "cookie_policy.html", context)

    @landing_router.get("/privacy", response_class=HTMLResponse)
    async def render_privacy_policy(request: Request):
        context = {
            "request": request,
            "current_year": datetime.utcnow().year,
            "last_updated": datetime.utcnow().strftime("%d %b %Y"),
            "api_url": api_url,
        }
        return templates.TemplateResponse(request, "privacy.html", context)

    @landing_router.get("/terms-of-service", response_class=HTMLResponse)
    async def render_terms_of_service(request: Request):
        context = {
            "request": request,
            "current_year": datetime.utcnow().year,
            "last_updated": "January 26, 2026",
            "api_url": api_url,
        }
        return templates.TemplateResponse(request, "terms.html", context)

    @landing_router.get("/auth/login", response_class=HTMLResponse, name="render_auth_login")
    async def render_auth_login(
        request: Request,
        next: str | None = Query(None),
        oauth_error: str | None = Query(None),
    ):
        next_url = next if next and _is_safe_redirect(next) else ""
        google_url = api_url("api_auth_google_login")
        if next_url:
            google_url = f"{google_url}?{urlencode({'next': next_url})}"
        oauth_error_message = ""
        if oauth_error == "access_denied":
            oauth_error_message = "Google sign-in was cancelled."
        elif oauth_error == "not_configured":
            oauth_error_message = "Google sign-in is not available right now."
        elif oauth_error:
            oauth_error_message = "Google sign-in failed. Try again or use email and password."
        context = {
            "request": request,
            "current_year": datetime.utcnow().year,
            "api_url": api_url,
            "google_login_url": google_url,
            "next_url": next_url,
            "oauth_error_message": oauth_error_message,
        }
        return templates.TemplateResponse(request, "auth/login.html", context)

    @landing_router.get("/auth/confirm", response_class=HTMLResponse)
    async def render_auth_confirm(request: Request, token: str | None = None):
        context = {
            "request": request,
            "current_year": datetime.utcnow().year,
            "token": token or "",
            "api_url": api_url,
        }
        return templates.TemplateResponse(request, "auth/confirm.html", context)

    @landing_router.get("/dashboard", include_in_schema=False)
    async def redirect_dashboard(request: Request) -> RedirectResponse:
        target_url = request.url_for("render_account_tab", tab="overview")
        return RedirectResponse(url=target_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    @landing_router.get("/developers", include_in_schema=False)
    async def redirect_developers(request: Request) -> RedirectResponse:
        target_url = request.url_for("render_docs")
        return RedirectResponse(url=target_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    @landing_router.get("/docs", response_class=HTMLResponse, name="render_docs")
    async def render_docs(
        request: Request,
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = session_dependency,
        account_service: AccountService = account_service_dependency,
    ):
        account_portal_url = await _resolve_account_portal_url(
            request,
            settings,
            session,
            account_service,
        )
        context = {
            "request": request,
            "current_year": datetime.utcnow().year,
            "api_url": api_url,
            "api_base": API_BASE_PATH,
            "account_portal_url": account_portal_url,
        }
        return templates.TemplateResponse(request, "docs.html", context)

    @landing_router.get("/downloads/sdk/{sdk_name}.zip", name="download_sdk_bundle")
    async def download_sdk_bundle(request: Request, sdk_name: str) -> StreamingResponse:
        normalized = sdk_name.lower()
        bundle = SDK_BUNDLES.get(normalized)
        if not bundle:
            raise HTTPException(status_code=404, detail="sdk_not_found")
        try:
            archive_buffer = _compose_sdk_bundle(bundle["sources"])
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"sdk_source_missing:{exc}") from exc

        content = archive_buffer.getvalue()
        filename = bundle["download_name"]
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-AICI-SDK": normalized,
        }
        return StreamingResponse(iter([content]), media_type="application/zip", headers=headers)

    def _compose_mini_context(request: Request, *, run_dir: Path, run_id: str) -> dict[str, object]:
        try:
            weights_rows = run_store.load_weights(run_dir)
            perf_metrics = run_store.load_perf(run_dir)
            equity_summary = run_store.load_equity_curve_summary(run_dir)
            chart_bytes = run_store.render_equity_curve_png(run_dir)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        weight_items = sorted(
            (models.WeightEntry(**row) for row in weights_rows),
            key=lambda entry: entry.weight,
            reverse=True,
        )
        total_weight = float(sum(entry.weight for entry in weight_items))
        encoded_chart = base64.b64encode(chart_bytes).decode("ascii")

        return {
            "request": request,
            "run_id": run_id,
            "weights": weight_items,
            "asset_count": len(weight_items),
            "total_weight": total_weight,
            "perf_metrics": perf_metrics,
            "equity_summary": equity_summary,
            "equity_curve_data_uri": f"data:image/png;base64,{encoded_chart}",
        }

    @landing_router.get("/runs/{run_id}/mini", response_class=HTMLResponse)
    async def render_run_mini(
        request: Request,
        run_id: str,
        settings: ServiceSettings = settings_dependency,
    ):
        try:
            run_dir = run_store.resolve_run_dir(settings, run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        context = _compose_mini_context(request, run_dir=run_dir, run_id=run_id)
        context["is_latest"] = False
        context["api_url"] = api_url
        return templates.TemplateResponse(request, "mini_result.html", context)

    @landing_router.get("/runs/latest/mini", response_class=HTMLResponse)
    async def render_latest_run_mini(
        request: Request,
        settings: ServiceSettings = settings_dependency,
    ):
        run_dir = _find_latest_index_run(settings)
        if run_dir is None:
            raise HTTPException(status_code=404, detail="no_runs_available")

        context = _compose_mini_context(request, run_dir=run_dir, run_id=run_dir.name)
        context["is_latest"] = True
        context["api_url"] = api_url
        return templates.TemplateResponse(request, "mini_result.html", context)

    landing_app.include_router(landing_router)

    def _build_legacy_redirect_target(request: Request, suffix: str | None = None) -> str:
        target_path = f"{API_PREFIX}{API_VERSION_ROUTE}"
        if suffix:
            normalized_suffix = suffix.strip("/")
            if normalized_suffix:
                target_path = f"{target_path}/{normalized_suffix}"
        return str(request.url.replace(path=target_path))

    @landing_app.api_route(
        API_VERSION_ROUTE,
        methods=["GET", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    async def landing_version_root_redirect(request: Request) -> RedirectResponse:
        target_url = _build_legacy_redirect_target(request)
        return RedirectResponse(url=target_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    @landing_app.get(f"{API_VERSION_ROUTE}/health", include_in_schema=False)
    async def landing_health_redirect() -> RedirectResponse:
        return RedirectResponse(
            url=f"{API_PREFIX}{API_VERSION_ROUTE}/health",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    @landing_app.api_route(
        f"{API_VERSION_ROUTE}/{{remaining_path:path}}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def landing_version_routes_redirect(
        request: Request,
        remaining_path: str,
    ) -> RedirectResponse:
        target_url = _build_legacy_redirect_target(request, suffix=remaining_path)
        return RedirectResponse(url=target_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    return landing_app


def create_admin_app(admin_config: dict[str, str] | None) -> FastAPI:
    admin_app = FastAPI(
        title="AI Crypto Index Admin",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    require_admin = _build_admin_dependency(admin_config)
    settings_dependency = Depends(get_settings)
    session_dependency = Depends(get_db_session)
    account_service_dependency = Depends(get_account_service)
    api_key_service_dependency = Depends(get_api_key_service)

    @admin_app.get(
        "/moderation",
        response_class=HTMLResponse,
        name="admin_moderation",
    )
    async def render_admin_moderation(
        request: Request,
        _: str = Depends(require_admin),
        settings: ServiceSettings = settings_dependency,
        session: AsyncSession = session_dependency,
        account_service: AccountService = account_service_dependency,
        api_key_service: ApiKeyService = api_key_service_dependency,
    ) -> HTMLResponse:
        accounts = await account_service.list_accounts(session, limit=100)
        profiles = []
        for account in accounts:
            profile = account_service.build_profile(account)
            plan = api_key_service.get_plan_for_account(account)
            profile["plan"] = {
                "code": plan.code,
                "daily_quota": plan.daily_quota,
                "monthly_quota": plan.monthly_quota,
                "burst_per_minute": plan.burst_per_minute,
                "burst_per_second": plan.burst_per_second,
                "data_latency_seconds": plan.data_latency_seconds,
            }
            profile["subscription_plan_code"] = (
                profile.get("subscription", {}).get("plan_code")
                if isinstance(profile.get("subscription"), dict)
                else None
            )
            profiles.append(profile)
        plan_options = sorted(
            [
                {
                    "code": plan.code,
                    "label": plan.code.upper(),
                    "daily_quota": plan.daily_quota,
                    "monthly_quota": plan.monthly_quota,
                    "burst_per_minute": plan.burst_per_minute,
                    "burst_per_second": plan.burst_per_second,
                    "data_latency_seconds": plan.data_latency_seconds,
                }
                for plan in settings.api_keys.plans.values()
            ],
            key=lambda item: item["code"],
        )
        role_options = [
            {"slug": role.slug, "name": role.name}
            for role in await account_service.list_roles(session)
        ]
        registrations = _load_recent_registrations(settings, limit=20)
        api_keys = await api_key_service.list_recent_keys(session, limit=12)
        api_key_cards = []
        for api_key in api_keys:
            plan, limits = api_key_service.derive_plan_and_limits(api_key, api_key.account)
            usage = await api_key_service.fetch_usage_snapshot(session, api_key)
            api_key_cards.append(
                _build_api_key_payload(
                    api_key,
                    plan=plan,
                    limits=limits,
                    usage=usage,
                    include_owner=True,
                )
            )
        perf_config, perf_snapshots, perf_benchmarks = _load_auto_config_with_latest(settings)
        perf_status = _build_performance_status_payload(perf_config, perf_snapshots, perf_benchmarks)
        index_auto_status = _build_index_auto_status_payload(settings)
        context = {
            "request": request,
            "accounts": profiles,
            "registrations": registrations,
            "api_base": API_BASE_PATH,
            "admin_section": "moderation",
            "api_keys": api_key_cards,
            "plan_options": plan_options,
            "plan_options_json": json.dumps(plan_options),
            "role_options": role_options,
            "performance_status": perf_status,
            "performance_status_json": json.dumps(perf_status),
            "index_auto_status": index_auto_status,
            "index_auto_status_json": json.dumps(index_auto_status),
        }
        return templates.TemplateResponse(request, "admin/moderation.html", context)

    @admin_app.get(
        "/cta-analytics",
        response_class=HTMLResponse,
        name="admin_cta_analytics",
    )
    async def render_admin_cta_analytics(
        request: Request,
        _: str = Depends(require_admin),
    ) -> HTMLResponse:
        context = {
            "request": request,
            "api_base": API_BASE_PATH,
            "admin_section": "cta_analytics",
        }
        return templates.TemplateResponse(request, "admin/cta_analytics.html", context)

    @admin_app.get(
        "/performance/status",
        response_model=models.AdminPerformanceStatus,
        name="admin_performance_status",
    )
    async def admin_performance_status(
        _: str = Depends(require_admin),
        settings: ServiceSettings = settings_dependency,
    ) -> dict[str, object]:
        config, snapshots, benchmarks = _load_auto_config_with_latest(settings)
        return _build_performance_status_payload(config, snapshots, benchmarks)

    @admin_app.post(
        "/performance/config",
        response_model=models.AdminPerformanceStatus,
        name="admin_update_performance_config",
    )
    async def admin_update_performance_config(
        payload: models.AdminPerformanceConfigRequest,
        _: str = Depends(require_admin),
        settings: ServiceSettings = settings_dependency,
    ) -> dict[str, object]:
        config, snapshots, benchmarks = _load_auto_config_with_latest(settings)
        if payload.enabled is not None:
            config.enabled = payload.enabled
        if payload.next_run_date:
            config.next_run_date = payload.next_run_date
        persist_auto_config(settings, config)
        return _build_performance_status_payload(config, snapshots, benchmarks)

    @admin_app.post(
        "/performance/run",
        response_model=models.AdminPerformanceStatus,
        name="admin_run_performance_refresh",
    )
    async def admin_run_performance_refresh(
        _: str = Depends(require_admin),
        settings: ServiceSettings = settings_dependency,
    ) -> dict[str, object]:
        config, snapshots, benchmarks = _load_auto_config_with_latest(settings)
        return await _trigger_performance_refresh(
            settings,
            reason="manual",
            config=config,
            snapshots=snapshots,
            benchmark_snapshots=benchmarks,
        )

    @admin_app.get(
        "/index-auto/status",
        response_model=models.AdminIndexAutoStatus,
        name="admin_index_auto_status",
    )
    async def admin_index_auto_status(
        _: str = Depends(require_admin),
        settings: ServiceSettings = settings_dependency,
    ) -> dict[str, object]:
        config = _load_index_auto_config(
            settings,
            latest_run_date=_latest_index_auto_run_date_across_profiles(settings),
        )
        return _build_index_auto_status_payload(settings, config=config)

    @admin_app.post(
        "/index-auto/config",
        response_model=models.AdminIndexAutoStatus,
        name="admin_update_index_auto_config",
    )
    async def admin_update_index_auto_config(
        payload: models.AdminPerformanceConfigRequest,
        _: str = Depends(require_admin),
        settings: ServiceSettings = settings_dependency,
    ) -> dict[str, object]:
        config = _load_index_auto_config(
            settings,
            latest_run_date=_latest_index_auto_run_date_across_profiles(settings),
        )
        if payload.enabled is not None:
            config.enabled = payload.enabled
        if payload.next_run_date is not None:
            config.next_run_date = payload.next_run_date
        _persist_index_auto_config(settings, config)
        return _build_index_auto_status_payload(settings, config=config)

    @admin_app.post(
        "/index-auto/run",
        response_model=models.AdminIndexAutoStatus,
        name="admin_run_index_auto",
    )
    async def admin_run_index_auto(
        _: str = Depends(require_admin),
        settings: ServiceSettings = settings_dependency,
    ) -> dict[str, object]:
        if _INDEX_AUTO_LOCK.locked():
            raise HTTPException(status.HTTP_409_CONFLICT, detail="index_auto_running")
        if os.getenv("AICI_ENABLE_PIPELINE", "1").lower() not in _PIPELINE_ENABLED_FLAGS:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="pipeline_disabled")

        config = _load_index_auto_config(
            settings,
            latest_run_date=_latest_index_auto_run_date_across_profiles(settings),
        )
        strategy_runs: list[dict[str, object]] = []
        async with _INDEX_AUTO_LOCK:
            try:
                with hold_monthly_job_lock(
                    settings.runs_root,
                    contour=_INDEX_AUTO_LOCK_CONTOUR,
                    target_month=date.today(),
                    stale_after_seconds=_MONTHLY_JOB_LOCK_STALE_SECONDS,
                ):
                    _INDEX_AUTO_STATUS.update({"state": "running", "started_at": datetime.utcnow(), "last_error": None})
                    strategy_runs = await _run_index_auto(settings, force=True, target_month=date.today())
                    config = _update_index_auto_after_success(
                        settings,
                        config,
                        strategy_runs=strategy_runs,
                    )
            except MonthlyJobLockBusyError:
                raise HTTPException(status.HTTP_409_CONFLICT, detail="index_auto_running")
            except HTTPException:
                config = _update_index_auto_after_failure(settings, config, "pipeline_disabled")
                raise
            except Exception as exc:  # noqa: BLE001
                config = _update_index_auto_after_failure(settings, config, str(exc))
                raise
        await _trigger_performance_refresh_after_index_auto(
            settings,
            strategy_runs=strategy_runs,
            reason="index_auto_manual",
        )
        return _build_index_auto_status_payload(settings, config=config)

    @admin_app.post(
        "/live-series/backfill",
        name="admin_backfill_live_series",
    )
    async def admin_backfill_live_series(
        overwrite: bool = False,
        _: str = Depends(require_admin),
        settings: ServiceSettings = settings_dependency,
    ) -> dict[str, object]:
        """Backfill stored live-month series for all existing auto runs.

        Safe to run multiple times — skips months that already have stored files
        unless overwrite=true is passed.
        """
        results: list[dict[str, object]] = []
        for profile in _index_auto_profiles():
            for run_dir in run_store.iter_completed_runs(settings, prefix=profile.run_prefix):
                try:
                    result = store_live_run_month(
                        settings,
                        run_dir,
                        profile.run_prefix,
                        overwrite=overwrite,
                    )
                    results.append(result)
                except Exception as exc:  # noqa: BLE001
                    results.append({"stored": False, "run_id": run_dir.name, "reason": str(exc)})
        stored_count = sum(1 for r in results if r.get("stored"))
        skipped_count = sum(1 for r in results if not r.get("stored"))
        return {"stored": stored_count, "skipped": skipped_count, "details": results}

    return admin_app


def create_app() -> FastAPI:
    _refresh_frontend_assets()
    global _PERFORMANCE_AUTO_ENABLED, _DAILY_SNAPSHOT_ENABLED, _BILLING_REMINDERS_ENABLED, _CTA_FORMAT_OPTIMIZATION_ENABLED
    _PERFORMANCE_AUTO_ENABLED = os.getenv("AICI_PERFORMANCE_AUTO_ENABLED", "1").lower() in _PIPELINE_ENABLED_FLAGS
    _DAILY_SNAPSHOT_ENABLED = os.getenv("AICI_DAILY_SNAPSHOT_ENABLED", "1").lower() in _PIPELINE_ENABLED_FLAGS
    _BILLING_REMINDERS_ENABLED = os.getenv("AICI_BILLING_REMINDERS_ENABLED", "1").lower() in _PIPELINE_ENABLED_FLAGS
    _CTA_FORMAT_OPTIMIZATION_ENABLED = (
        os.getenv("AICI_CTA_FORMAT_OPTIMIZATION_ENABLED", "1").lower() in _PIPELINE_ENABLED_FLAGS
    )
    root_app = FastAPI(
        title="AI Crypto Index Service",
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    admin_config = _resolve_admin_config()
    api_app = create_api_app(admin_config)
    landing_app = create_landing_app(api_app)
    admin_app = create_admin_app(admin_config)
    root_app.mount("/api", api_app)
    root_app.mount("/admin", admin_app)
    root_app.mount("/", landing_app)

    @root_app.on_event("startup")
    async def bootstrap_auth_components() -> None:
        settings = get_settings()
        await ensure_auth_schema(settings)
        session_factory = await get_auth_sessionmaker(settings)
        async with session_factory() as session:
            await accounts_bootstrap.ensure_default_roles(session)

    @root_app.on_event("startup")
    async def bootstrap_cta_analytics_store() -> None:
        settings = get_settings()
        await run_in_threadpool(cta_analytics_store.ensure_cta_analytics_schema, settings)

    @root_app.on_event("startup")
    async def bootstrap_performance_scheduler() -> None:
        if not _PERFORMANCE_AUTO_ENABLED:
            return
        settings = get_settings()
        await _maybe_run_performance_auto(settings)
        await _start_performance_scheduler(settings)

    @root_app.on_event("startup")
    async def bootstrap_daily_snapshot_scheduler() -> None:
        if not _DAILY_SNAPSHOT_ENABLED:
            return
        settings = get_settings()
        await _maybe_run_daily_snapshot(settings)
        await _start_daily_snapshot_scheduler(settings)

    @root_app.on_event("startup")
    async def bootstrap_index_auto_scheduler() -> None:
        settings = get_settings()
        await _start_index_auto_scheduler(settings)

    @root_app.on_event("startup")
    async def bootstrap_billing_reminder_scheduler() -> None:
        settings = get_settings()
        await _start_billing_reminder_scheduler(settings)

    @root_app.on_event("startup")
    async def bootstrap_cta_format_optimization_scheduler() -> None:
        settings = get_settings()
        await _maybe_run_cta_format_optimization(settings)
        await _start_cta_format_optimization_scheduler(settings)

    @root_app.on_event("shutdown")
    async def stop_performance_scheduler() -> None:
        await _stop_performance_scheduler()

    @root_app.on_event("shutdown")
    async def stop_daily_snapshot_scheduler() -> None:
        await _stop_daily_snapshot_scheduler()

    @root_app.on_event("shutdown")
    async def stop_index_auto_scheduler() -> None:
        await _stop_index_auto_scheduler()

    @root_app.on_event("shutdown")
    async def stop_billing_reminder_scheduler() -> None:
        await _stop_billing_reminder_scheduler()

    @root_app.on_event("shutdown")
    async def stop_cta_format_optimization_scheduler() -> None:
        await _stop_cta_format_optimization_scheduler()

    return root_app


app = create_app()

__all__ = [
    "API_BASE_PATH",
    "create_app",
    "create_api_app",
    "create_landing_app",
    "app",
]
