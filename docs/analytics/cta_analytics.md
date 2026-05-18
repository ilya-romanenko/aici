# CTA Analytics (Canonical)

Last updated: 2026-02-28.

## Scope

This document is the single source of truth for CTA analytics:
- frontend event tracking contract;
- backend ingestion and normalization;
- storage layers and retention;
- admin dashboard metrics and filters;
- weekly CTA format optimization decisions.

Historical iteration docs were moved to:
- `docs/archive/analytics/cta_analytics_contract.md`
- `docs/archive/analytics/cta_analytics_product_analytics.md`
- `docs/archive/analytics/cta_analytics_final_contour.md`

## 1. Event Contract

Endpoint:
- `POST /api/v1/events/cta`

Target events (`event_type`):
- `cta_click`
- `signup_started`
- `email_confirmed`
- `paid`

Required event fields (for all target events):
- `event_type`
- `cta_id`
- `page_path`
- `placement`
- `cta_format`
- `utm_source`
- `utm_medium`
- `utm_campaign`
- `utm_term`
- `utm_content`
- `timestamp` (UTC, ISO 8601)
- `actor_id`

Optional request fields:
- `href`
- `metadata` (object)
- `account_id`
- `session_id`
- `fingerprint`

Server-enriched fields:
- `request_id`
- `referer`
- `user_agent`
- `received_at` (UTC)

Normalization notes:
- `event_type` is validated against target events list.
- `page_path`, `placement`, `cta_format`, `utm_*` are trimmed and lowercased.
- empty `page_path` is normalized to `/`, empty `utm_source` is normalized to empty value.
- If `timestamp` is missing from client payload, server writes ingestion time in UTC.
- `actor_id` is computed by identity attribution rules below when not provided explicitly.

Response:
- `event_id`
- `received_at`

## 2. Core Dictionary

Primary normalized placements:
- `header`
- `hero`
- `pricing`
- `api_section`
- `docs`

Identity attribution (single canonical rule):
- priority: `account_id` -> `session_id` -> `fingerprint`
- canonical `actor_id` is formed from first non-empty identifier:
  - `acc:<account_id>`
  - `sess:<session_id>`
  - `fp:<fingerprint>`
- if all identifiers are missing, ingestion generates fallback fingerprint and sets `actor_id=fp:<generated_fingerprint>`
- once `account_id` appears for a user, attribution for funnel metrics is upgraded to `account_id` for the 7-day lookback window

Dedup policy:
- key based on actor + event_type + CTA attributes + short time window
- ingestion keeps raw backup and normalized analytics payload

## 3. KPI and Funnel

Core KPIs:
- `total_clicks`
- `unique_clicks`
- `attribution_coverage`
- conversion chain `cta_click -> signup_started -> email_confirmed -> paid`
- rates:
  - `CTR = unique_clicks / total_clicks`
  - `signup CR = signup_users / click_users`
  - `confirm CR = confirmed_users / signup_users`
  - `paid CR = paid_users / confirmed_users`

Default attribution:
- last CTA click
- lookback window: 7 days (fixed)
- identity priority: `account_id -> session_id -> fingerprint`

Stage 3 funnel slices:
- `utm_source`
- `page_path`
- `cta_id`
- `cta_format`
- `placement` (`location_norm`)

## 4. Storage Model

Raw backup:
- `runs/_intake/cta_events.jsonl`
- `runs/_intake/cta_events_analytics.jsonl`

Analytics DB:
- `runs/_analytics/cta_analytics.db`

Main tables:
- `cta_events_fact`
- `cta_metrics_hourly`
- `cta_metrics_daily`
- `cta_event_metrics_hourly`
- `cta_event_metrics_daily`
- `cta_ingestion_quality_hourly`
- `cta_format_optimization_runs`
- `cta_format_status_current`
- `cta_format_status_log`

Archive:
- `runs/_intake/archive/cta_analytics/<YYYY-MM>/*.jsonl.gz`

## 5. Admin Analytics API

Endpoints:
- `GET /api/v1/admin/cta-analytics/dashboard/summary`
- `GET /api/v1/admin/cta-analytics/timeseries`
- `GET /api/v1/admin/cta-analytics/top-cta`
- `GET /api/v1/admin/cta-analytics/breakdown`
- `GET /api/v1/admin/cta-analytics/funnel`
- `GET /api/v1/admin/cta-analytics/format-decisions`
- `GET /api/v1/admin/cta-analytics/export`

Common filters:
- period (`start_at`, `end_at`, `lookback_days`)
- placement/page/cta dimensions
- traffic source and attribution context

Supported filter params (dashboard, timeseries, top-cta, breakdown, funnel, export):
- `start_at`, `end_at`, `lookback_days`
- `placement`
- `page`
- `cta_id`
- `cta_format`
- `utm_source`
- `auth_state`
- `referrer`
- `utm`

## 6. Weekly CTA Format Optimization

- Scheduler cadence: every 7 days.
- Ranking: top-3 `cta_format` by:
  - `signup CR` (desc),
  - `CTR` (desc),
  - tie-breakers: `unique_clicks`, `total_clicks`, `cta_format`.
- Status policy:
  - top-3 => `active`,
  - all other known formats => `paused`.
- Every run writes:
  - decision snapshot (window, ranking, top formats, reason),
  - status changes with previous/new status and metric context.

## 7. Operational Notes

- Do not introduce breaking changes in event semantics without explicit versioning.
- Additive changes are allowed (new optional fields, new derived metrics, new export datasets).
- When behavior changes, update this file and record migration notes in `docs/roadmap.md`.

## 8. End-to-End Validation and Release Status

Validation date:
- 2026-02-28

Confirmed flow:
- `cta_click -> signup_started -> email_confirmed -> paid`
- attribution model: `last_click`
- fixed lookback window: `7` days
- identity priority: `account_id -> session_id -> fingerprint`

Focused verification suite (all passed):
- `tests/test_cta_metrics_service.py::test_cta_metrics_service_dashboard`
- `tests/api/test_admin_cta_analytics_api.py::test_admin_cta_analytics_endpoints_support_filters_pagination_and_csv`
- `tests/frontend/test_smoke.py::test_cta_event_logging`
- `tests/frontend/test_smoke.py::test_cta_event_analytics_dedup_and_metadata_normalization`
- `tests/frontend/test_smoke.py::test_cta_event_analytics_unique_actor_counters`

Release status:
- CTA analytics functionality is fully ready for production use within the defined scope.
