from fastapi import APIRouter
from typing import Optional
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor
from db import get_db

router = APIRouter(prefix="/negotiation", tags=["negotiation"])


class NegotiationAction(BaseModel):
    action: str
    reason: Optional[str] = None


@router.get("/status")
def get_negotiation_status():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT *
        FROM decision_negotiation
        ORDER BY created_at DESC
        LIMIT 1;
    """)

    row = cur.fetchone()

    cur.close()
    conn.close()

    if row is None:
        return {"latest_decision": None, "analysis": None}

    return row


@router.post("/confirm/{decision_id}")
def confirm_decision(decision_id: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE decision_negotiation
        SET status = 'CONFIRM'
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
        SET status = 'REJECT',
            analysis = %s
        WHERE decision_id = %s;
    """, (payload.reason, decision_id))

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "rejected"}
