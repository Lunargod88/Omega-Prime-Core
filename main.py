from ai.analyzer import analyze_ledger
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from execution.adapter import resolve_execution_mode, session_allowed
from execution.tradestation import submit_paper_order

app = FastAPI(title="Ω PRIME Core")

DATABASE_URL = os.getenv("DATABASE_URL")

# --------------------
# DATABASE
# --------------------
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# --------------------
# MODELS
# --------------------
class OmegaPayload(BaseModel):
    price: float | None = None
    chiTier: str | None = None
    omegaConf: int | None = None
    memNet: int | None = None
    whaleIntentScore: int | None = None
    expectedRRLow: float | None = None
    expectedRRHigh: float | None = None
    rrStopPrice: float | None = None
    execRegime: str | None = None
    execStance: str | None = None
    session: str | None = None

class DecisionIn(BaseModel):
    symbol: str
    timeframe: str

    decision: str
    stance: str  # ENTER / HOLD / STAND_DOWN / DENIED

    confidence: int
    tier: str

    reason_codes: list[str] | None = None
    reasons_text: list[str] | None = None

    regime: str | None = None
    session: str | None = None
    tf_htf: str | None = None
    tf_ltf: str | None = None

    payload: OmegaPayload

# --------------------
# HEALTH
# --------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# --------------------
# INIT LEDGER
# --------------------
@app.post("/ledger/init")
def init_ledger():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS decision_ledger (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,

            decision TEXT NOT NULL,
            stance TEXT NOT NULL,

            confidence INTEGER NOT NULL,
            tier TEXT NOT NULL,

            reason_codes TEXT[],
            reasons_text TEXT[],

            regime TEXT,
            session TEXT,
            tf_htf TEXT,
            tf_ltf TEXT,

            payload JSONB NOT NULL
        );
    """)

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "decision_ledger initialized"}

# --------------------
# RECORD DECISION
# --------------------
@app.post("/ledger/decision")
def record_decision(d: DecisionIn):

    # Validation
    if d.decision not in {"BUY", "SELL", "EXIT", "HOLD", "ENTER LONG", "ENTER SHORT"}:
        raise HTTPException(status_code=400, detail="Invalid decision")

    if d.stance not in {"ENTER", "HOLD", "STAND_DOWN", "DENIED"}:
        raise HTTPException(status_code=400, detail="Invalid stance")

    if not (0 <= d.confidence <= 100):
        raise HTTPException(status_code=400, detail="Confidence out of range")

    # Governor
    if d.confidence < 70:
        raise HTTPException(status_code=403, detail="Denied: confidence gate")

    if d.tier in {"Ø", "S-", "C", "D"}:
        raise HTTPException(status_code=403, detail="Denied: tier gate")

    if d.session and d.session not in {"RTH", "ETH"}:
        raise HTTPException(status_code=403, detail="Denied: session gate")

    exec_mode = resolve_execution_mode(d.payload.dict())

    if not session_allowed(d.session):
        raise HTTPException(status_code=403, detail="Denied: execution session")

    if exec_mode == "PAPER" and d.stance == "ENTER":
        submit_paper_order(d.dict())

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO decision_ledger (
            symbol,
            timeframe,
            decision,
            stance,
            confidence,
            tier,
            reason_codes,
            reasons_text,
            regime,
            session,
            tf_htf,
            tf_ltf,
            payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at;
    """, (
        d.symbol,
        d.timeframe,
        d.decision,
        d.stance,
        d.confidence,
        d.tier,
        d.reason_codes,
        d.reasons_text,
        d.regime,
        d.session,
        d.tf_htf,
        d.tf_ltf,
        Json(d.payload.dict())
    ))

    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "recorded",
        "id": row["id"],
        "timestamp": row["created_at"].isoformat()
    }

# --------------------
# READ DECISIONS (LIST)
# --------------------
@app.get("/ledger/decisions")
def get_decisions(limit: int = 50):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM decision_ledger
        ORDER BY created_at DESC
        LIMIT %s;
    """, (limit,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return {"count": len(rows), "decisions": rows}

# --------------------
# STEP 16A-3 / 16A-4 — DECISION REPLAY (FORENSIC)
# --------------------
@app.get("/ledger/decision/{decision_id}")
def replay_decision(decision_id: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM decision_ledger
        WHERE id = %s;
    """, (decision_id,))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Decision not found")

    return row
# --------------------
# STEP 17 — AI READ-ONLY ANALYSIS
# --------------------
@app.get("/ai/insights")
def ai_insights():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM decision_ledger
        ORDER BY created_at ASC;
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return analyze_ledger(rows)
