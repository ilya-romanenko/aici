-- Normalize crypto billing enums to uppercase to match ORM values.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'billing_crypto_chain') THEN
        IF EXISTS (
            SELECT 1 FROM pg_enum e
            JOIN pg_type t ON e.enumtypid = t.oid
            WHERE t.typname = 'billing_crypto_chain' AND e.enumlabel = 'trc20'
        ) THEN
            ALTER TYPE billing_crypto_chain RENAME VALUE 'trc20' TO 'TRC20';
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_enum e
            JOIN pg_type t ON e.enumtypid = t.oid
            WHERE t.typname = 'billing_crypto_chain' AND e.enumlabel = 'bsc'
        ) THEN
            ALTER TYPE billing_crypto_chain RENAME VALUE 'bsc' TO 'BSC';
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_enum e
            JOIN pg_type t ON e.enumtypid = t.oid
            WHERE t.typname = 'billing_crypto_chain' AND e.enumlabel = 'polygon'
        ) THEN
            ALTER TYPE billing_crypto_chain RENAME VALUE 'polygon' TO 'POLYGON';
        END IF;
    END IF;
END$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'billing_crypto_payment_status') THEN
        IF EXISTS (
            SELECT 1 FROM pg_enum e
            JOIN pg_type t ON e.enumtypid = t.oid
            WHERE t.typname = 'billing_crypto_payment_status' AND e.enumlabel = 'pending'
        ) THEN
            ALTER TYPE billing_crypto_payment_status RENAME VALUE 'pending' TO 'PENDING';
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_enum e
            JOIN pg_type t ON e.enumtypid = t.oid
            WHERE t.typname = 'billing_crypto_payment_status' AND e.enumlabel = 'processing'
        ) THEN
            ALTER TYPE billing_crypto_payment_status RENAME VALUE 'processing' TO 'PROCESSING';
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_enum e
            JOIN pg_type t ON e.enumtypid = t.oid
            WHERE t.typname = 'billing_crypto_payment_status' AND e.enumlabel = 'confirmed'
        ) THEN
            ALTER TYPE billing_crypto_payment_status RENAME VALUE 'confirmed' TO 'CONFIRMED';
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_enum e
            JOIN pg_type t ON e.enumtypid = t.oid
            WHERE t.typname = 'billing_crypto_payment_status' AND e.enumlabel = 'failed'
        ) THEN
            ALTER TYPE billing_crypto_payment_status RENAME VALUE 'failed' TO 'FAILED';
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_enum e
            JOIN pg_type t ON e.enumtypid = t.oid
            WHERE t.typname = 'billing_crypto_payment_status' AND e.enumlabel = 'expired'
        ) THEN
            ALTER TYPE billing_crypto_payment_status RENAME VALUE 'expired' TO 'EXPIRED';
        END IF;
    END IF;
END$$;

ALTER TABLE IF EXISTS billing_crypto_payments ALTER COLUMN status SET DEFAULT 'PENDING';
