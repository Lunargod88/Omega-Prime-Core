CREATE TABLE IF NOT EXISTS regime_memory (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    regime TEXT NOT NULL CHECK (regime IN ('COMPRESSION','EXPANSION','NEUTRAL')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe)
);
