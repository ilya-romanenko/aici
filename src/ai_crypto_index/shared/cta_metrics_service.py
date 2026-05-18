from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_crypto_index.accounts import models as account_models
from ai_crypto_index.shared import cta_analytics_store
from ai_crypto_index.shared.settings import ServiceSettings

DEFAULT_CONVERSION_LOOKBACK_DAYS = 7
_SQL_BATCH_SIZE = 500
_VALID_INTERVALS = {"day": "event_date", "hour": "event_hour"}
_ATTRIBUTION_MODEL = "last_click"
_ATTRIBUTION_IDENTITY_PRIORITY = ("account_id", "session_id", "fingerprint")
_PAID_EVENT_TYPES = (
    "checkout.session.completed",
    "invoice.payment_succeeded",
    "crypto.payment.confirmed",
    "crypto.activation.notified",
)
_PAID_SUBSCRIPTION_STATUSES = (
    account_models.BillingSubscriptionStatus.TRIALING,
    account_models.BillingSubscriptionStatus.ACTIVE,
    account_models.BillingSubscriptionStatus.PAST_DUE,
)
_CTA_CLICK_EVENT_TYPE = "cta_click"


@dataclass(frozen=True, slots=True)
class CtaMetricsQuery:
    start_at: datetime
    end_at: datetime
    cta_ids: tuple[str, ...] = ()
    cta_types: tuple[str, ...] = ()
    cta_formats: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    utm_sources: tuple[str, ...] = ()
    traffic_sources: tuple[str, ...] = ()
    page_paths: tuple[str, ...] = ()
    auth_states: tuple[str, ...] = ()
    referers: tuple[str, ...] = ()
    utm_values: tuple[str, ...] = ()
    lookback_days: int = DEFAULT_CONVERSION_LOOKBACK_DAYS

    def normalized(self) -> CtaMetricsQuery:
        start_at = _ensure_utc(self.start_at)
        end_at = _ensure_utc(self.end_at)
        if end_at <= start_at:
            raise ValueError("end_at must be greater than start_at.")
        return CtaMetricsQuery(
            start_at=start_at,
            end_at=end_at,
            cta_ids=_normalize_filters(self.cta_ids),
            cta_types=_normalize_filters(self.cta_types),
            cta_formats=_normalize_filters(self.cta_formats),
            locations=_normalize_filters(self.locations),
            utm_sources=_normalize_filters(self.utm_sources),
            traffic_sources=_normalize_filters(self.traffic_sources),
            page_paths=_normalize_filters(self.page_paths),
            auth_states=_normalize_auth_states(self.auth_states),
            referers=_normalize_filters(self.referers),
            utm_values=_normalize_filters(self.utm_values),
            lookback_days=DEFAULT_CONVERSION_LOOKBACK_DAYS,
        )


@dataclass(frozen=True, slots=True)
class _ClickCandidate:
    key: Any
    clicked_at: datetime
    account_id: str | None = None
    session_id: str | None = None


class CtaMetricsService:
    def __init__(self, settings: ServiceSettings) -> None:
        self.settings = settings

    async def build_dashboard(
        self,
        session: AsyncSession,
        query: CtaMetricsQuery,
        *,
        interval: str = "day",
        breakdown_limit: int | None = 100,
        top_limit: int | None = 100,
    ) -> dict[str, Any]:
        normalized_query = query.normalized()
        bucket_column = _VALID_INTERVALS.get(interval.strip().lower())
        if bucket_column is None:
            raise ValueError("interval must be either 'day' or 'hour'.")
        breakdown_cap = breakdown_limit if breakdown_limit is not None and breakdown_limit > 0 else None
        top_cap = top_limit if top_limit is not None and top_limit > 0 else None

        cta_analytics_store.ensure_cta_analytics_schema(self.settings)
        with _connect_analytics_db(self.settings) as connection:
            kpi_counts = _load_kpi_counts(connection, normalized_query)
            timeseries_counts = _load_timeseries_counts(connection, normalized_query, bucket_column=bucket_column)
            breakdown_counts = _load_breakdown_counts(connection, normalized_query, limit=breakdown_cap)
            top_cta_counts = _load_top_cta_counts(connection, normalized_query, limit=top_cap)
            observability = _load_observability_snapshot(connection, normalized_query)

            overall_click_candidates = _load_click_candidates(
                connection,
                normalized_query,
                key_columns=(),
            )
            timeseries_click_candidates = _load_click_candidates(
                connection,
                normalized_query,
                key_columns=(bucket_column,),
            )
            breakdown_click_candidates = _load_click_candidates(
                connection,
                normalized_query,
                key_columns=("cta_id", "cta_format", "location_norm", "page_path", "utm_source"),
            )
            top_click_candidates = _load_click_candidates(
                connection,
                normalized_query,
                key_columns=("cta_id",),
            )
            session_bridge = _load_signup_session_bridge(
                connection,
                normalized_query,
                lookback_window=timedelta(days=normalized_query.lookback_days),
            )

        account_ids = _collect_candidate_account_ids(
            overall_click_candidates,
            timeseries_click_candidates,
            breakdown_click_candidates,
            top_click_candidates,
            session_bridge,
        )
        signup_timestamps = await _load_signup_timestamps(session, account_ids)
        confirmed_timestamps = await _load_confirmed_timestamps(session, account_ids)
        paid_timestamps = await _load_paid_timestamps(session, account_ids)
        lookback_window = timedelta(days=normalized_query.lookback_days)
        overall_click_map, overall_accounts = _attribute_click_candidates(
            overall_click_candidates,
            session_bridge,
            signup_timestamps,
            lookback_window=lookback_window,
        )
        timeseries_click_map, timeseries_accounts = _attribute_click_candidates(
            timeseries_click_candidates,
            session_bridge,
            signup_timestamps,
            lookback_window=lookback_window,
        )
        breakdown_click_map, breakdown_accounts = _attribute_click_candidates(
            breakdown_click_candidates,
            session_bridge,
            signup_timestamps,
            lookback_window=lookback_window,
        )
        top_click_map, top_accounts = _attribute_click_candidates(
            top_click_candidates,
            session_bridge,
            signup_timestamps,
            lookback_window=lookback_window,
        )

        overall_funnel = _build_funnel_metrics(
            overall_click_map.get(None, {}),
            signup_timestamps,
            confirmed_timestamps,
            paid_timestamps,
            lookback_window=lookback_window,
        )

        unique_clicks = int(kpi_counts["unique_clicks"] or 0)
        kpi = {
            "period": {
                "start_at": normalized_query.start_at.isoformat(),
                "end_at": normalized_query.end_at.isoformat(),
                "lookback_days": normalized_query.lookback_days,
            },
            "total_clicks": int(kpi_counts["total_clicks"] or 0),
            "unique_clicks": unique_clicks,
            "unique_users": int(kpi_counts["unique_users"] or 0),
            "unique_sessions": int(kpi_counts["unique_sessions"] or 0),
            "unique_anonymous": int(kpi_counts["unique_anonymous"] or 0),
            "conversion": overall_funnel,
            "rates": _build_rate_metrics(
                total_clicks=int(kpi_counts["total_clicks"] or 0),
                unique_clicks=unique_clicks,
                conversion=overall_funnel,
            ),
            "attribution_coverage": _safe_ratio(overall_funnel["click_users"], unique_clicks),
            "attribution": _build_attribution_payload(lookback_days=normalized_query.lookback_days),
            "observability": observability["observability"],
            "service_state": observability["service_state"],
        }

        timeseries: list[dict[str, Any]] = []
        for row in timeseries_counts:
            bucket = row["bucket"]
            funnel = _build_funnel_metrics(
                timeseries_click_map.get(bucket, {}),
                signup_timestamps,
                confirmed_timestamps,
                paid_timestamps,
                lookback_window=lookback_window,
            )
            unique_bucket_clicks = int(row["unique_clicks"] or 0)
            timeseries.append(
                {
                    "bucket": bucket,
                    "total_clicks": int(row["total_clicks"] or 0),
                    "unique_clicks": unique_bucket_clicks,
                    "unique_users": int(row["unique_users"] or 0),
                    "unique_sessions": int(row["unique_sessions"] or 0),
                    "conversion": funnel,
                    "rates": _build_rate_metrics(
                        total_clicks=int(row["total_clicks"] or 0),
                        unique_clicks=unique_bucket_clicks,
                        conversion=funnel,
                    ),
                    "attribution_coverage": _safe_ratio(funnel["click_users"], unique_bucket_clicks),
                    "attribution": _build_attribution_payload(lookback_days=normalized_query.lookback_days),
                }
            )

        breakdown: list[dict[str, Any]] = []
        for row in breakdown_counts:
            key = (
                row["cta_id"],
                row["cta_format"],
                row["location"],
                row["page_path"],
                row["utm_source"],
            )
            funnel = _build_funnel_metrics(
                breakdown_click_map.get(key, {}),
                signup_timestamps,
                confirmed_timestamps,
                paid_timestamps,
                lookback_window=lookback_window,
            )
            unique_breakdown_clicks = int(row["unique_clicks"] or 0)
            breakdown.append(
                {
                    "cta_id": row["cta_id"],
                    "cta_format": row["cta_format"],
                    "location": row["location"],
                    "page_path": row["page_path"],
                    "utm_source": row["utm_source"],
                    "total_clicks": int(row["total_clicks"] or 0),
                    "unique_clicks": unique_breakdown_clicks,
                    "unique_users": int(row["unique_users"] or 0),
                    "unique_sessions": int(row["unique_sessions"] or 0),
                    "conversion": funnel,
                    "rates": _build_rate_metrics(
                        total_clicks=int(row["total_clicks"] or 0),
                        unique_clicks=unique_breakdown_clicks,
                        conversion=funnel,
                    ),
                    "attribution_coverage": _safe_ratio(funnel["click_users"], unique_breakdown_clicks),
                    "attribution": _build_attribution_payload(lookback_days=normalized_query.lookback_days),
                }
            )

        top_cta: list[dict[str, Any]] = []
        for row in top_cta_counts:
            key = row["cta_id"]
            funnel = _build_funnel_metrics(
                top_click_map.get(key, {}),
                signup_timestamps,
                confirmed_timestamps,
                paid_timestamps,
                lookback_window=lookback_window,
            )
            unique_top_clicks = int(row["unique_clicks"] or 0)
            top_cta.append(
                {
                    "cta_id": row["cta_id"],
                    "total_clicks": int(row["total_clicks"] or 0),
                    "unique_clicks": unique_top_clicks,
                    "unique_users": int(row["unique_users"] or 0),
                    "unique_sessions": int(row["unique_sessions"] or 0),
                    "conversion": funnel,
                    "rates": _build_rate_metrics(
                        total_clicks=int(row["total_clicks"] or 0),
                        unique_clicks=unique_top_clicks,
                        conversion=funnel,
                    ),
                    "attribution_coverage": _safe_ratio(funnel["click_users"], unique_top_clicks),
                    "attribution": _build_attribution_payload(lookback_days=normalized_query.lookback_days),
                }
            )

        return {
            "kpi": kpi,
            "timeseries": timeseries,
            "breakdown": breakdown,
            "top_cta": top_cta,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def build_kpi_cards(self, session: AsyncSession, query: CtaMetricsQuery) -> dict[str, Any]:
        payload = await self.build_dashboard(session, query, interval="day", breakdown_limit=1)
        return payload["kpi"]

    async def build_timeseries(
        self,
        session: AsyncSession,
        query: CtaMetricsQuery,
        *,
        interval: str = "day",
    ) -> list[dict[str, Any]]:
        payload = await self.build_dashboard(session, query, interval=interval, breakdown_limit=1)
        return payload["timeseries"]

    async def build_breakdown(
        self,
        session: AsyncSession,
        query: CtaMetricsQuery,
        *,
        limit: int | None = 100,
    ) -> list[dict[str, Any]]:
        payload = await self.build_dashboard(session, query, interval="day", breakdown_limit=limit)
        return payload["breakdown"]

    async def build_top_cta(
        self,
        session: AsyncSession,
        query: CtaMetricsQuery,
        *,
        limit: int | None = 100,
    ) -> list[dict[str, Any]]:
        payload = await self.build_dashboard(
            session,
            query,
            interval="day",
            breakdown_limit=1,
            top_limit=limit,
        )
        return payload["top_cta"]

    async def build_funnel(self, session: AsyncSession, query: CtaMetricsQuery) -> dict[str, Any]:
        payload = await self.build_dashboard(
            session,
            query,
            interval="day",
            breakdown_limit=1,
            top_limit=1,
        )
        kpi = payload["kpi"]
        return {
            "period": kpi["period"],
            "total_clicks": kpi["total_clicks"],
            "unique_clicks": kpi["unique_clicks"],
            "conversion": kpi["conversion"],
            "rates": kpi["rates"],
            "attribution_coverage": kpi["attribution_coverage"],
            "attribution": kpi["attribution"],
            "generated_at": payload["generated_at"],
        }


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_filters(values: Iterable[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = str(value or "").strip().lower()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return tuple(normalized)


def _normalize_auth_states(values: Iterable[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    allowed = {"anonymous", "authenticated"}
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = str(value or "").strip().lower()
        if not candidate or candidate not in allowed or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return tuple(normalized)


def _connect_analytics_db(settings: ServiceSettings) -> sqlite3.Connection:
    db_path = cta_analytics_store.resolve_cta_analytics_db_path(settings)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    return connection


def _build_fact_filter_clause(query: CtaMetricsQuery) -> tuple[str, list[object]]:
    clauses = ["received_at >= ?", "received_at < ?", "event_type = ?"]
    params: list[object] = [query.start_at.isoformat(), query.end_at.isoformat(), _CTA_CLICK_EVENT_TYPE]
    if query.cta_ids:
        placeholders = ", ".join("?" for _ in query.cta_ids)
        clauses.append(f"cta_id IN ({placeholders})")
        params.extend(query.cta_ids)
    if query.cta_types:
        cta_type_clauses = ["LOWER(COALESCE(cta_id, '')) LIKE ?" for _ in query.cta_types]
        clauses.append(f"({' OR '.join(cta_type_clauses)})")
        params.extend(f"%{value}%" for value in query.cta_types)
    if query.cta_formats:
        placeholders = ", ".join("?" for _ in query.cta_formats)
        clauses.append(f"LOWER(COALESCE(cta_format, 'unknown')) IN ({placeholders})")
        params.extend(query.cta_formats)
    if query.locations:
        placeholders = ", ".join("?" for _ in query.locations)
        clauses.append(f"location_norm IN ({placeholders})")
        params.extend(query.locations)
    if query.utm_sources:
        placeholders = ", ".join("?" for _ in query.utm_sources)
        clauses.append(f"LOWER(COALESCE(utm_source, '')) IN ({placeholders})")
        params.extend(query.utm_sources)
    if query.traffic_sources:
        traffic_clauses: list[str] = []
        for source in query.traffic_sources:
            if source == "direct":
                traffic_clauses.append(
                    "(LOWER(TRIM(COALESCE(utm_source, ''))) = '' AND LOWER(TRIM(COALESCE(referer, ''))) = '')"
                )
                continue
            if source == "referral":
                traffic_clauses.append(
                    "(LOWER(TRIM(COALESCE(utm_source, ''))) = '' AND LOWER(TRIM(COALESCE(referer, ''))) <> '')"
                )
                continue
            traffic_clauses.append("LOWER(COALESCE(utm_source, '')) = ?")
            params.append(source)
        clauses.append(f"({' OR '.join(traffic_clauses)})")
    if query.page_paths:
        placeholders = ", ".join("?" for _ in query.page_paths)
        clauses.append(f"LOWER(COALESCE(page_path, '')) IN ({placeholders})")
        params.extend(query.page_paths)
    if query.auth_states:
        placeholders = ", ".join("?" for _ in query.auth_states)
        clauses.append(f"auth_state IN ({placeholders})")
        params.extend(query.auth_states)
    if query.referers:
        referer_clauses = ["LOWER(COALESCE(referer, '')) LIKE ?" for _ in query.referers]
        clauses.append(f"({' OR '.join(referer_clauses)})")
        params.extend(f"%{value}%" for value in query.referers)
    if query.utm_values:
        utm_clauses: list[str] = []
        for _ in query.utm_values:
            utm_clauses.append(
                "("
                "LOWER(COALESCE(utm_source, '')) LIKE ? OR "
                "LOWER(COALESCE(utm_medium, '')) LIKE ? OR "
                "LOWER(COALESCE(utm_campaign, '')) LIKE ? OR "
                "LOWER(COALESCE(utm_content, '')) LIKE ? OR "
                "LOWER(COALESCE(utm_term, '')) LIKE ?"
                ")"
            )
        clauses.append(f"({' OR '.join(utm_clauses)})")
        for value in query.utm_values:
            token = f"%{value}%"
            params.extend((token, token, token, token, token))
    return " AND ".join(clauses), params


def _load_kpi_counts(connection: sqlite3.Connection, query: CtaMetricsQuery) -> sqlite3.Row:
    where_clause, params = _build_fact_filter_clause(query)
    return connection.execute(
        f"""
        SELECT
            COUNT(*) AS total_clicks,
            COUNT(DISTINCT unique_actor_id) AS unique_clicks,
            COUNT(DISTINCT CASE WHEN unique_actor_id LIKE 'account:%' THEN unique_actor_id END) AS unique_users,
            COUNT(DISTINCT CASE WHEN unique_actor_id LIKE 'session:%' THEN unique_actor_id END) AS unique_sessions,
            COUNT(
                DISTINCT CASE
                    WHEN unique_actor_id NOT LIKE 'account:%' AND unique_actor_id NOT LIKE 'session:%'
                    THEN unique_actor_id
                END
            ) AS unique_anonymous
        FROM cta_events_fact
        WHERE {where_clause}
        """,
        params,
    ).fetchone() or {
        "total_clicks": 0,
        "unique_clicks": 0,
        "unique_users": 0,
        "unique_sessions": 0,
        "unique_anonymous": 0,
    }


def _load_timeseries_counts(
    connection: sqlite3.Connection,
    query: CtaMetricsQuery,
    *,
    bucket_column: str,
) -> list[dict[str, Any]]:
    where_clause, params = _build_fact_filter_clause(query)
    rows = connection.execute(
        f"""
        SELECT
            {bucket_column} AS bucket,
            COUNT(*) AS total_clicks,
            COUNT(DISTINCT unique_actor_id) AS unique_clicks,
            COUNT(DISTINCT CASE WHEN unique_actor_id LIKE 'account:%' THEN unique_actor_id END) AS unique_users,
            COUNT(DISTINCT CASE WHEN unique_actor_id LIKE 'session:%' THEN unique_actor_id END) AS unique_sessions
        FROM cta_events_fact
        WHERE {where_clause}
        GROUP BY {bucket_column}
        ORDER BY {bucket_column}
        """,
        params,
    ).fetchall()

    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append(
            {
                "bucket": str(row["bucket"]),
                "total_clicks": int(row["total_clicks"] or 0),
                "unique_clicks": int(row["unique_clicks"] or 0),
                "unique_users": int(row["unique_users"] or 0),
                "unique_sessions": int(row["unique_sessions"] or 0),
            }
        )
    return payload


def _load_breakdown_counts(
    connection: sqlite3.Connection,
    query: CtaMetricsQuery,
    *,
    limit: int | None,
) -> list[dict[str, Any]]:
    where_clause, params = _build_fact_filter_clause(query)
    sql = f"""
        SELECT
            cta_id,
            COALESCE(cta_format, 'unknown') AS cta_format,
            location_norm,
            COALESCE(page_path, '/') AS page_path,
            COALESCE(utm_source, '') AS utm_source,
            COUNT(*) AS total_clicks,
            COUNT(DISTINCT unique_actor_id) AS unique_clicks,
            COUNT(DISTINCT CASE WHEN unique_actor_id LIKE 'account:%' THEN unique_actor_id END) AS unique_users,
            COUNT(DISTINCT CASE WHEN unique_actor_id LIKE 'session:%' THEN unique_actor_id END) AS unique_sessions
        FROM cta_events_fact
        WHERE {where_clause}
        GROUP BY cta_id, COALESCE(cta_format, 'unknown'), location_norm, COALESCE(page_path, '/'), COALESCE(utm_source, '')
        ORDER BY total_clicks DESC, cta_id ASC, cta_format ASC, location_norm ASC, page_path ASC, utm_source ASC
        """
    query_params = list(params)
    if limit is not None and limit > 0:
        sql += "\nLIMIT ?"
        query_params.append(limit)
    rows = connection.execute(sql, query_params).fetchall()

    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append(
            {
                "cta_id": str(row["cta_id"]),
                "cta_format": str(row["cta_format"]),
                "location": str(row["location_norm"]),
                "page_path": str(row["page_path"]),
                "utm_source": str(row["utm_source"]),
                "total_clicks": int(row["total_clicks"] or 0),
                "unique_clicks": int(row["unique_clicks"] or 0),
                "unique_users": int(row["unique_users"] or 0),
                "unique_sessions": int(row["unique_sessions"] or 0),
            }
        )
    return payload


def _load_top_cta_counts(
    connection: sqlite3.Connection,
    query: CtaMetricsQuery,
    *,
    limit: int | None,
) -> list[dict[str, Any]]:
    where_clause, params = _build_fact_filter_clause(query)
    sql = (
        f"""
        SELECT
            cta_id,
            COUNT(*) AS total_clicks,
            COUNT(DISTINCT unique_actor_id) AS unique_clicks,
            COUNT(DISTINCT CASE WHEN unique_actor_id LIKE 'account:%' THEN unique_actor_id END) AS unique_users,
            COUNT(DISTINCT CASE WHEN unique_actor_id LIKE 'session:%' THEN unique_actor_id END) AS unique_sessions
        FROM cta_events_fact
        WHERE {where_clause}
        GROUP BY cta_id
        ORDER BY total_clicks DESC, cta_id ASC
        """
    )
    query_params = list(params)
    if limit is not None and limit > 0:
        sql += "\nLIMIT ?"
        query_params.append(limit)
    rows = connection.execute(sql, query_params).fetchall()

    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append(
            {
                "cta_id": str(row["cta_id"]),
                "total_clicks": int(row["total_clicks"] or 0),
                "unique_clicks": int(row["unique_clicks"] or 0),
                "unique_users": int(row["unique_users"] or 0),
                "unique_sessions": int(row["unique_sessions"] or 0),
            }
        )
    return payload


def _load_click_candidates(
    connection: sqlite3.Connection,
    query: CtaMetricsQuery,
    *,
    key_columns: tuple[str, ...],
) -> list[_ClickCandidate]:
    where_clause, params = _build_fact_filter_clause(query)
    key_select = ", ".join(key_columns)
    select_prefix = f"{key_select}, " if key_columns else ""
    group_prefix = f"{key_select}, " if key_columns else ""
    sql = f"""
        SELECT {select_prefix}unique_actor_id, metadata_json, MAX(received_at) AS last_click_at
        FROM cta_events_fact
        WHERE {where_clause}
        GROUP BY {group_prefix}unique_actor_id
    """
    rows = connection.execute(sql, params).fetchall()

    candidates: list[_ClickCandidate] = []
    for row in rows:
        clicked_at = _parse_datetime(row["last_click_at"])
        if clicked_at is None:
            continue
        actor_id = str(row["unique_actor_id"] or "")
        metadata = _parse_metadata_json(row["metadata_json"])
        account_id = _extract_account_id(actor_id)
        session_id = _extract_session_id(actor_id, metadata)
        if account_id is None and session_id is None:
            continue
        candidates.append(
            _ClickCandidate(
                key=_resolve_key(row, key_columns),
                clicked_at=clicked_at,
                account_id=account_id,
                session_id=session_id,
            )
        )
    return candidates


def _load_signup_session_bridge(
    connection: sqlite3.Connection,
    query: CtaMetricsQuery,
    *,
    lookback_window: timedelta,
) -> dict[str, set[str]]:
    end_at = query.end_at + lookback_window
    rows = connection.execute(
        """
        SELECT unique_actor_id, metadata_json
        FROM cta_events_fact
        WHERE event_type = ? AND received_at >= ? AND received_at < ? AND unique_actor_id LIKE 'account:%'
        """,
        ("signup_started", query.start_at.isoformat(), end_at.isoformat()),
    ).fetchall()
    bridge: dict[str, set[str]] = {}
    for row in rows:
        account_id = _extract_account_id(str(row["unique_actor_id"] or ""))
        if account_id is None:
            continue
        metadata = _parse_metadata_json(row["metadata_json"])
        session_id = _extract_session_id("", metadata)
        if not session_id:
            continue
        bridge.setdefault(session_id, set()).add(account_id)
    return bridge


def _collect_candidate_account_ids(
    *candidate_groups: object,
) -> set[str]:
    account_ids: set[str] = set()
    for group in candidate_groups:
        if isinstance(group, dict):
            for mapped_accounts in group.values():
                account_ids.update(str(item) for item in mapped_accounts)
            continue
        if not isinstance(group, list):
            continue
        for item in group:
            if isinstance(item, _ClickCandidate) and item.account_id is not None:
                account_ids.add(item.account_id)
    return account_ids


def _attribute_click_candidates(
    candidates: list[_ClickCandidate],
    session_bridge: dict[str, set[str]],
    signup_timestamps: dict[str, datetime],
    *,
    lookback_window: timedelta,
) -> tuple[dict[Any, dict[str, datetime]], set[str]]:
    click_map: dict[Any, dict[str, datetime]] = {}
    account_ids: set[str] = set()

    for candidate in candidates:
        account_id = candidate.account_id
        if account_id is None and candidate.session_id:
            account_id = _resolve_session_account_id(
                candidate.session_id,
                clicked_at=candidate.clicked_at,
                session_bridge=session_bridge,
                signup_timestamps=signup_timestamps,
                lookback_window=lookback_window,
            )
        if account_id is None:
            continue
        bucket = click_map.setdefault(candidate.key, {})
        existing = bucket.get(account_id)
        if existing is None or candidate.clicked_at > existing:
            bucket[account_id] = candidate.clicked_at
        account_ids.add(account_id)

    return click_map, account_ids


def _resolve_session_account_id(
    session_id: str,
    *,
    clicked_at: datetime,
    session_bridge: dict[str, set[str]],
    signup_timestamps: dict[str, datetime],
    lookback_window: timedelta,
) -> str | None:
    candidate_account_ids = session_bridge.get(session_id, set())
    best_account_id: str | None = None
    best_signup_at: datetime | None = None
    for account_id in candidate_account_ids:
        signup_at = signup_timestamps.get(account_id)
        if not _is_within_window(clicked_at, signup_at, lookback_window):
            continue
        if best_signup_at is None or (signup_at is not None and signup_at < best_signup_at):
            best_account_id = account_id
            best_signup_at = signup_at
    return best_account_id


def _resolve_key(row: sqlite3.Row, key_columns: tuple[str, ...]) -> Any:
    if not key_columns:
        return None
    if len(key_columns) == 1:
        return _normalize_key_value(key_columns[0], row[key_columns[0]])
    return tuple(_normalize_key_value(column, row[column]) for column in key_columns)


def _normalize_key_value(column: str, value: object | None) -> str:
    if value is None:
        if column == "page_path":
            return "/"
        if column == "utm_source":
            return ""
        if column == "cta_format":
            return "unknown"
        return "unknown"
    return str(value)


def _extract_account_id(actor_id: str) -> str | None:
    if not actor_id.startswith("account:"):
        return None
    candidate = actor_id.split(":", maxsplit=1)[1].strip()
    if not candidate:
        return None
    try:
        return str(uuid.UUID(candidate))
    except ValueError:
        return None


def _extract_session_id(actor_id: str, metadata: dict[str, Any]) -> str | None:
    if actor_id.startswith("session:"):
        candidate = actor_id.split(":", maxsplit=1)[1].strip()
        return candidate or None
    metadata_value = metadata.get("session_id")
    if not isinstance(metadata_value, str):
        return None
    candidate = metadata_value.strip()
    return candidate or None


def _parse_metadata_json(value: object | None) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _parse_datetime(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, str):
        try:
            return _ensure_utc(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def _iter_batches(values: list[uuid.UUID], size: int = _SQL_BATCH_SIZE) -> Iterable[list[uuid.UUID]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


async def _load_signup_timestamps(
    session: AsyncSession,
    account_ids: set[str],
) -> dict[str, datetime]:
    if not account_ids:
        return {}
    account_uuids = sorted({uuid.UUID(raw_id) for raw_id in account_ids})
    payload: dict[str, datetime] = {}

    for batch in _iter_batches(account_uuids):
        stmt = select(
            account_models.Account.id,
            account_models.Account.created_at,
        ).where(account_models.Account.id.in_(batch))
        rows = await session.execute(stmt)
        for account_id, created_at in rows:
            created_ts = _parse_datetime(created_at)
            if created_ts is None:
                continue
            _set_min_timestamp(payload, str(account_id), created_ts)
    return payload


async def _load_confirmed_timestamps(
    session: AsyncSession,
    account_ids: set[str],
) -> dict[str, datetime]:
    if not account_ids:
        return {}
    account_uuids = sorted({uuid.UUID(raw_id) for raw_id in account_ids})
    payload: dict[str, datetime] = {}

    for batch in _iter_batches(account_uuids):
        stmt = select(
            account_models.Account.id,
            account_models.Account.email_verified_at,
        ).where(account_models.Account.id.in_(batch))
        rows = await session.execute(stmt)
        for account_id, confirmed_at in rows:
            confirmed_ts = _parse_datetime(confirmed_at)
            if confirmed_ts is None:
                continue
            _set_min_timestamp(payload, str(account_id), confirmed_ts)
    return payload


async def _load_paid_timestamps(
    session: AsyncSession,
    account_ids: set[str],
) -> dict[str, datetime]:
    if not account_ids:
        return {}
    account_uuids = sorted({uuid.UUID(raw_id) for raw_id in account_ids})
    payload: dict[str, datetime] = {}

    for batch in _iter_batches(account_uuids):
        billing_event_stmt = (
            select(
                account_models.BillingEvent.account_id,
                func.min(
                    func.coalesce(
                        account_models.BillingEvent.processed_at,
                        account_models.BillingEvent.created_at,
                    )
                ).label("paid_at"),
            )
            .where(
                account_models.BillingEvent.account_id.in_(batch),
                account_models.BillingEvent.event_type.in_(_PAID_EVENT_TYPES),
            )
            .group_by(account_models.BillingEvent.account_id)
        )
        billing_event_rows = await session.execute(billing_event_stmt)
        for account_id, paid_at in billing_event_rows:
            paid_ts = _parse_datetime(paid_at)
            if account_id is None or paid_ts is None:
                continue
            _set_min_timestamp(payload, str(account_id), paid_ts)

        confirmed_crypto_stmt = (
            select(
                account_models.BillingCryptoPayment.account_id,
                func.min(
                    func.coalesce(
                        account_models.BillingCryptoPayment.updated_at,
                        account_models.BillingCryptoPayment.created_at,
                    )
                ).label("paid_at"),
            )
            .where(
                account_models.BillingCryptoPayment.account_id.in_(batch),
                account_models.BillingCryptoPayment.status == account_models.BillingCryptoPaymentStatus.CONFIRMED,
            )
            .group_by(account_models.BillingCryptoPayment.account_id)
        )
        confirmed_crypto_rows = await session.execute(confirmed_crypto_stmt)
        for account_id, paid_at in confirmed_crypto_rows:
            paid_ts = _parse_datetime(paid_at)
            if account_id is None or paid_ts is None:
                continue
            _set_min_timestamp(payload, str(account_id), paid_ts)

        subscription_stmt = (
            select(
                account_models.BillingSubscription.account_id,
                func.min(
                    func.coalesce(
                        account_models.BillingSubscription.current_period_start,
                        account_models.BillingSubscription.created_at,
                    )
                ).label("paid_at"),
            )
            .where(
                account_models.BillingSubscription.account_id.in_(batch),
                account_models.BillingSubscription.plan_code != "free",
                account_models.BillingSubscription.status.in_(_PAID_SUBSCRIPTION_STATUSES),
            )
            .group_by(account_models.BillingSubscription.account_id)
        )
        subscription_rows = await session.execute(subscription_stmt)
        for account_id, paid_at in subscription_rows:
            paid_ts = _parse_datetime(paid_at)
            if account_id is None or paid_ts is None:
                continue
            _set_min_timestamp(payload, str(account_id), paid_ts)

    return payload


def _set_min_timestamp(payload: dict[str, datetime], key: str, value: datetime) -> None:
    existing = payload.get(key)
    if existing is None or value < existing:
        payload[key] = value


def _build_funnel_metrics(
    click_accounts: dict[str, datetime],
    signup_timestamps: dict[str, datetime],
    confirmed_timestamps: dict[str, datetime],
    paid_timestamps: dict[str, datetime],
    *,
    lookback_window: timedelta,
) -> dict[str, Any]:
    click_users = len(click_accounts)
    signup_users = 0
    confirmed_users = 0
    paid_users = 0

    for account_id, click_at in click_accounts.items():
        signup_at = signup_timestamps.get(account_id)
        if not _is_within_window(click_at, signup_at, lookback_window):
            continue
        signup_users += 1

        confirmed_at = confirmed_timestamps.get(account_id)
        if not _is_within_window(click_at, confirmed_at, lookback_window):
            continue
        if signup_at is not None and confirmed_at is not None and confirmed_at < signup_at:
            continue
        confirmed_users += 1

        paid_at = paid_timestamps.get(account_id)
        if not _is_within_window(click_at, paid_at, lookback_window):
            continue
        if confirmed_at is not None and paid_at is not None and paid_at < confirmed_at:
            continue
        paid_users += 1

    return {
        "click_users": click_users,
        "signup_users": signup_users,
        "confirmed_users": confirmed_users,
        "paid_users": paid_users,
        "click_to_signup": _safe_ratio(signup_users, click_users),
        "click_to_confirmed": _safe_ratio(confirmed_users, click_users),
        "signup_to_confirmed": _safe_ratio(confirmed_users, signup_users),
        "confirmed_to_paid": _safe_ratio(paid_users, confirmed_users),
        "signup_to_paid": _safe_ratio(paid_users, signup_users),
        "click_to_paid": _safe_ratio(paid_users, click_users),
    }


def _is_within_window(
    anchor: datetime,
    candidate: datetime | None,
    window: timedelta,
) -> bool:
    if candidate is None:
        return False
    return anchor <= candidate <= anchor + window


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _build_rate_metrics(
    *,
    total_clicks: int,
    unique_clicks: int,
    conversion: dict[str, Any],
) -> dict[str, float]:
    return {
        "ctr": _safe_ratio(unique_clicks, total_clicks),
        "signup_cr": float(conversion.get("click_to_signup", 0.0) or 0.0),
        "confirm_cr": float(conversion.get("signup_to_confirmed", 0.0) or 0.0),
        "paid_cr": float(conversion.get("confirmed_to_paid", 0.0) or 0.0),
    }


def _build_attribution_payload(*, lookback_days: int) -> dict[str, Any]:
    return {
        "model": _ATTRIBUTION_MODEL,
        "lookback_days": int(lookback_days),
        "identity_priority": list(_ATTRIBUTION_IDENTITY_PRIORITY),
    }


def _floor_to_hour(value: datetime) -> datetime:
    value_utc = _ensure_utc(value)
    return value_utc.replace(minute=0, second=0, microsecond=0)


def _ceil_to_hour(value: datetime) -> datetime:
    value_utc = _ensure_utc(value)
    floored = _floor_to_hour(value_utc)
    if value_utc == floored:
        return floored
    return floored + timedelta(hours=1)


def _load_observability_snapshot(connection: sqlite3.Connection, query: CtaMetricsQuery) -> dict[str, Any]:
    start_hour = _floor_to_hour(query.start_at)
    end_hour = _ceil_to_hour(query.end_at)
    duration_seconds = max(0.0, (end_hour - start_hour).total_seconds())
    expected_slots = int(duration_seconds // 3600) if duration_seconds > 0 else 0

    active_slots_row = connection.execute(
        """
        SELECT COUNT(DISTINCT event_hour) AS active_slots
        FROM cta_metrics_hourly
        WHERE event_hour >= ? AND event_hour < ?
        """,
        (start_hour.isoformat(), end_hour.isoformat()),
    ).fetchone()
    active_slots = int(active_slots_row["active_slots"] or 0) if active_slots_row else 0
    missing_slots = max(0, expected_slots - active_slots)

    quality_row = connection.execute(
        """
        SELECT
            COALESCE(SUM(total_events), 0) AS total_events,
            COALESCE(SUM(invalid_events), 0) AS invalid_events,
            COALESCE(SUM(duplicate_events), 0) AS duplicate_events
        FROM cta_ingestion_quality_hourly
        WHERE event_hour >= ? AND event_hour < ?
        """,
        (start_hour.isoformat(), end_hour.isoformat()),
    ).fetchone()
    total_events = int(quality_row["total_events"] or 0) if quality_row else 0
    invalid_events = int(quality_row["invalid_events"] or 0) if quality_row else 0
    duplicate_events = int(quality_row["duplicate_events"] or 0) if quality_row else 0

    last_accepted_row = connection.execute(
        """
        SELECT event_id, received_at, cta_id, location_norm
        FROM cta_events_fact
        ORDER BY received_at DESC
        LIMIT 1
        """
    ).fetchone()
    last_accepted_event: dict[str, Any] | None = None
    if last_accepted_row:
        received_at_value = (
            str(last_accepted_row["received_at"])
            if last_accepted_row["received_at"] is not None
            else None
        )
        last_accepted_event = {
            "event_id": str(last_accepted_row["event_id"] or ""),
            "received_at": received_at_value,
            "cta_id": str(last_accepted_row["cta_id"] or "unknown"),
            "location": str(last_accepted_row["location_norm"] or "unknown"),
        }

    last_aggregated_row = connection.execute(
        """
        SELECT event_hour, event_date
        FROM cta_metrics_hourly
        ORDER BY event_hour DESC
        LIMIT 1
        """
    ).fetchone()
    last_aggregated_slot: dict[str, Any] | None = None
    aggregation_lag_seconds: int | None = None
    if last_aggregated_row:
        last_hour_raw = str(last_aggregated_row["event_hour"] or "")
        last_aggregated_slot = {
            "event_hour": last_hour_raw or None,
            "event_date": str(last_aggregated_row["event_date"] or "") or None,
        }
    # Measure lag from last accepted event, not last hourly slot.
    # Using the slot time overstates lag because any quiet hour looks like pipeline delay.
    if last_accepted_row and last_accepted_row["received_at"] is not None:
        parsed_received = _parse_datetime(str(last_accepted_row["received_at"]))
        if parsed_received is not None:
            aggregation_lag_seconds = max(
                0,
                int((datetime.now(timezone.utc) - parsed_received).total_seconds()),
            )

    return {
        "observability": {
            "expected_slots": expected_slots,
            "active_slots": active_slots,
            "missing_slots": missing_slots,
            "missing_ratio": _safe_ratio(missing_slots, expected_slots),
            "total_events": total_events,
            "invalid_events": invalid_events,
            "duplicate_events": duplicate_events,
            "invalid_ratio": _safe_ratio(invalid_events, total_events),
            "aggregation_lag_seconds": aggregation_lag_seconds,
        },
        "service_state": {
            "last_accepted_event": last_accepted_event,
            "last_aggregated_slot": last_aggregated_slot,
        },
    }


__all__ = [
    "CtaMetricsQuery",
    "CtaMetricsService",
    "DEFAULT_CONVERSION_LOOKBACK_DAYS",
]
