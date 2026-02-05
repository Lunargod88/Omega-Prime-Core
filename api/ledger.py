from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from db import get_db

from models.enums import (
    StanceEnum,
    TierEnum,
    AuthorityEnum,
    ExitReasonEnum,
    RegimeEnum,
    ExitQualityEnum,
)

router = APIRouter(prefix="/ledger", tags=["ledger"])


class DecisionIngest(BaseModel):
    symbol: str
    timeframe: str

    stance: StanceEnum
    tier: TierEnum
    authority: AuthorityEnum = AuthorityEnum.NORMAL
    regime: RegimeEnum

    confidence: Optional[int] = None

    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    min_target: Optional[float] = None
    max_target: Optional[float] = None
    current_price: Optional[float] = None

    exit_reason: Optional[ExitReasonEnum] = ExitReasonEnum.NONE
    exit_quality: Optional[ExitQualityEnum] = None

    # ===== PHASE 5 — FORENSIC REPLAY =====
    memory_score: Optional[int] = None
    whale_band: Optional[str] = None
    hold_strength: Optional[int] = None
    continuation_efficiency: Optional[int] = None
    paid: Optional[bool] = False
    decision_timeline: Optional[dict] = None

    @validator("confidence")
    def validate_confidence(cls, v):
        if v is None:
            return v
        if not 0 <= v <= 100:
            raise ValueError("confidence must be between 0 and 100")
        return v


@router.post("/ingest")
async def ingest_decision(decision: DecisionIngest):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # ===============================
    # SYMBOL WHITELIST
    # ===============================
    cur.execute(
        "SELECT symbol, market_mode FROM symbol_whitelist WHERE symbol = %s;",
        (decision.symbol,)
    )
    symbol_row = cur.fetchone()

    if not symbol_row:
        cur.close()
        conn.close()
        raise HTTPException(status_code=403, detail="Symbol not allowed")

    # ===============================
    # PHASE 7 — REGIME GOVERNANCE
    # ===============================
    allowed_regimes = {r.value for r in RegimeEnum}

    if decision.regime.value not in allowed_regimes:
        cur.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid regime")

    # ===============================
    # PHASE 7 — REGIME MEMORY
    # ===============================
    cur.execute("""
        INSERT INTO regime_memory (symbol, timeframe, regime)
        VALUES (%s, %s, %s)
        ON CONFLICT (symbol, timeframe)
        DO UPDATE SET
            regime = EXCLUDED.regime,
            updated_at = NOW();
    """, (decision.symbol, decision.timeframe, decision.regime))

    # ===============================
    # PHASE 6 — EXIT GOVERNANCE
    # ===============================
    if decision.exit_quality and decision.exit_reason == ExitReasonEnum.NONE:
        cur.close()
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="exit_quality requires exit_reason"
        )

        # HUMAN_EXIT enum may not exist depending on enum version
    if hasattr(ExitReasonEnum, "HUMAN_EXIT"):
        if decision.exit_reason == ExitReasonEnum.HUMAN_EXIT and not decision.exit_quality:
            cur.close()
            conn.close()
            raise HTTPException(
                status_code=400,
                detail="HUMAN_EXIT requires exit_quality"
            )

    # ===============================
    # PHASE 7 — REGIME GOVERNANCE
    # ===============================
    if decision.regime == RegimeEnum.COMPRESSION:
        if decision.stance in (
            StanceEnum.ENTER_LONG,
            StanceEnum.ENTER_SHORT,
        ):
            cur.close()
            conn.close()
            raise HTTPException(
                status_code=400,
                detail="Cannot ENTER trades during COMPRESSION regime"
            )

    # ===============================
    # INSERT DECISION LEDGER
    # ===============================
    cur.execute("""
        INSERT INTO decision_ledger (
            symbol, timeframe, stance, tier, authority, regime,
            confidence, entry_price, stop_price,
            min_target, max_target, current_price,
            exit_reason, exit_quality,
            memory_score, whale_band, hold_strength,
            continuation_efficiency, paid, decision_timeline
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id;
    """, (
        decision.symbol,
        decision.timeframe,
        decision.stance,
        decision.tier,
        decision.authority,
        decision.regime,
        decision.confidence,
        decision.entry_price,
        decision.stop_price,
        decision.min_target,
        decision.max_target,
        decision.current_price,
        decision.exit_reason,
        decision.exit_quality,
        decision.memory_score,
        decision.whale_band,
        decision.hold_strength,
        decision.continuation_efficiency,
        decision.paid,
        decision.decision_timeline,
    ))

    ledger_row = cur.fetchone()
    decision_id = ledger_row["id"]

    # ===============================
    # INSERT NEGOTIATION ROW
    # ===============================
    cur.execute("""
        INSERT INTO decision_negotiation (decision_id, status, analysis, created_at)
        VALUES (%s, 'PENDING', %s, NOW())
        ON CONFLICT DO NOTHING;
    """, (decision_id, None))

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "ok",
        "decision_id": decision_id,
        "decision": decision.dict()
    }
