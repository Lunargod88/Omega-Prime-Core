-- ============================================
-- PHASE 2 — ACCOUNT & ENUM ALIGNMENT
-- ============================================

-- 1. Account column (multi-cockpit)
ALTER TABLE decision_ledger
ADD COLUMN IF NOT EXISTS account TEXT NOT NULL DEFAULT 'JAYLYN';

CREATE INDEX IF NOT EXISTS idx_decision_account ON decision_ledger(account);

-- 2. Execution stance (from Pine)
DO $$ BEGIN
    CREATE TYPE exec_stance_enum AS ENUM (
        'WAIT',
        'ENTER LONG',
        'ENTER SHORT',
        'HOLD LONG',
        'HOLD SHORT',
        'HOLD LONG (PAID)',
        'HOLD SHORT (PAID)',
        'STAND DOWN'
    );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

-- 3. Tier enum
DO $$ BEGIN
    CREATE TYPE tier_enum AS ENUM (
        'S+++','S++','S+','S','A','B','C','Ø'
    );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

-- 4. Regime enum
DO $$ BEGIN
    CREATE TYPE regime_enum AS ENUM (
        'COMPRESSION','EXPANSION','NEUTRAL'
    );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

-- 5. Exit reason enum
DO $$ BEGIN
    CREATE TYPE exit_reason_enum AS ENUM (
        'CRYPTO_TIMEOUT',
        'DISTRIBUTION',
        'MOMENTUM_FADE',
        'REGIME_SHIFT',
        'HTF_CONFLICT',
        'TIME_DECAY',
        'NONE'
    );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

-- 6. Convert columns (if they exist as TEXT)
ALTER TABLE decision_ledger
ALTER COLUMN stance TYPE exec_stance_enum USING stance::exec_stance_enum;

ALTER TABLE decision_ledger
ALTER COLUMN tier TYPE tier_enum USING tier::tier_enum;

ALTER TABLE decision_ledger
ALTER COLUMN regime TYPE regime_enum USING regime::regime_enum;

ALTER TABLE decision_ledger
ALTER COLUMN exit_reason TYPE exit_reason_enum USING exit_reason::exit_reason_enum;
