-- Track user and auto index runs for latest allocation selection and auditing.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'index_run_source') THEN
        CREATE TYPE index_run_source AS ENUM ('user', 'auto');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS index_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id VARCHAR(80) NOT NULL,
    source index_run_source NOT NULL,
    account_id UUID NULL REFERENCES auth_accounts(id) ON DELETE SET NULL,
    api_key_id UUID NULL REFERENCES api_keys(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_index_runs_run_id UNIQUE (run_id),
    CONSTRAINT ck_index_runs_run_id_length CHECK (char_length(run_id) BETWEEN 3 AND 80)
);

CREATE INDEX IF NOT EXISTS ix_index_runs_account_source_created ON index_runs (account_id, source, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_index_runs_source_created ON index_runs (source, created_at DESC);
