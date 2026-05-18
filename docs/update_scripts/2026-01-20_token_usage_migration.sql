-- Migrate historical request-based usage to token accounting (1 token per call, 2 tokens for pipeline triggers).
-- Idempotent: safe to run multiple times.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

WITH event_tokens AS (
    SELECT
        id,
        api_key_id,
        created_at::date AS usage_date,
        date_trunc('month', created_at)::date AS period_start,
        CASE
            WHEN request_cost = 0 THEN 0
            WHEN lower(method) = 'post'
                AND (
                    lower(COALESCE(route_name, '')) IN ('api_trigger_run', 'api_trigger_run_async')
                    OR TRIM(TRAILING '/' FROM lower(COALESCE(route_path, ''))) IN ('/api/v1/run', '/api/v1/run/async')
                )
            THEN GREATEST(COALESCE(request_cost, 2), 2)
            ELSE GREATEST(COALESCE(request_cost, 1), 1)
        END AS tokens
    FROM api_usage_events
),
updated_events AS (
    UPDATE api_usage_events AS e
    SET request_cost = et.tokens,
        updated_at = NOW()
    FROM event_tokens AS et
    WHERE e.id = et.id
      AND e.request_cost IS DISTINCT FROM et.tokens
    RETURNING 1
),
daily_upsert AS (
    INSERT INTO api_key_usage_daily (id, api_key_id, usage_date, call_count, compute_units, created_at, updated_at)
    SELECT uuid_generate_v4(), api_key_id, usage_date, tokens, tokens, NOW(), NOW()
    FROM (
        SELECT api_key_id, usage_date, SUM(tokens) AS tokens
        FROM event_tokens
        GROUP BY api_key_id, usage_date
    ) AS aggregated
    ON CONFLICT (api_key_id, usage_date) DO UPDATE
    SET call_count = EXCLUDED.call_count,
        compute_units = EXCLUDED.compute_units,
        updated_at = NOW()
    RETURNING 1
),
monthly_upsert AS (
    INSERT INTO api_key_usage_monthly (id, api_key_id, period_start, call_count, compute_units, created_at, updated_at)
    SELECT uuid_generate_v4(), api_key_id, period_start, tokens, tokens, NOW(), NOW()
    FROM (
        SELECT api_key_id, period_start, SUM(tokens) AS tokens
        FROM event_tokens
        GROUP BY api_key_id, period_start
    ) AS aggregated
    ON CONFLICT (api_key_id, period_start) DO UPDATE
    SET call_count = EXCLUDED.call_count,
        compute_units = EXCLUDED.compute_units,
        updated_at = NOW()
    RETURNING 1
),
daily_sync AS (
    UPDATE api_key_usage_daily
    SET compute_units = call_count,
        updated_at = NOW()
    WHERE compute_units IS DISTINCT FROM call_count
    RETURNING 1
),
monthly_sync AS (
    UPDATE api_key_usage_monthly
    SET compute_units = call_count,
        updated_at = NOW()
    WHERE compute_units IS DISTINCT FROM call_count
    RETURNING 1
)
SELECT
    (SELECT COUNT(*) FROM updated_events) AS updated_usage_events,
    (SELECT COUNT(*) FROM daily_upsert) AS upserted_daily_rows,
    (SELECT COUNT(*) FROM monthly_upsert) AS upserted_monthly_rows,
    (SELECT COUNT(*) FROM daily_sync) AS synced_daily_rows,
    (SELECT COUNT(*) FROM monthly_sync) AS synced_monthly_rows;
