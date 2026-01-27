from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional, Literal, Any, Dict
import os, json
import psycopg2
from psycopg2.extras import RealDictCursor

router = APIRouter(prefix="/api", tags=["webhook"])

# -----------------------------
# PHASE 2 Canonical enums (DB law)
# -----------------------------
Account = Literal["JAYLYN", "WIFE"]

Stance = Literal[
    "ENTER_LONG",
    "ENTER_SHORT",
    "HOLD_LONG",
    "HOLD_SHORT",
    "HOLD_LONG_PAID",
    "HOLD_SHORT_PAID",
    "STAND_DOWN",
    "WAIT",
]

Tier = Literal["S+++", "S++", "S+", "S", "A", "B", "C", "Ø"]
Authority = Literal["PRIME", "NORMAL"]

# NOTE: Regime/ExitReason/ExitQuality are later phases in the roadmap,
# but the DB already may have them. Keep them optional here so we don't drift.
Regime = Optional[Literal["COMPRESSION", "EXPANSION", "NEUTRAL"]]

class TradingViewAlert(BaseModel):
    account: Account
    symbol: str
    timeframe: Optional[str] = None

    stance: Stance
    tier: Tier
    authority: Authority
    confidence: Optional[float] = Field(default=None, ge=0, le=100)

    regime: Regime = None

    # PHASE 3 — PRICE CONTEXT
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    min_target: Optional[float] = None
    max_target: Optional[float] = None
    current_price: Optional[float] = None

    meta: Optional[Dict[str, Any]] = None



def _db_conn():
    # Railway usually provides DATABASE_URL
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(dsn)


@router.post("/webhook/tradingview")
async def tradingview_webhook(
    request: Request,
    x_omega_key: Optional[str] = Header(default=None),
):
    """
    PHASE 2: Ingest TradingView alerts with ACCOUNT + STANCE + TIER + AUTHORITY split.

    Security: optional shared secret via X-Omega-Key (recommended).
    """
    expected = os.getenv("OMEGA_WEBHOOK_KEY")
    if expected and x_omega_key != expected:
        raise HTTPException(status_code=401, detail="Invalid webhook key")

    body = await request.json()

    # Allow either direct dict or {"payload": {...}} styles
    payload = body.get("payload") if isinstance(body, dict) else None
    data = payload if isinstance(payload, dict) else body

    try:
        alert = TradingViewAlert.model_validate(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid alert payload: {e}")

    # Store minimal Phase 2 law in ledger.
    # IMPORTANT: adjust table/column names ONLY if your migration used different names.
   insert_sql = """
    INSERT INTO decision_ledger (
        account,
        symbol,
        timeframe,
        stance,
        tier,
        authority,
        confidence,
        regime,
        entry_price,
        stop_price,
        min_target,
        max_target,
        current_price,
        raw_payload
    )
    VALUES (
        %(account)s,
        %(symbol)s,
        %(timeframe)s,
        %(stance)s,
        %(tier)s,
        %(authority)s,
        %(confidence)s,
        %(regime)s,
        %(entry_price)s,
        %(stop_price)s,
        %(min_target)s,
        %(max_target)s,
        %(current_price)s,
        %(raw_payload)s::jsonb
    )
    RETURNING id, created_at
"""


params = {
    "account": alert.account,
    "symbol": alert.symbol,
    "timeframe": alert.timeframe,
    "stance": alert.stance,
    "tier": alert.tier,
    "authority": alert.authority,
    "confidence": alert.confidence,
    "regime": alert.regime,

    # PHASE 3 — PRICE CONTEXT
    "entry_price": alert.entry_price,
    "stop_price": alert.stop_price,
    "min_target": alert.min_target,
    "max_target": alert.max_target,
    "current_price": alert.current_price,

    "raw_payload": json.dumps(body),
}


    try:
        with _db_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(insert_sql, params)
                row = cur.fetchone()
                conn.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB insert failed: {e}")

    return {"ok": True, "id": row["id"], "created_at": row["created_at"]}
