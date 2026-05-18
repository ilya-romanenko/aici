-- Step 2: CTA ingestion/storage expansion for event_type + cta_format and event aggregates.
-- Target DB: PostgreSQL (aici_auth, auth-db service).

ALTER TABLE cta_events_fact
    ADD COLUMN IF NOT EXISTS event_type TEXT NOT NULL DEFAULT 'cta_click';

ALTER TABLE cta_events_fact
    ADD COLUMN IF NOT EXISTS cta_format TEXT NOT NULL DEFAULT 'unknown';

CREATE INDEX IF NOT EXISTS ix_cta_events_fact_event_type_date
    ON cta_events_fact(event_type, event_date);

CREATE INDEX IF NOT EXISTS ix_cta_events_fact_format_date
    ON cta_events_fact(cta_format, event_date);

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
