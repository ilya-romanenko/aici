from __future__ import annotations

import gzip
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from ai_crypto_index.shared import intake_store
from ai_crypto_index.shared.settings import ServiceSettings

ANALYTICS_DIR_NAME = "_analytics"
CTA_ANALYTICS_DB_FILE = "cta_analytics.db"
CTA_ARCHIVE_DIR_NAME = "archive"
CTA_ARCHIVE_NAMESPACE = "cta_analytics"
DEFAULT_FACT_RETENTION_DAYS = 90
DEFAULT_RETENTION_CHECK_SECONDS = 3600
DEFAULT_RETENTION_BATCH_SIZE = 5000
DEFAULT_ARCHIVE_FILE_RETENTION_DAYS = 365
DEFAULT_FORMAT_OPTIMIZATION_WINDOW_DAYS = 7
DEFAULT_FORMAT_OPTIMIZATION_TOP_N = 3
_CTA_CLICK_EVENT_TYPE = "cta_click"
_CTA_ALLOWED_EVENT_TYPES = {"cta_click", "signup_started", "email_confirmed", "paid"}
_CTA_FORMAT_STATUS_ACTIVE = "active"
_CTA_FORMAT_STATUS_PAUSED = "paused"

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY: set[Path] = set()
_RETENTION_LOCK = threading.Lock()
_RETENTION_LAST_RUN_TS: dict[Path, float] = {}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cta_events_fact (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_hour TEXT NOT NULL,
    event_type TEXT NOT NULL,
    cta_id TEXT NOT NULL,
    cta_format TEXT NOT NULL,
    location TEXT NOT NULL,
    location_norm TEXT NOT NULL,
    page_path TEXT,
    section TEXT,
    href TEXT,
    referer TEXT,
    user_agent TEXT,
    device_type TEXT NOT NULL,
    auth_state TEXT NOT NULL,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    utm_content TEXT,
    utm_term TEXT,
    unique_actor_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    dedup_signature TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_cta_events_fact_received_at
    ON cta_events_fact(received_at);
CREATE INDEX IF NOT EXISTS ix_cta_events_fact_event_date_cta_id
    ON cta_events_fact(event_date, cta_id);
CREATE INDEX IF NOT EXISTS ix_cta_events_fact_event_hour_cta_id
    ON cta_events_fact(event_hour, cta_id);
CREATE INDEX IF NOT EXISTS ix_cta_events_fact_cta_received_at
    ON cta_events_fact(cta_id, received_at);
CREATE INDEX IF NOT EXISTS ix_cta_events_fact_actor_received_at
    ON cta_events_fact(unique_actor_id, received_at);

CREATE TABLE IF NOT EXISTS cta_metrics_hourly (
    event_hour TEXT NOT NULL,
    event_date TEXT NOT NULL,
    cta_id TEXT NOT NULL,
    location_norm TEXT NOT NULL,
    device_type TEXT NOT NULL,
    auth_state TEXT NOT NULL,
    total_clicks INTEGER NOT NULL DEFAULT 0,
    unique_clicks INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (event_hour, cta_id, location_norm, device_type, auth_state)
);

CREATE INDEX IF NOT EXISTS ix_cta_metrics_hourly_cta_hour
    ON cta_metrics_hourly(cta_id, event_hour);
CREATE INDEX IF NOT EXISTS ix_cta_metrics_hourly_date
    ON cta_metrics_hourly(event_date);

CREATE TABLE IF NOT EXISTS cta_metrics_daily (
    event_date TEXT NOT NULL,
    cta_id TEXT NOT NULL,
    location_norm TEXT NOT NULL,
    device_type TEXT NOT NULL,
    auth_state TEXT NOT NULL,
    total_clicks INTEGER NOT NULL DEFAULT 0,
    unique_clicks INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (event_date, cta_id, location_norm, device_type, auth_state)
);

CREATE INDEX IF NOT EXISTS ix_cta_metrics_daily_cta_date
    ON cta_metrics_daily(cta_id, event_date);

CREATE TABLE IF NOT EXISTS cta_metrics_hourly_actor_unique (
    event_hour TEXT NOT NULL,
    cta_id TEXT NOT NULL,
    location_norm TEXT NOT NULL,
    device_type TEXT NOT NULL,
    auth_state TEXT NOT NULL,
    unique_actor_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (event_hour, cta_id, location_norm, device_type, auth_state, unique_actor_id)
);

CREATE TABLE IF NOT EXISTS cta_metrics_daily_actor_unique (
    event_date TEXT NOT NULL,
    cta_id TEXT NOT NULL,
    location_norm TEXT NOT NULL,
    device_type TEXT NOT NULL,
    auth_state TEXT NOT NULL,
    unique_actor_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (event_date, cta_id, location_norm, device_type, auth_state, unique_actor_id)
);

CREATE TABLE IF NOT EXISTS cta_event_metrics_hourly (
    event_hour TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    cta_id TEXT NOT NULL,
    cta_format TEXT NOT NULL,
    location_norm TEXT NOT NULL,
    page_path TEXT NOT NULL,
    utm_source TEXT NOT NULL,
    device_type TEXT NOT NULL,
    auth_state TEXT NOT NULL,
    total_events INTEGER NOT NULL DEFAULT 0,
    unique_actors INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
        event_hour,
        event_type,
        cta_id,
        cta_format,
        location_norm,
        page_path,
        utm_source,
        device_type,
        auth_state
    )
);

CREATE INDEX IF NOT EXISTS ix_cta_event_metrics_hourly_type_hour
    ON cta_event_metrics_hourly(event_type, event_hour);

CREATE TABLE IF NOT EXISTS cta_event_metrics_daily (
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    cta_id TEXT NOT NULL,
    cta_format TEXT NOT NULL,
    location_norm TEXT NOT NULL,
    page_path TEXT NOT NULL,
    utm_source TEXT NOT NULL,
    device_type TEXT NOT NULL,
    auth_state TEXT NOT NULL,
    total_events INTEGER NOT NULL DEFAULT 0,
    unique_actors INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
        event_date,
        event_type,
        cta_id,
        cta_format,
        location_norm,
        page_path,
        utm_source,
        device_type,
        auth_state
    )
);

CREATE INDEX IF NOT EXISTS ix_cta_event_metrics_daily_type_date
    ON cta_event_metrics_daily(event_type, event_date);

CREATE TABLE IF NOT EXISTS cta_event_metrics_hourly_actor_unique (
    event_hour TEXT NOT NULL,
    event_type TEXT NOT NULL,
    cta_id TEXT NOT NULL,
    cta_format TEXT NOT NULL,
    location_norm TEXT NOT NULL,
    page_path TEXT NOT NULL,
    utm_source TEXT NOT NULL,
    device_type TEXT NOT NULL,
    auth_state TEXT NOT NULL,
    unique_actor_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (
        event_hour,
        event_type,
        cta_id,
        cta_format,
        location_norm,
        page_path,
        utm_source,
        device_type,
        auth_state,
        unique_actor_id
    )
);

CREATE TABLE IF NOT EXISTS cta_event_metrics_daily_actor_unique (
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    cta_id TEXT NOT NULL,
    cta_format TEXT NOT NULL,
    location_norm TEXT NOT NULL,
    page_path TEXT NOT NULL,
    utm_source TEXT NOT NULL,
    device_type TEXT NOT NULL,
    auth_state TEXT NOT NULL,
    unique_actor_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (
        event_date,
        event_type,
        cta_id,
        cta_format,
        location_norm,
        page_path,
        utm_source,
        device_type,
        auth_state,
        unique_actor_id
    )
);

CREATE TABLE IF NOT EXISTS cta_fact_retention_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    cutoff_at TEXT NOT NULL,
    archived_rows INTEGER NOT NULL,
    deleted_rows INTEGER NOT NULL,
    archive_path TEXT,
    status TEXT NOT NULL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS cta_ingestion_quality_hourly (
    event_hour TEXT PRIMARY KEY,
    total_events INTEGER NOT NULL DEFAULT 0,
    accepted_events INTEGER NOT NULL DEFAULT 0,
    duplicate_events INTEGER NOT NULL DEFAULT 0,
    invalid_events INTEGER NOT NULL DEFAULT 0,
    last_event_id TEXT,
    last_received_at TEXT,
    last_error_reason TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_cta_ingestion_quality_hourly_updated_at
    ON cta_ingestion_quality_hourly(updated_at);

CREATE TABLE IF NOT EXISTS cta_format_optimization_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decided_at TEXT NOT NULL,
    window_start_at TEXT NOT NULL,
    window_end_at TEXT NOT NULL,
    window_days INTEGER NOT NULL,
    top_n INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ranking_json TEXT NOT NULL,
    top_formats_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_cta_format_optimization_runs_decided_at
    ON cta_format_optimization_runs(decided_at DESC);

CREATE TABLE IF NOT EXISTS cta_format_status_current (
    cta_format TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    rank INTEGER,
    ctr REAL NOT NULL DEFAULT 0,
    signup_cr REAL NOT NULL DEFAULT 0,
    total_clicks INTEGER NOT NULL DEFAULT 0,
    unique_clicks INTEGER NOT NULL DEFAULT 0,
    signup_users INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    last_decision_id INTEGER,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_cta_format_status_current_status
    ON cta_format_status_current(status);

CREATE TABLE IF NOT EXISTS cta_format_status_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL,
    cta_format TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    rank INTEGER,
    ctr REAL NOT NULL DEFAULT 0,
    signup_cr REAL NOT NULL DEFAULT 0,
    total_clicks INTEGER NOT NULL DEFAULT 0,
    unique_clicks INTEGER NOT NULL DEFAULT 0,
    signup_users INTEGER NOT NULL DEFAULT 0,
    change_reason TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_cta_format_status_log_decision_id
    ON cta_format_status_log(decision_id);

CREATE INDEX IF NOT EXISTS ix_cta_format_status_log_changed_at
    ON cta_format_status_log(changed_at DESC);
"""


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        parsed = int(raw.strip())
    except ValueError:
        return default
    return parsed


def _normalize_positive_int(value: int, fallback: int) -> int:
    return value if value > 0 else fallback


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_cta_analytics_root(settings: ServiceSettings) -> Path:
    return resolve_cta_analytics_root_from_runs_root(settings.runs_root)


def resolve_cta_analytics_root_from_runs_root(runs_root: Path) -> Path:
    root = Path(runs_root) / ANALYTICS_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_cta_analytics_db_path(settings: ServiceSettings) -> Path:
    return resolve_cta_analytics_db_path_from_runs_root(settings.runs_root)


def resolve_cta_analytics_db_path_from_runs_root(runs_root: Path) -> Path:
    root = resolve_cta_analytics_root_from_runs_root(runs_root)
    return root / CTA_ANALYTICS_DB_FILE


def _archive_root(settings: ServiceSettings) -> Path:
    root = (
        settings.runs_root
        / intake_store.INTAKE_DIR_NAME
        / CTA_ARCHIVE_DIR_NAME
        / CTA_ARCHIVE_NAMESPACE
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30.0)
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA synchronous=NORMAL;")
    connection.execute("PRAGMA foreign_keys=ON;")
    connection.row_factory = sqlite3.Row
    return connection


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_column(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> None:
    if column_name in _table_columns(connection, table_name):
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def _migrate_schema(connection: sqlite3.Connection) -> None:
    _ensure_column(
        connection,
        table_name="cta_events_fact",
        column_name="event_type",
        column_sql=f"TEXT NOT NULL DEFAULT '{_CTA_CLICK_EVENT_TYPE}'",
    )
    _ensure_column(
        connection,
        table_name="cta_events_fact",
        column_name="cta_format",
        column_sql="TEXT NOT NULL DEFAULT 'unknown'",
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_cta_events_fact_event_type_date ON cta_events_fact(event_type, event_date);"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_cta_events_fact_format_date ON cta_events_fact(cta_format, event_date);"
    )


def ensure_cta_analytics_schema(settings: ServiceSettings) -> None:
    db_path = resolve_cta_analytics_db_path(settings)
    with _SCHEMA_LOCK:
        if db_path in _SCHEMA_READY:
            return
    with _connect(db_path) as connection:
        connection.executescript(_SCHEMA_SQL)
        _migrate_schema(connection)
    with _SCHEMA_LOCK:
        _SCHEMA_READY.add(db_path)


def _parse_received_at(value: object | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = _utc_now()
    else:
        parsed = _utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_text(value: object | None, *, max_length: int = 320) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        return text[:max_length]
    return text


def _normalize_lower_text(value: object | None, *, max_length: int = 320) -> str | None:
    normalized = _normalize_text(value, max_length=max_length)
    if normalized is None:
        return None
    lowered = normalized.lower()
    return lowered or None


def _extract_path(candidate: str | None) -> str | None:
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.path:
        return parsed.path
    if candidate.startswith("/"):
        return candidate
    return None


def _normalize_page_path(value: object | None, *, fallback: str | None = None) -> str:
    normalized = _normalize_lower_text(value, max_length=240) or _normalize_lower_text(fallback, max_length=240) or "/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized.lstrip('/')}"
    return normalized


def _extract_utm_value(field: str, *, href: str | None, referer: str | None) -> str | None:
    for candidate in (href, referer):
        if not candidate:
            continue
        try:
            parsed = urlparse(candidate)
        except ValueError:
            continue
        for key, value in parse_qsl(parsed.query, keep_blank_values=False):
            if key.strip().lower() == field:
                normalized = _normalize_lower_text(value, max_length=200)
                if normalized:
                    return normalized
    return None


def _normalize_event_type(value: object | None) -> str:
    normalized = _normalize_lower_text(value, max_length=64)
    if normalized in _CTA_ALLOWED_EVENT_TYPES:
        return normalized
    return _CTA_CLICK_EVENT_TYPE


def _normalize_cta_format(value: object | None) -> str:
    return _normalize_lower_text(value, max_length=120) or "unknown"


def _detect_device_type(user_agent: str | None) -> str:
    if not user_agent:
        return "unknown"
    ua = user_agent.lower()
    if any(token in ua for token in ("bot", "spider", "crawl", "slurp", "headless")):
        return "bot"
    if any(token in ua for token in ("ipad", "tablet")):
        return "tablet"
    if any(token in ua for token in ("iphone", "android", "mobile")):
        return "mobile"
    if any(token in ua for token in ("windows", "macintosh", "linux", "x11")):
        return "desktop"
    return "unknown"


def _materialize_fact(record: dict[str, object]) -> dict[str, str]:
    received_at_dt = _parse_received_at(record.get("received_at"))
    event_timestamp_dt = _parse_received_at(record.get("timestamp") or record.get("received_at"))
    event_hour_dt = event_timestamp_dt.replace(minute=0, second=0, microsecond=0)
    received_at = received_at_dt.isoformat()
    event_hour = event_hour_dt.isoformat()
    event_date = event_timestamp_dt.date().isoformat()

    cta_id = (_normalize_text(record.get("cta_id"), max_length=120) or "unknown").lower()
    event_type = _normalize_event_type(record.get("event_type"))
    cta_format = _normalize_cta_format(record.get("cta_format"))
    location_norm = (_normalize_text(record.get("location"), max_length=160) or "unknown").lower()
    location = (_normalize_text(record.get("location_raw"), max_length=160) or location_norm).lower()
    href = _normalize_text(record.get("href"), max_length=320)
    referer = _normalize_text(record.get("referer"), max_length=600)
    user_agent = _normalize_text(record.get("user_agent"), max_length=400)

    metadata_raw = record.get("metadata")
    metadata: dict[str, object] = metadata_raw if isinstance(metadata_raw, dict) else {}
    page_path = _normalize_page_path(
        record.get("page_path") or metadata.get("page_path"),
        fallback=_extract_path(href) or _extract_path(referer),
    )
    section = _normalize_text(metadata.get("section"), max_length=120) or location_norm
    auth_state = (_normalize_text(metadata.get("auth_state"), max_length=40) or "anonymous").lower()
    if auth_state not in {"anonymous", "authenticated"}:
        auth_state = "anonymous"

    utm_source = _normalize_lower_text(record.get("utm_source"), max_length=200) or _normalize_lower_text(
        metadata.get("utm_source"),
        max_length=200,
    ) or _extract_utm_value("utm_source", href=href, referer=referer)
    utm_medium = _normalize_lower_text(record.get("utm_medium"), max_length=200) or _normalize_lower_text(
        metadata.get("utm_medium"),
        max_length=200,
    ) or _extract_utm_value("utm_medium", href=href, referer=referer)
    utm_campaign = _normalize_lower_text(record.get("utm_campaign"), max_length=200) or _normalize_lower_text(
        metadata.get("utm_campaign"),
        max_length=200,
    ) or _extract_utm_value("utm_campaign", href=href, referer=referer)
    utm_content = _normalize_lower_text(record.get("utm_content"), max_length=200) or _normalize_lower_text(
        metadata.get("utm_content"),
        max_length=200,
    ) or _extract_utm_value("utm_content", href=href, referer=referer)
    utm_term = _normalize_lower_text(record.get("utm_term"), max_length=200) or _normalize_lower_text(
        metadata.get("utm_term"),
        max_length=200,
    ) or _extract_utm_value("utm_term", href=href, referer=referer)

    return {
        "event_id": _normalize_text(record.get("event_id"), max_length=80) or "",
        "received_at": received_at,
        "event_date": event_date,
        "event_hour": event_hour,
        "event_type": event_type,
        "cta_id": cta_id,
        "cta_format": cta_format,
        "location": location,
        "location_norm": location_norm,
        "page_path": page_path,
        "section": section,
        "href": href or "",
        "referer": referer or "",
        "user_agent": user_agent or "",
        "device_type": _detect_device_type(user_agent),
        "auth_state": auth_state,
        "utm_source": utm_source or "",
        "utm_medium": utm_medium or "",
        "utm_campaign": utm_campaign or "",
        "utm_content": utm_content or "",
        "utm_term": utm_term or "",
        "unique_actor_id": _normalize_text(record.get("unique_actor_id"), max_length=120) or "anonymous",
        "metadata_json": json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        "dedup_signature": _normalize_text(record.get("dedup_signature"), max_length=80) or "",
        "created_at": _utc_now().isoformat(),
    }


def persist_cta_analytics_record(settings: ServiceSettings, record: dict[str, object]) -> bool:
    ensure_cta_analytics_schema(settings)
    fact = _materialize_fact(record)
    if not fact["event_id"]:
        return False

    db_path = resolve_cta_analytics_db_path(settings)
    inserted = False
    with _connect(db_path) as connection:
        connection.execute("BEGIN;")
        try:
            insert_result = connection.execute(
                """
                INSERT OR IGNORE INTO cta_events_fact (
                    event_id, received_at, event_date, event_hour, event_type, cta_id, cta_format, location, location_norm,
                    page_path, section, href, referer, user_agent, device_type, auth_state,
                    utm_source, utm_medium, utm_campaign, utm_content, utm_term,
                    unique_actor_id, metadata_json, dedup_signature, created_at
                ) VALUES (
                    :event_id, :received_at, :event_date, :event_hour, :event_type, :cta_id, :cta_format, :location, :location_norm,
                    :page_path, :section, :href, :referer, :user_agent, :device_type, :auth_state,
                    :utm_source, :utm_medium, :utm_campaign, :utm_content, :utm_term,
                    :unique_actor_id, :metadata_json, :dedup_signature, :created_at
                )
                """,
                fact,
            )
            inserted = insert_result.rowcount > 0
            if inserted:
                _update_aggregates(connection, fact)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    maybe_apply_fact_retention(settings)
    return inserted


def _update_aggregates(connection: sqlite3.Connection, fact: dict[str, str]) -> None:
    updated_at = _utc_now().isoformat()
    _update_event_aggregates(connection, fact, updated_at=updated_at)
    if fact["event_type"] != _CTA_CLICK_EVENT_TYPE:
        return
    _update_click_aggregates(connection, fact, updated_at=updated_at)


def _update_event_aggregates(
    connection: sqlite3.Connection,
    fact: dict[str, str],
    *,
    updated_at: str,
) -> None:
    hourly_key = {
        "event_hour": fact["event_hour"],
        "event_date": fact["event_date"],
        "event_type": fact["event_type"],
        "cta_id": fact["cta_id"],
        "cta_format": fact["cta_format"],
        "location_norm": fact["location_norm"],
        "page_path": fact["page_path"],
        "utm_source": fact["utm_source"],
        "device_type": fact["device_type"],
        "auth_state": fact["auth_state"],
        "updated_at": updated_at,
    }
    connection.execute(
        """
        INSERT INTO cta_event_metrics_hourly (
            event_hour, event_date, event_type, cta_id, cta_format, location_norm, page_path, utm_source,
            device_type, auth_state, total_events, unique_actors, updated_at
        ) VALUES (
            :event_hour, :event_date, :event_type, :cta_id, :cta_format, :location_norm, :page_path, :utm_source,
            :device_type, :auth_state, 1, 0, :updated_at
        )
        ON CONFLICT(event_hour, event_type, cta_id, cta_format, location_norm, page_path, utm_source, device_type, auth_state)
        DO UPDATE SET
            total_events = cta_event_metrics_hourly.total_events + 1,
            updated_at = excluded.updated_at
        """,
        hourly_key,
    )
    hourly_unique_insert = connection.execute(
        """
        INSERT OR IGNORE INTO cta_event_metrics_hourly_actor_unique (
            event_hour, event_type, cta_id, cta_format, location_norm, page_path, utm_source, device_type, auth_state,
            unique_actor_id, first_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact["event_hour"],
            fact["event_type"],
            fact["cta_id"],
            fact["cta_format"],
            fact["location_norm"],
            fact["page_path"],
            fact["utm_source"],
            fact["device_type"],
            fact["auth_state"],
            fact["unique_actor_id"],
            fact["received_at"],
        ),
    )
    if hourly_unique_insert.rowcount > 0:
        connection.execute(
            """
            UPDATE cta_event_metrics_hourly
            SET unique_actors = unique_actors + 1, updated_at = ?
            WHERE event_hour = ? AND event_type = ? AND cta_id = ? AND cta_format = ? AND location_norm = ?
                AND page_path = ? AND utm_source = ? AND device_type = ? AND auth_state = ?
            """,
            (
                updated_at,
                fact["event_hour"],
                fact["event_type"],
                fact["cta_id"],
                fact["cta_format"],
                fact["location_norm"],
                fact["page_path"],
                fact["utm_source"],
                fact["device_type"],
                fact["auth_state"],
            ),
        )

    daily_key = {
        "event_date": fact["event_date"],
        "event_type": fact["event_type"],
        "cta_id": fact["cta_id"],
        "cta_format": fact["cta_format"],
        "location_norm": fact["location_norm"],
        "page_path": fact["page_path"],
        "utm_source": fact["utm_source"],
        "device_type": fact["device_type"],
        "auth_state": fact["auth_state"],
        "updated_at": updated_at,
    }
    connection.execute(
        """
        INSERT INTO cta_event_metrics_daily (
            event_date, event_type, cta_id, cta_format, location_norm, page_path, utm_source, device_type, auth_state,
            total_events, unique_actors, updated_at
        ) VALUES (
            :event_date, :event_type, :cta_id, :cta_format, :location_norm, :page_path, :utm_source, :device_type, :auth_state,
            1, 0, :updated_at
        )
        ON CONFLICT(event_date, event_type, cta_id, cta_format, location_norm, page_path, utm_source, device_type, auth_state)
        DO UPDATE SET
            total_events = cta_event_metrics_daily.total_events + 1,
            updated_at = excluded.updated_at
        """,
        daily_key,
    )
    daily_unique_insert = connection.execute(
        """
        INSERT OR IGNORE INTO cta_event_metrics_daily_actor_unique (
            event_date, event_type, cta_id, cta_format, location_norm, page_path, utm_source, device_type, auth_state,
            unique_actor_id, first_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact["event_date"],
            fact["event_type"],
            fact["cta_id"],
            fact["cta_format"],
            fact["location_norm"],
            fact["page_path"],
            fact["utm_source"],
            fact["device_type"],
            fact["auth_state"],
            fact["unique_actor_id"],
            fact["received_at"],
        ),
    )
    if daily_unique_insert.rowcount > 0:
        connection.execute(
            """
            UPDATE cta_event_metrics_daily
            SET unique_actors = unique_actors + 1, updated_at = ?
            WHERE event_date = ? AND event_type = ? AND cta_id = ? AND cta_format = ? AND location_norm = ?
                AND page_path = ? AND utm_source = ? AND device_type = ? AND auth_state = ?
            """,
            (
                updated_at,
                fact["event_date"],
                fact["event_type"],
                fact["cta_id"],
                fact["cta_format"],
                fact["location_norm"],
                fact["page_path"],
                fact["utm_source"],
                fact["device_type"],
                fact["auth_state"],
            ),
        )


def _update_click_aggregates(
    connection: sqlite3.Connection,
    fact: dict[str, str],
    *,
    updated_at: str,
) -> None:
    hourly_key = {
        "event_hour": fact["event_hour"],
        "event_date": fact["event_date"],
        "cta_id": fact["cta_id"],
        "location_norm": fact["location_norm"],
        "device_type": fact["device_type"],
        "auth_state": fact["auth_state"],
        "updated_at": updated_at,
    }
    connection.execute(
        """
        INSERT INTO cta_metrics_hourly (
            event_hour, event_date, cta_id, location_norm, device_type, auth_state, total_clicks, unique_clicks, updated_at
        ) VALUES (
            :event_hour, :event_date, :cta_id, :location_norm, :device_type, :auth_state, 1, 0, :updated_at
        )
        ON CONFLICT(event_hour, cta_id, location_norm, device_type, auth_state)
        DO UPDATE SET
            total_clicks = cta_metrics_hourly.total_clicks + 1,
            updated_at = excluded.updated_at
        """,
        hourly_key,
    )
    hourly_unique_insert = connection.execute(
        """
        INSERT OR IGNORE INTO cta_metrics_hourly_actor_unique (
            event_hour, cta_id, location_norm, device_type, auth_state, unique_actor_id, first_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact["event_hour"],
            fact["cta_id"],
            fact["location_norm"],
            fact["device_type"],
            fact["auth_state"],
            fact["unique_actor_id"],
            fact["received_at"],
        ),
    )
    if hourly_unique_insert.rowcount > 0:
        connection.execute(
            """
            UPDATE cta_metrics_hourly
            SET unique_clicks = unique_clicks + 1, updated_at = ?
            WHERE event_hour = ? AND cta_id = ? AND location_norm = ? AND device_type = ? AND auth_state = ?
            """,
            (
                updated_at,
                fact["event_hour"],
                fact["cta_id"],
                fact["location_norm"],
                fact["device_type"],
                fact["auth_state"],
            ),
        )

    daily_key = {
        "event_date": fact["event_date"],
        "cta_id": fact["cta_id"],
        "location_norm": fact["location_norm"],
        "device_type": fact["device_type"],
        "auth_state": fact["auth_state"],
        "updated_at": updated_at,
    }
    connection.execute(
        """
        INSERT INTO cta_metrics_daily (
            event_date, cta_id, location_norm, device_type, auth_state, total_clicks, unique_clicks, updated_at
        ) VALUES (
            :event_date, :cta_id, :location_norm, :device_type, :auth_state, 1, 0, :updated_at
        )
        ON CONFLICT(event_date, cta_id, location_norm, device_type, auth_state)
        DO UPDATE SET
            total_clicks = cta_metrics_daily.total_clicks + 1,
            updated_at = excluded.updated_at
        """,
        daily_key,
    )
    daily_unique_insert = connection.execute(
        """
        INSERT OR IGNORE INTO cta_metrics_daily_actor_unique (
            event_date, cta_id, location_norm, device_type, auth_state, unique_actor_id, first_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact["event_date"],
            fact["cta_id"],
            fact["location_norm"],
            fact["device_type"],
            fact["auth_state"],
            fact["unique_actor_id"],
            fact["received_at"],
        ),
    )
    if daily_unique_insert.rowcount > 0:
        connection.execute(
            """
            UPDATE cta_metrics_daily
            SET unique_clicks = unique_clicks + 1, updated_at = ?
            WHERE event_date = ? AND cta_id = ? AND location_norm = ? AND device_type = ? AND auth_state = ?
            """,
            (
                updated_at,
                fact["event_date"],
                fact["cta_id"],
                fact["location_norm"],
                fact["device_type"],
                fact["auth_state"],
            ),
        )


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _normalize_cta_format_status(value: object | None) -> str:
    normalized = _normalize_lower_text(value, max_length=24)
    if normalized == _CTA_FORMAT_STATUS_ACTIVE:
        return _CTA_FORMAT_STATUS_ACTIVE
    return _CTA_FORMAT_STATUS_PAUSED


def _build_cta_format_weekly_ranking(
    connection: sqlite3.Connection,
    *,
    window_start_at: datetime,
    window_end_at: datetime,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            clicks.cta_format AS cta_format,
            clicks.total_clicks AS total_clicks,
            clicks.unique_clicks AS unique_clicks,
            COALESCE(signups.signup_users, 0) AS signup_users
        FROM (
            SELECT
                cta_format,
                COUNT(*) AS total_clicks,
                COUNT(DISTINCT unique_actor_id) AS unique_clicks
            FROM cta_events_fact
            WHERE event_type = ? AND received_at >= ? AND received_at < ? AND cta_format <> 'unknown'
            GROUP BY cta_format
        ) AS clicks
        LEFT JOIN (
            SELECT
                cta_format,
                COUNT(DISTINCT unique_actor_id) AS signup_users
            FROM cta_events_fact
            WHERE event_type = ? AND received_at >= ? AND received_at < ? AND cta_format <> 'unknown'
            GROUP BY cta_format
        ) AS signups
            ON signups.cta_format = clicks.cta_format
        """,
        (
            _CTA_CLICK_EVENT_TYPE,
            window_start_at.isoformat(),
            window_end_at.isoformat(),
            "signup_started",
            window_start_at.isoformat(),
            window_end_at.isoformat(),
        ),
    ).fetchall()

    ranking: list[dict[str, Any]] = []
    for row in rows:
        cta_format = _normalize_cta_format(row["cta_format"])
        if cta_format == "unknown":
            continue
        total_clicks = int(row["total_clicks"] or 0)
        unique_clicks = int(row["unique_clicks"] or 0)
        signup_users = int(row["signup_users"] or 0)
        ranking.append(
            {
                "cta_format": cta_format,
                "total_clicks": total_clicks,
                "unique_clicks": unique_clicks,
                "signup_users": signup_users,
                "ctr": _safe_ratio(unique_clicks, total_clicks),
                "signup_cr": _safe_ratio(signup_users, unique_clicks),
            }
        )

    ranking.sort(
        key=lambda item: (
            -float(item["signup_cr"]),
            -float(item["ctr"]),
            -int(item["unique_clicks"]),
            -int(item["total_clicks"]),
            str(item["cta_format"]),
        )
    )
    for index, item in enumerate(ranking, start=1):
        item["rank"] = index
    return ranking


def _build_cta_format_change_reason(
    *,
    top_n: int,
    rank: int | None,
    ctr: float,
    signup_cr: float,
    next_status: str,
) -> str:
    if rank is None:
        return (
            f"Outside top-{top_n}; set {next_status}. "
            f"signup_cr={signup_cr:.4f}, ctr={ctr:.4f}."
        )
    return (
        f"Rank #{rank} in top-{top_n}; set {next_status}. "
        f"signup_cr={signup_cr:.4f}, ctr={ctr:.4f}."
    )


def _default_format_metric_payload(cta_format: str) -> dict[str, Any]:
    return {
        "cta_format": cta_format,
        "total_clicks": 0,
        "unique_clicks": 0,
        "signup_users": 0,
        "ctr": 0.0,
        "signup_cr": 0.0,
        "rank": None,
    }


def _parse_json(raw: object | None, fallback: Any) -> Any:
    if not isinstance(raw, str) or not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _load_decision_status_changes(
    connection: sqlite3.Connection,
    *,
    decision_id: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            cta_format,
            previous_status,
            new_status,
            rank,
            ctr,
            signup_cr,
            total_clicks,
            unique_clicks,
            signup_users,
            change_reason,
            changed_at
        FROM cta_format_status_log
        WHERE decision_id = ?
        ORDER BY cta_format ASC
        """,
        (decision_id,),
    ).fetchall()
    return [
        {
            "cta_format": str(row["cta_format"] or "unknown"),
            "previous_status": _normalize_cta_format_status(row["previous_status"]),
            "new_status": _normalize_cta_format_status(row["new_status"]),
            "rank": int(row["rank"]) if row["rank"] is not None else None,
            "ctr": float(row["ctr"] or 0.0),
            "signup_cr": float(row["signup_cr"] or 0.0),
            "total_clicks": int(row["total_clicks"] or 0),
            "unique_clicks": int(row["unique_clicks"] or 0),
            "signup_users": int(row["signup_users"] or 0),
            "reason": str(row["change_reason"] or ""),
            "changed_at": str(row["changed_at"] or ""),
        }
        for row in rows
    ]


def _materialize_format_decision(
    row: sqlite3.Row,
    *,
    status_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    ranking = _parse_json(row["ranking_json"], [])
    if not isinstance(ranking, list):
        ranking = []
    top_formats = _parse_json(row["top_formats_json"], [])
    if not isinstance(top_formats, list):
        top_formats = []

    return {
        "id": int(row["id"]),
        "decided_at": str(row["decided_at"]),
        "window_start_at": str(row["window_start_at"]),
        "window_end_at": str(row["window_end_at"]),
        "window_days": int(row["window_days"] or 0),
        "top_n": int(row["top_n"] or 0),
        "reason": str(row["reason"] or ""),
        "top_formats": [str(item) for item in top_formats],
        "ranking": ranking,
        "status_changes": status_changes,
        "changed_formats": len(status_changes),
    }


def run_weekly_cta_format_optimization(
    settings: ServiceSettings,
    *,
    now: datetime | None = None,
    window_days: int = DEFAULT_FORMAT_OPTIMIZATION_WINDOW_DAYS,
    top_n: int = DEFAULT_FORMAT_OPTIMIZATION_TOP_N,
) -> dict[str, Any]:
    ensure_cta_analytics_schema(settings)
    window_days_normalized = window_days if window_days > 0 else DEFAULT_FORMAT_OPTIMIZATION_WINDOW_DAYS
    top_n_normalized = top_n if top_n > 0 else DEFAULT_FORMAT_OPTIMIZATION_TOP_N
    decision_time = _parse_received_at(now) if now is not None else _utc_now()
    window_start_at = decision_time - timedelta(days=window_days_normalized)
    decision_reason = (
        f"Weekly optimization: top-{top_n_normalized} by signup_cr desc, ctr desc, "
        f"unique_clicks desc, total_clicks desc over last {window_days_normalized} days."
    )

    db_path = resolve_cta_analytics_db_path(settings)
    with _connect(db_path) as connection:
        ranking = _build_cta_format_weekly_ranking(
            connection,
            window_start_at=window_start_at,
            window_end_at=decision_time,
        )
        top_formats = [str(item["cta_format"]) for item in ranking[:top_n_normalized]]
        top_formats_set = set(top_formats)
        rank_by_format = {
            str(item["cta_format"]): int(item["rank"])
            for item in ranking
            if item.get("rank") is not None
        }
        metrics_by_format = {str(item["cta_format"]): dict(item) for item in ranking}

        existing_rows = connection.execute(
            """
            SELECT cta_format, status
            FROM cta_format_status_current
            """
        ).fetchall()
        existing_status_map = {
            str(row["cta_format"]): _normalize_cta_format_status(row["status"])
            for row in existing_rows
        }
        all_formats = sorted(set(existing_status_map.keys()) | set(metrics_by_format.keys()))

        now_iso = decision_time.isoformat()
        window_start_iso = window_start_at.isoformat()
        ranking_json = json.dumps(ranking, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        top_formats_json = json.dumps(top_formats, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

        status_changes: list[dict[str, Any]] = []
        decision_id = 0
        connection.execute("BEGIN;")
        try:
            decision_insert = connection.execute(
                """
                INSERT INTO cta_format_optimization_runs (
                    decided_at,
                    window_start_at,
                    window_end_at,
                    window_days,
                    top_n,
                    reason,
                    ranking_json,
                    top_formats_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_iso,
                    window_start_iso,
                    now_iso,
                    window_days_normalized,
                    top_n_normalized,
                    decision_reason,
                    ranking_json,
                    top_formats_json,
                    _utc_now().isoformat(),
                ),
            )
            decision_id = int(decision_insert.lastrowid)

            for cta_format in all_formats:
                metric = metrics_by_format.get(cta_format, _default_format_metric_payload(cta_format))
                rank = rank_by_format.get(cta_format)
                ctr = float(metric.get("ctr", 0.0) or 0.0)
                signup_cr = float(metric.get("signup_cr", 0.0) or 0.0)
                total_clicks = int(metric.get("total_clicks", 0) or 0)
                unique_clicks = int(metric.get("unique_clicks", 0) or 0)
                signup_users = int(metric.get("signup_users", 0) or 0)
                previous_status = existing_status_map.get(cta_format, _CTA_FORMAT_STATUS_PAUSED)
                next_status = (
                    _CTA_FORMAT_STATUS_ACTIVE
                    if cta_format in top_formats_set
                    else _CTA_FORMAT_STATUS_PAUSED
                )
                change_reason = _build_cta_format_change_reason(
                    top_n=top_n_normalized,
                    rank=rank,
                    ctr=ctr,
                    signup_cr=signup_cr,
                    next_status=next_status,
                )

                if previous_status != next_status:
                    connection.execute(
                        """
                        INSERT INTO cta_format_status_log (
                            decision_id,
                            cta_format,
                            previous_status,
                            new_status,
                            rank,
                            ctr,
                            signup_cr,
                            total_clicks,
                            unique_clicks,
                            signup_users,
                            change_reason,
                            changed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            decision_id,
                            cta_format,
                            previous_status,
                            next_status,
                            rank,
                            ctr,
                            signup_cr,
                            total_clicks,
                            unique_clicks,
                            signup_users,
                            change_reason,
                            now_iso,
                        ),
                    )
                    status_changes.append(
                        {
                            "cta_format": cta_format,
                            "previous_status": previous_status,
                            "new_status": next_status,
                            "rank": rank,
                            "ctr": ctr,
                            "signup_cr": signup_cr,
                            "total_clicks": total_clicks,
                            "unique_clicks": unique_clicks,
                            "signup_users": signup_users,
                            "reason": change_reason,
                            "changed_at": now_iso,
                        }
                    )

                connection.execute(
                    """
                    INSERT INTO cta_format_status_current (
                        cta_format,
                        status,
                        rank,
                        ctr,
                        signup_cr,
                        total_clicks,
                        unique_clicks,
                        signup_users,
                        reason,
                        last_decision_id,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cta_format)
                    DO UPDATE SET
                        status = excluded.status,
                        rank = excluded.rank,
                        ctr = excluded.ctr,
                        signup_cr = excluded.signup_cr,
                        total_clicks = excluded.total_clicks,
                        unique_clicks = excluded.unique_clicks,
                        signup_users = excluded.signup_users,
                        reason = excluded.reason,
                        last_decision_id = excluded.last_decision_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        cta_format,
                        next_status,
                        rank,
                        ctr,
                        signup_cr,
                        total_clicks,
                        unique_clicks,
                        signup_users,
                        change_reason,
                        decision_id,
                        now_iso,
                    ),
                )

            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return {
        "id": decision_id,
        "decided_at": now_iso,
        "window_start_at": window_start_iso,
        "window_end_at": now_iso,
        "window_days": window_days_normalized,
        "top_n": top_n_normalized,
        "reason": decision_reason,
        "top_formats": top_formats,
        "ranking": ranking,
        "status_changes": status_changes,
        "changed_formats": len(status_changes),
        "total_formats": len(all_formats),
    }


def get_latest_cta_format_optimization_decision(settings: ServiceSettings) -> dict[str, Any] | None:
    ensure_cta_analytics_schema(settings)
    db_path = resolve_cta_analytics_db_path(settings)
    with _connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT
                id,
                decided_at,
                window_start_at,
                window_end_at,
                window_days,
                top_n,
                reason,
                ranking_json,
                top_formats_json
            FROM cta_format_optimization_runs
            ORDER BY decided_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        decision_id = int(row["id"])
        status_changes = _load_decision_status_changes(connection, decision_id=decision_id)
        return _materialize_format_decision(row, status_changes=status_changes)


def list_cta_format_optimization_decisions(
    settings: ServiceSettings,
    *,
    since: datetime | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    ensure_cta_analytics_schema(settings)
    normalized_limit = limit if limit > 0 else 20
    normalized_limit = min(normalized_limit, 100)
    db_path = resolve_cta_analytics_db_path(settings)
    with _connect(db_path) as connection:
        params: list[object] = []
        where_clause = ""
        if since is not None:
            since_iso = _parse_received_at(since).isoformat()
            where_clause = "WHERE decided_at >= ?"
            params.append(since_iso)

        rows = connection.execute(
            f"""
            SELECT
                id,
                decided_at,
                window_start_at,
                window_end_at,
                window_days,
                top_n,
                reason,
                ranking_json,
                top_formats_json
            FROM cta_format_optimization_runs
            {where_clause}
            ORDER BY decided_at DESC
            LIMIT ?
            """,
            [*params, normalized_limit],
        ).fetchall()

        decisions: list[dict[str, Any]] = []
        for row in rows:
            decision_id = int(row["id"])
            status_changes = _load_decision_status_changes(connection, decision_id=decision_id)
            decisions.append(_materialize_format_decision(row, status_changes=status_changes))
        return decisions


def list_cta_format_statuses(settings: ServiceSettings) -> list[dict[str, Any]]:
    ensure_cta_analytics_schema(settings)
    db_path = resolve_cta_analytics_db_path(settings)
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT
                cta_format,
                status,
                rank,
                ctr,
                signup_cr,
                total_clicks,
                unique_clicks,
                signup_users,
                reason,
                last_decision_id,
                updated_at
            FROM cta_format_status_current
            ORDER BY status DESC, rank ASC, cta_format ASC
            """
        ).fetchall()
        return [
            {
                "cta_format": str(row["cta_format"]),
                "status": _normalize_cta_format_status(row["status"]),
                "rank": int(row["rank"]) if row["rank"] is not None else None,
                "ctr": float(row["ctr"] or 0.0),
                "signup_cr": float(row["signup_cr"] or 0.0),
                "total_clicks": int(row["total_clicks"] or 0),
                "unique_clicks": int(row["unique_clicks"] or 0),
                "signup_users": int(row["signup_users"] or 0),
                "reason": str(row["reason"] or ""),
                "last_decision_id": int(row["last_decision_id"]) if row["last_decision_id"] is not None else None,
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in rows
        ]


def maybe_apply_fact_retention(settings: ServiceSettings) -> None:
    check_seconds = _normalize_positive_int(
        _env_int("AICI_CTA_RETENTION_CHECK_SECONDS", DEFAULT_RETENTION_CHECK_SECONDS),
        DEFAULT_RETENTION_CHECK_SECONDS,
    )
    db_path = resolve_cta_analytics_db_path(settings)
    now_ts = time.time()
    with _RETENTION_LOCK:
        previous = _RETENTION_LAST_RUN_TS.get(db_path, 0.0)
        if previous and now_ts - previous < check_seconds:
            return
        _RETENTION_LAST_RUN_TS[db_path] = now_ts
    _run_fact_retention(settings, db_path)


def _run_fact_retention(settings: ServiceSettings, db_path: Path) -> None:
    retention_days = _normalize_positive_int(
        _env_int("AICI_CTA_FACT_RETENTION_DAYS", DEFAULT_FACT_RETENTION_DAYS),
        DEFAULT_FACT_RETENTION_DAYS,
    )
    batch_size = _normalize_positive_int(
        _env_int("AICI_CTA_RETENTION_BATCH_SIZE", DEFAULT_RETENTION_BATCH_SIZE),
        DEFAULT_RETENTION_BATCH_SIZE,
    )
    archive_file_retention_days = _normalize_positive_int(
        _env_int("AICI_CTA_ARCHIVE_FILE_RETENTION_DAYS", DEFAULT_ARCHIVE_FILE_RETENTION_DAYS),
        DEFAULT_ARCHIVE_FILE_RETENTION_DAYS,
    )
    started_at = _utc_now()
    cutoff_at = started_at - timedelta(days=retention_days)
    cutoff_iso = cutoff_at.isoformat()
    cutoff_date = cutoff_at.date().isoformat()
    cutoff_hour = cutoff_at.replace(minute=0, second=0, microsecond=0).isoformat()

    archived_rows = 0
    deleted_rows = 0
    archive_path: Path | None = None
    status = "ok"
    error_message: str | None = None

    try:
        with _connect(db_path) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM cta_events_fact
                WHERE received_at < ?
                ORDER BY received_at
                LIMIT ?
                """,
                (cutoff_iso, batch_size),
            ).fetchall()
            if rows:
                archive_path = _write_archive_batch(settings, rows, started_at=started_at)
                archived_rows = len(rows)
                event_ids = [str(row["event_id"]) for row in rows]
                connection.execute("BEGIN;")
                try:
                    connection.executemany(
                        "DELETE FROM cta_events_fact WHERE event_id = ?",
                        [(event_id,) for event_id in event_ids],
                    )
                    deleted_rows = len(event_ids)
                    connection.execute(
                        "DELETE FROM cta_metrics_hourly_actor_unique WHERE event_hour < ?",
                        (cutoff_hour,),
                    )
                    connection.execute(
                        "DELETE FROM cta_metrics_daily_actor_unique WHERE event_date < ?",
                        (cutoff_date,),
                    )
                    connection.execute(
                        "DELETE FROM cta_event_metrics_hourly_actor_unique WHERE event_hour < ?",
                        (cutoff_hour,),
                    )
                    connection.execute(
                        "DELETE FROM cta_event_metrics_daily_actor_unique WHERE event_date < ?",
                        (cutoff_date,),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            _append_retention_log(
                connection,
                started_at=started_at,
                cutoff_at=cutoff_at,
                finished_at=_utc_now(),
                archived_rows=archived_rows,
                deleted_rows=deleted_rows,
                archive_path=archive_path,
                status=status,
                error_message=error_message,
            )
    except Exception as exc:
        status = "error"
        error_message = str(exc)
        with _connect(db_path) as connection:
            _append_retention_log(
                connection,
                started_at=started_at,
                cutoff_at=cutoff_at,
                finished_at=_utc_now(),
                archived_rows=archived_rows,
                deleted_rows=deleted_rows,
                archive_path=archive_path,
                status=status,
                error_message=error_message,
            )
    _prune_archive_files(settings, retention_days=archive_file_retention_days)


def record_cta_ingestion_quality(
    settings: ServiceSettings,
    *,
    event_id: str | None,
    received_at: str | datetime | None,
    status: str,
    reason: str | None = None,
) -> None:
    ensure_cta_analytics_schema(settings)
    received_at_dt = _parse_received_at(received_at)
    event_hour = received_at_dt.replace(minute=0, second=0, microsecond=0).isoformat()
    normalized_status = str(status or "").strip().lower()
    accepted_events = 1 if normalized_status == "accepted" else 0
    duplicate_events = 1 if normalized_status == "duplicate" else 0
    invalid_events = 1 if normalized_status == "invalid" else 0
    if accepted_events == 0 and duplicate_events == 0 and invalid_events == 0:
        invalid_events = 1
        normalized_status = "invalid"

    db_path = resolve_cta_analytics_db_path(settings)
    now_iso = _utc_now().isoformat()
    event_id_value = _normalize_text(event_id, max_length=80)
    reason_value = _normalize_text(reason, max_length=320)
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO cta_ingestion_quality_hourly (
                event_hour,
                total_events,
                accepted_events,
                duplicate_events,
                invalid_events,
                last_event_id,
                last_received_at,
                last_error_reason,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_hour)
            DO UPDATE SET
                total_events = cta_ingestion_quality_hourly.total_events + excluded.total_events,
                accepted_events = cta_ingestion_quality_hourly.accepted_events + excluded.accepted_events,
                duplicate_events = cta_ingestion_quality_hourly.duplicate_events + excluded.duplicate_events,
                invalid_events = cta_ingestion_quality_hourly.invalid_events + excluded.invalid_events,
                last_event_id = COALESCE(excluded.last_event_id, cta_ingestion_quality_hourly.last_event_id),
                last_received_at = COALESCE(excluded.last_received_at, cta_ingestion_quality_hourly.last_received_at),
                last_error_reason = CASE
                    WHEN excluded.last_error_reason IS NULL OR excluded.last_error_reason = ''
                    THEN cta_ingestion_quality_hourly.last_error_reason
                    ELSE excluded.last_error_reason
                END,
                updated_at = excluded.updated_at
            """,
            (
                event_hour,
                1,
                accepted_events,
                duplicate_events,
                invalid_events,
                event_id_value,
                received_at_dt.isoformat(),
                reason_value if normalized_status == "invalid" else None,
                now_iso,
            ),
        )


def _write_archive_batch(
    settings: ServiceSettings,
    rows: list[sqlite3.Row],
    *,
    started_at: datetime,
) -> Path:
    if not rows:
        raise ValueError("archive batch cannot be empty")
    archive_root = _archive_root(settings)
    bucket = started_at.strftime("%Y-%m")
    target_dir = archive_root / bucket
    target_dir.mkdir(parents=True, exist_ok=True)

    first_date = str(rows[0]["event_date"])
    last_date = str(rows[-1]["event_date"])
    filename = (
        f"cta_events_fact_{first_date}_to_{last_date}_{started_at.strftime('%Y%m%dT%H%M%SZ')}.jsonl.gz"
    )
    target_path = target_dir / filename
    with gzip.open(target_path, "wt", encoding="utf-8") as handle:
        for row in rows:
            payload = dict(row)
            metadata_raw = payload.get("metadata_json")
            if isinstance(metadata_raw, str):
                try:
                    payload["metadata_json"] = json.loads(metadata_raw)
                except json.JSONDecodeError:
                    payload["metadata_json"] = metadata_raw
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return target_path


def _append_retention_log(
    connection: sqlite3.Connection,
    *,
    started_at: datetime,
    cutoff_at: datetime,
    finished_at: datetime,
    archived_rows: int,
    deleted_rows: int,
    archive_path: Path | None,
    status: str,
    error_message: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO cta_fact_retention_log (
            started_at, finished_at, cutoff_at, archived_rows, deleted_rows, archive_path, status, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            started_at.isoformat(),
            finished_at.isoformat(),
            cutoff_at.isoformat(),
            int(archived_rows),
            int(deleted_rows),
            str(archive_path) if archive_path else None,
            status,
            error_message,
        ),
    )


def _prune_archive_files(settings: ServiceSettings, *, retention_days: int) -> None:
    archive_root = _archive_root(settings)
    cutoff_ts = (_utc_now() - timedelta(days=retention_days)).timestamp()
    for candidate in archive_root.rglob("*.jsonl.gz"):
        if not candidate.is_file():
            continue
        try:
            if candidate.stat().st_mtime < cutoff_ts:
                candidate.unlink(missing_ok=True)
        except OSError:
            continue


__all__ = [
    "ANALYTICS_DIR_NAME",
    "CTA_ANALYTICS_DB_FILE",
    "DEFAULT_FORMAT_OPTIMIZATION_TOP_N",
    "DEFAULT_FORMAT_OPTIMIZATION_WINDOW_DAYS",
    "ensure_cta_analytics_schema",
    "get_latest_cta_format_optimization_decision",
    "list_cta_format_optimization_decisions",
    "list_cta_format_statuses",
    "maybe_apply_fact_retention",
    "persist_cta_analytics_record",
    "record_cta_ingestion_quality",
    "resolve_cta_analytics_db_path",
    "resolve_cta_analytics_db_path_from_runs_root",
    "resolve_cta_analytics_root",
    "resolve_cta_analytics_root_from_runs_root",
    "run_weekly_cta_format_optimization",
]
