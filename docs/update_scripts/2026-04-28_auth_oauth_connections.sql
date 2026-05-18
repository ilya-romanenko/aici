-- OAuth connections: store per-provider tokens linked to an account.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'auth_oauth_provider') THEN
        CREATE TYPE auth_oauth_provider AS ENUM ('GOOGLE', 'GITHUB');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS auth_oauth_connections (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id      UUID NOT NULL REFERENCES auth_accounts(id) ON DELETE CASCADE,
    provider        auth_oauth_provider NOT NULL,
    provider_user_id VARCHAR(255) NOT NULL,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NULL,
    expires_at      TIMESTAMPTZ NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_auth_oauth_connections_account_id UNIQUE (account_id, provider)
);

CREATE INDEX IF NOT EXISTS ix_auth_oauth_connections_provider_user
    ON auth_oauth_connections (provider, provider_user_id);
