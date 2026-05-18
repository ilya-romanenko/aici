-- Ensure index_run_source values use uppercase labels to match SQLAlchemy Enum names.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_enum enum_vals
        JOIN pg_type enum_type ON enum_type.oid = enum_vals.enumtypid
        WHERE enum_type.typname = 'index_run_source' AND enum_vals.enumlabel = 'user'
    ) THEN
        EXECUTE 'ALTER TYPE index_run_source RENAME VALUE ''user'' TO ''USER''';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_enum enum_vals
        JOIN pg_type enum_type ON enum_type.oid = enum_vals.enumtypid
        WHERE enum_type.typname = 'index_run_source' AND enum_vals.enumlabel = 'auto'
    ) THEN
        EXECUTE 'ALTER TYPE index_run_source RENAME VALUE ''auto'' TO ''AUTO''';
    END IF;
END$$;
