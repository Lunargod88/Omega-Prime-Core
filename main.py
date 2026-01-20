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
    session: str | None = None
    chiTier: str | None = None
    omegaConf: int | None = None
    memNet: int | None = None
    whaleIntentScore: int | None = None
    expectedRRLow: float | None = None
    expectedRRHigh: float | None = None
    rrStopPrice: float | None = None
    execRegime: str | None = None
    execStance: str | None = None


class DecisionIn(BaseModel):
    symbol: str
    timeframe: str
    decision: str
    confidence: int
    tier: str
    reason: str | None = None
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
            confidence INTEGER NOT NULL,
            tier TEXT NOT NULL,
            reason TEXT,
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

    # --------------------
    # STEP 12D — VALIDATION (CORRECT LOCATION)
    # --------------------
    if d.decision not in {"BUY", "SELL", "EXIT", "HOLD", "ENTER LONG", "ENTER SHORT"}:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision value: {d.decision}"
        )

    if d.confidence < 0 or d.confidence > 100:
        raise HTTPException(
            status_code=400,
            detail="Confidence must be between 0 and 100"
        )
    # --------------------
    # STEP 13 — DECISION AUTHORITY (GOVERNOR)
    # --------------------

    # Minimum confidence gate
    MIN_CONFIDENCE = 70

    if d.confidence < MIN_CONFIDENCE:
        raise HTTPException(
            status_code=403,
            detail=f"Decision denied by Governor: confidence {d.confidence} < {MIN_CONFIDENCE}"
        )

    # Tier gate (example: disallow low-grade tiers)
    DISALLOWED_TIERS = {"Ø", "S-", "C", "D"}

    if d.tier in DISALLOWED_TIERS:
        raise HTTPException(
            status_code=403,
            detail=f"Decision denied by Governor: tier {d.tier} not permitted"
        )

    # Session safety gate (optional but on by default)
    if d.payload.session is not None and d.payload.session not in {"RTH", "ETH"}:
        raise HTTPException(
            status_code=403,
            detail=f"Decision denied by Governor: session {d.payload.session} not allowed"
        )
    # --------------------
    # STEP 14 — EXECUTION ADAPTER
    # --------------------
    exec_mode = resolve_execution_mode(d.payload.dict())

    if not session_allowed(d.payload.session):
        raise HTTPException(
            status_code=403,
            detail=f"Decision denied by Governor: session {d.payload.session} not allowed"
        )

    if exec_mode == "PAPER":
        execution_result = submit_paper_order(d.dict())
    else:
        execution_result = {"status": "logged_only"}

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO decision_ledger
        (symbol, timeframe, decision, confidence, tier, reason, payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at;
        """,
        (
            d.symbol,
            d.timeframe,
            d.decision,
            d.confidence,
            d.tier,
            d.reason,
            Json(d.payload.dict())
        )
    )

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
# READ DECISIONS
# --------------------
@app.get("/ledger/decisions")
def get_decisions(limit: int = 50):
# --------------------
# STEP 16A-3 — DECISION REPLAY (SINGLE ID)
# --------------------
@app.get("/ledger/decision/{decision_id}")
def replay_decision(decision_id: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            id,
            created_at,
            symbol,
            timeframe,
            decision,
            confidence,
            tier,
            reason,
            payload
        FROM decision_ledger
        WHERE id = %s;
        """,
        (decision_id,)
    )

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Decision {decision_id} not found"
        )

    return {
        "decision_id": row["id"],
        "timestamp": row["created_at"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "decision": row["decision"],
        "confidence": row["confidence"],
        "tier": row["tier"],
        "reason": row["reason"],
        "payload": row["payload"]
    }
    
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            id,
            created_at,
            symbol,
            timeframe,
            decision,
            confidence,
            tier,
            reason,
            payload
        FROM decision_ledger
        ORDER BY created_at DESC
        LIMIT %s;
        """,
        (limit,)
    )

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return {
        "count": len(rows),
        "decisions": rows
    }
