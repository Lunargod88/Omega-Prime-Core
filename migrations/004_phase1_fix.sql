-- ============================================
-- PHASE 1 FIX — ALIGN DB TO PINE
-- ============================================

-- 1. Drop bad stance enum
DO $$ BEGIN
    DROP TYPE IF EXISTS exec_stance_enum;
EXCEPTION WHEN dependent_objects_still_exist THEN null;
END $$;

-- 2. Create correct stance enum
CREATE TYPE stance_enum AS ENUM (
  'ENTER_LONG',
  'ENTER_SHORT',
  'HOLD_LONG',
  'HOLD_SHORT',
  'HOLD_LONG_PAID',
  'HOLD_SHORT_PAID',
  'STAND_DOWN',
  'WAIT'
);

-- 3. Authority enum
CREATE TYPE authority_enum AS ENUM (
  'NORMAL',
  'PRIME'
);

-- 4. Exit quality enum
CREATE TYPE exit_quality_enum AS ENUM (
  'EARLY',
  'GOOD',
  'LATE'
);

-- 5. Alter columns
ALTER TABLE decision_ledger
  ALTER COLUMN stance TYPE stance_enum USING replace(replace(stance,' ', '_'),'(','')::stance_enum,
  ADD COLUMN IF NOT EXISTS authority authority_enum DEFAULT 'NORMAL',
  ADD COLUMN IF NOT EXISTS exit_quality exit_quality_enum,
  ADD COLUMN IF NOT EXISTS timeframe TEXT;