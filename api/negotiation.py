from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor
from db import get_db

router = APIRouter(prefix="/negotiation", tags=["negotiation"])


class NegotiationAction(BaseModel):
    action: str  # CONFIRM / REJECT / HOLD
    reason: Optional[str] = None


@router.get("/status")
def get_negotiation_status():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT 
            n.decision_id,
            n.status,
            n.analysis,
            n.created_at,
            d.symbol,
            d.stance
        FROM decision_negotiation n
        JOIN decision_ledger d ON d.id = n.decision_id
        ORDER BY n.created_at DESC
        LIMIT 1;
    """)

    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        return {"latest_decision": None, "analysis": None}

    cur.close()
    conn.close()

    return {
        "decision_id": row["decision_id"],
        "status": row["status"],
        "analysis": row["analysis"],
        "created_at": row["created_at"],
        "symbol": row["symbol"],
        "stance": row["stance"],
    }


@router.post("/confirm/{decision_id}")
def confirm_decision(decision_id: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE decision_negotiation
        SET human_action = 'CONFIRM',
            updated_at = now()
        WHERE decision_id = %s;
    """, (decision_id,))

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "confirmed"}


@router.post("/reject/{decision_id}")
def reject_decision(decision_id: int, payload: NegotiationAction):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE decision_negotiation
        SET human_action = 'REJECT',
            human_reason = %s,
            updated_at = now()
        WHERE decision_id = %s;
    """, (payload.reason, decision_id))

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "rejected"}
