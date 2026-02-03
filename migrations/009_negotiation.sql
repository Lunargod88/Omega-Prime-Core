CREATE TABLE IF NOT EXISTS decision_negotiation (
    id SERIAL PRIMARY KEY,
    decision_id UUID NOT NULL REFERENCES decision_ledger(id) ON DELETE CASCADE,

    system_action TEXT NOT NULL, -- ENTER_LONG, HOLD, etc
    human_action TEXT,           -- CONFIRM / REJECT / HOLD
    human_reason TEXT,

    auto_confirm BOOLEAN DEFAULT false,

    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_negotiation_decision
ON decision_negotiation(decision_id);
