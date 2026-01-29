-- ============================================
-- PHASE 5 — FORENSIC REPLAY ENRICHMENT
-- ============================================

ALTER TABLE decision_ledger
ADD COLUMN IF NOT EXISTS memory_score INTEGER,
ADD COLUMN IF NOT EXISTS whale_band TEXT,
ADD COLUMN IF NOT EXISTS hold_strength INTEGER,
ADD COLUMN IF NOT EXISTS continuation_efficiency INTEGER,
ADD COLUMN IF NOT EXISTS paid BOOLEAN DEFAULT false,
ADD COLUMN IF NOT EXISTS decision_timeline JSONB;
