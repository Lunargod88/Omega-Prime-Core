-- ============================================
-- PHASE 6 — EXIT REASON + EXIT QUALITY
-- ============================================

DO $$ BEGIN
    CREATE TYPE exit_reason_enum AS ENUM (
        'CRYPTO_TIMEOUT',
        'DISTRIBUTION',
        'MOMENTUM_FADE',
        'REGIME_SHIFT',
        'HTF_CONFLICT',
        'TIME_DECAY',
        'HUMAN_EXIT',
        'NONE'
    );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE exit_quality_enum AS ENUM (
        'EARLY',
        'GOOD',
        'LATE'
    );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

ALTER TABLE decision_ledger
ADD COLUMN IF NOT EXISTS exit_reason exit_reason_enum DEFAULT 'NONE',
ADD COLUMN IF NOT EXISTS exit_quality exit_quality_enum;
