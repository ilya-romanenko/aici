-- Crypto billing support (NOWPayments): add provider enum value, chain/status enums, and crypto payments table.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_enum e
        JOIN pg_type t ON e.enumtypid = t.oid
        WHERE t.typname = 'billing_provider' AND e.enumlabel = 'crypto'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_enum e
        JOIN pg_type t ON e.enumtypid = t.oid
        WHERE t.typname = 'billing_provider' AND e.enumlabel = 'CRYPTO'
    ) THEN
        ALTER TYPE billing_provider RENAME VALUE 'crypto' TO 'CRYPTO';
    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_enum e
        JOIN pg_type t ON e.enumtypid = t.oid
        WHERE t.typname = 'billing_provider' AND e.enumlabel = 'CRYPTO'
    ) THEN
        ALTER TYPE billing_provider ADD VALUE 'CRYPTO';
    END IF;
END$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_enum e
        JOIN pg_type t ON e.enumtypid = t.oid
        WHERE t.typname = 'billing_event_provider' AND e.enumlabel = 'crypto'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_enum e
        JOIN pg_type t ON e.enumtypid = t.oid
        WHERE t.typname = 'billing_event_provider' AND e.enumlabel = 'CRYPTO'
    ) THEN
        ALTER TYPE billing_event_provider RENAME VALUE 'crypto' TO 'CRYPTO';
    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_enum e
        JOIN pg_type t ON e.enumtypid = t.oid
        WHERE t.typname = 'billing_event_provider' AND e.enumlabel = 'CRYPTO'
    ) THEN
        ALTER TYPE billing_event_provider ADD VALUE 'CRYPTO';
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'billing_crypto_chain') THEN
        CREATE TYPE billing_crypto_chain AS ENUM ('trc20', 'bsc', 'polygon');
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'billing_crypto_payment_status') THEN
        CREATE TYPE billing_crypto_payment_status AS ENUM ('pending', 'processing', 'confirmed', 'failed', 'expired');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS billing_crypto_payments (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES auth_accounts(id) ON DELETE CASCADE,
    plan_code VARCHAR(64) NOT NULL,
    invoice_id VARCHAR(160) NOT NULL,
    tx_hash VARCHAR(200),
    chain billing_crypto_chain NOT NULL,
    expected_amount NUMERIC(20, 8) NOT NULL,
    paid_amount NUMERIC(20, 8) NOT NULL DEFAULT 0,
    confirmations INTEGER NOT NULL DEFAULT 0,
    status billing_crypto_payment_status NOT NULL DEFAULT 'pending',
    raw_payload JSONB,
    period_end_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_crypto_payment_invoice_id UNIQUE (invoice_id)
);

CREATE INDEX IF NOT EXISTS ix_crypto_payment_account_status ON billing_crypto_payments (account_id, status);
CREATE INDEX IF NOT EXISTS ix_crypto_payment_tx_hash ON billing_crypto_payments (tx_hash);
